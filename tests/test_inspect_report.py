"""Offline coverage for the Inspect comparison report.

The summary-row source and the Inspect header dump are both mocked, so these tests need neither
`.eval` logs nor the isolated `uvx` tool environment.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
import typing
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "inspect_report.py"
_SPEC = importlib.util.spec_from_file_location("inspect_report", SCRIPT_PATH)
inspect_report = importlib.util.module_from_spec(_SPEC)
sys.modules["inspect_report"] = inspect_report
_SPEC.loader.exec_module(inspect_report)


def sample_row(task, model, sample_id, score, seconds, **overrides):
    row = {
        "log": "x.eval",
        "task": task,
        "model": model,
        "reasoning_effort": "high",
        "sample_id": sample_id,
        "epoch": 1,
        "score": score,
        "score_detail": "",
        "difficulty": "",
        "input_tokens": 100,
        "output_tokens": 1000,
        "reasoning_tokens": 200,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "stop_reason": "stop",
        "total_time_s": seconds,
        "working_time_s": seconds,
        "response_id": "",
        "served_model": "",
        "lyra_selected_model": "",
        "lyra_router_model": "",
        "lyra_difficulty": "",
        "lyra_task_type": "",
        "lyra_mode": "",
        "lyra_policy_version": "",
        "lyra_attempt_count": "",
        "error": "",
    }
    row.update(overrides)
    return row


def billing(model, minute, cost, duration_ms, settled=True):
    return {
        "id": f"{model}-{minute}",
        "time": f"2026-09-12T13:{minute:02d}:00Z",
        "request_path": "/v1/chat/completions",
        "model": model,
        "duration_ms": duration_ms,
        "cost_usd_exact": f"{cost:.8f}",
        "settled": settled,
        "billing_status": "settled" if settled else "pending",
        "ok": True,
        "error_code": None,
        "tokens": {"input": 100, "output": 1000},
    }


def run(
    task,
    model,
    n_planned=2,
    started="2026-09-12T13:00:00+00:00",
    completed="2026-09-12T13:02:00+00:00",
):
    return {
        "log": "x.eval",
        "task": task,
        "model": model,
        "effort": "high",
        "started": started,
        "completed": completed,
        "n_planned": n_planned,
        "wall_time_s": 120.0,
        "status": "success",
    }


# --------------------------------------------------------------------------- cost attribution


def test_cost_window_keeps_own_model_and_excludes_others():
    target = run("ifeval", "apigo/lyra-auto")
    entries = [
        billing("apigo/lyra-auto", 0, 0.01, 5000),
        billing("apigo/lyra-auto", 1, 0.02, 6000),
        # Sub-call of the routing model: a separate line item for a different model, excluded.
        billing("deepseek-v4-flash", 1, 0.99, 6000),
        # Same model but outside the window.
        billing("apigo/lyra-auto", 30, 0.50, 6000),
    ]
    matched = inspect_report.entries_for_run(target, entries)
    assert [entry["id"] for entry in matched] == ["apigo/lyra-auto-0", "apigo/lyra-auto-1"]
    total, billed = inspect_report.settled_cost(matched)
    assert total == pytest.approx(0.03)
    assert billed == 2


def test_cost_window_includes_the_tail_after_completion():
    target = run("ifeval", "gpt-6-astra", completed="2026-09-12T13:01:57+00:00")
    entries = [billing("gpt-6-astra", 2, 0.04, 1000)]  # 13:02:00, three seconds past completion
    assert inspect_report.entries_for_run(target, entries, tail_seconds=5)
    assert not inspect_report.entries_for_run(target, entries, tail_seconds=1)


def test_unsettled_entries_never_count_as_zero_cost():
    total, billed = inspect_report.settled_cost(
        [billing("gpt-6-astra", 0, 0.04, 1000, settled=False)]
    )
    assert total is None and billed == 0


def test_per_sample_cost_matches_by_duration_one_to_one():
    rows = [
        sample_row("ifeval", "openai-api/apigo/gpt-6-astra", "a", "1", 5.0),
        sample_row("ifeval", "openai-api/apigo/gpt-6-astra", "b", "0", 30.0),
    ]
    entries = [
        billing("gpt-6-astra", 0, 0.01, 5100),
        billing("gpt-6-astra", 1, 0.09, 29500),
    ]
    matched = inspect_report.match_sample_costs(rows, entries)
    assert matched["a"][0] == pytest.approx(0.01)
    assert matched["b"][0] == pytest.approx(0.09)


def test_per_sample_cost_left_empty_when_duration_is_far_off():
    rows = [sample_row("ifeval", "openai-api/apigo/gpt-6-astra", "a", "1", 5.0)]
    assert inspect_report.match_sample_costs(rows, [billing("gpt-6-astra", 0, 0.01, 90000)]) == {}


# --------------------------------------------------------------------------- statistics


def test_accuracy_keeps_errors_in_the_denominator():
    rows = [
        sample_row("ifeval", "openai-api/apigo/gpt-6-astra", "a", "1", 5.0),
        sample_row(
            "ifeval", "openai-api/apigo/gpt-6-astra", "b", "", 600.0, error="RetryError(...)"
        ),
    ]
    report, _ = inspect_report.build_rows(rows, [run("ifeval", "gpt-6-astra")], [], {})
    (entry,) = report
    assert entry["n_planned"] == 2
    assert entry["n_scored"] == 1
    assert entry["n_error"] == 1
    assert entry["accuracy"] == pytest.approx(0.5)
    # The 600s timeout sample is excluded from latency but not from accuracy.
    assert entry["latency_p95_s"] == pytest.approx(5.0)


def test_wilson_bounds_bracket_the_point_estimate():
    low, high = inspect_report.wilson_interval(7, 10)
    assert low == pytest.approx(0.3968, abs=1e-3)
    assert high == pytest.approx(0.8922, abs=1e-3)
    assert low < 0.7 < high
    assert inspect_report.wilson_interval(0, 10)[0] == 0.0
    assert inspect_report.wilson_interval(10, 10)[1] == pytest.approx(1.0)


def test_cost_per_correct_is_empty_when_nothing_was_correct():
    rows = [
        sample_row("ifeval", "openai-api/apigo/gpt-6-astra", "a", "0", 5.0),
        sample_row("ifeval", "openai-api/apigo/gpt-6-astra", "b", "0", 6.0),
    ]
    entries = [billing("gpt-6-astra", 0, 0.01, 5000), billing("gpt-6-astra", 1, 0.02, 6000)]
    report, _ = inspect_report.build_rows(rows, [run("ifeval", "gpt-6-astra")], entries, {})
    (entry,) = report
    assert entry["cost_usd_settled"] == pytest.approx(0.03)
    assert entry["cost_per_correct_usd"] == ""


def test_cost_columns_stay_empty_without_a_platform_export():
    rows = [sample_row("ifeval", "openai-api/apigo/gpt-6-astra", "a", "1", 5.0)]
    report, _ = inspect_report.build_rows(rows, [run("ifeval", "gpt-6-astra", n_planned=1)], [], {})
    (entry,) = report
    assert entry["cost_usd_settled"] == ""
    assert entry["cost_usd_per_sample"] == ""
    assert entry["cost_per_correct_usd"] == ""


def test_public_price_estimate_covers_fixed_models_only():
    prices = {"gpt-6-astra": {"input_usd_per_1m": 10, "output_usd_per_1m": 50}}
    rows = [sample_row("ifeval", "openai-api/apigo/gpt-6-astra", "a", "1", 5.0)]
    assert inspect_report.public_estimate("gpt-6-astra", rows, prices) == pytest.approx(
        100 * 10 / 1e6 + 1000 * 50 / 1e6
    )
    assert inspect_report.public_estimate("apigo/lyra-auto", rows, prices) is None


def test_routing_breakdown_counts_selected_models():
    rows = [
        sample_row(
            "ifeval",
            "openai-api/apigo/apigo/lyra-auto",
            "a",
            "1",
            5.0,
            lyra_selected_model="deepseek-v4-flash",
        ),
        sample_row(
            "ifeval",
            "openai-api/apigo/apigo/lyra-auto",
            "b",
            "1",
            5.0,
            lyra_selected_model="deepseek-v4-flash",
        ),
        sample_row("ifeval", "openai-api/apigo/apigo/lyra-budget", "a", "1", 5.0),
    ]
    report, _ = inspect_report.build_rows(
        rows,
        [run("ifeval", "apigo/lyra-auto"), run("ifeval", "apigo/lyra-budget", n_planned=1)],
        [],
        {},
    )
    by_model = {entry["model"]: entry for entry in report}
    assert json.loads(by_model["apigo/lyra-auto"]["routing_selected_models"]) == {
        "deepseek-v4-flash": 2
    }
    assert by_model["apigo/lyra-budget"]["routing_selected_models"] == ""
    assert by_model["apigo/lyra-auto"]["is_fusion"] == "true"


# --------------------------------------------------------------------------- end to end


def _tiny_dataset():
    rows = []
    runs = []
    entries = []
    minute = 0
    for task in ("ifeval", "gpqa_diamond", "livecodebench_v6"):
        for model, score in (
            ("gpt-6-astra", "1"),
            ("gpt-5.6-terra", "0"),
            ("apigo/lyra-auto", "1"),
        ):
            for index in range(2):
                rows.append(
                    sample_row(
                        task,
                        f"openai-api/apigo/{model}",
                        f"{task}-{index}",
                        score,
                        5.0 + index,
                        lyra_selected_model="deepseek-v4-flash" if "lyra" in model else "",
                    )
                )
                entries.append(billing(model, minute, 0.01 * (index + 1), 5000 + index * 1000))
                minute += 1
            runs.append(
                run(
                    task,
                    model,
                    started="2026-09-12T13:00:00+00:00",
                    completed="2026-09-12T13:59:00+00:00",
                )
            )
    return rows, runs, entries


def test_html_builder_runs_and_contains_every_benchmark_section():
    rows, runs, entries = _tiny_dataset()
    prices = {
        "gpt-6-astra": {"input_usd_per_1m": 10, "output_usd_per_1m": 50},
        "gpt-5.6-terra": {"input_usd_per_1m": 2, "output_usd_per_1m": 12},
    }
    report, detailed = inspect_report.build_rows(rows, runs, entries, prices)
    document = inspect_report.render_html(
        report,
        {
            "title": "标题",
            "run_set": "calibration",
            "generated": "2026-09-12",
            "samples_per_cell": 2,
            "n_cells": len(report),
        },
    )
    for label in inspect_report.BENCHMARK_LABELS.values():
        assert label in document
    assert document.startswith("<!doctype html>")
    assert "<svg" in document
    assert "report.csv" in document and "samples.csv" in document
    assert "观察到" in document and "显著" not in document
    assert "Fusion auto" in document
    assert len(detailed) == len(rows)


def test_main_writes_every_artifact(tmp_path, monkeypatch):
    rows, runs, entries = _tiny_dataset()
    logs = tmp_path / "logs"
    logs.mkdir()
    for index in range(len(runs)):
        (logs / f"run{index}.eval").write_text("", encoding="utf-8")
    platform = tmp_path / "platform.json"
    platform.write_text(json.dumps(entries), encoding="utf-8")
    prices = tmp_path / "prices.json"
    prices.write_text(
        json.dumps(
            {
                "data": {
                    "items": [
                        {
                            "slug": "gpt-6-astra",
                            "pricing_headline": {"input_usd_per_1m": 10, "output_usd_per_1m": 50},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"

    class FakeSummary:
        FIELDS: typing.ClassVar[list[str]] = list(rows[0])

        @staticmethod
        def rows_for(path):
            return []

    monkeypatch.setattr(inspect_report, "_load_inspect_summary", lambda: FakeSummary)
    monkeypatch.setattr(inspect_report, "collect_runs", lambda paths: runs[: len(list(paths))])
    monkeypatch.setattr(
        FakeSummary, "rows_for", staticmethod(lambda path: rows if path.name == "run0.eval" else [])
    )

    code = inspect_report.main(
        [
            "--logs",
            str(logs),
            "--platform",
            str(platform),
            "--prices",
            str(prices),
            "--out",
            str(out),
            "--run-set",
            "calibration",
        ]
    )
    assert code == 0
    for name in ("report.html", "report.csv", "report.json", "samples.csv", "runs.jsonl"):
        assert (out / name).exists()
    written = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert len(written) == len(runs)
    with (out / "report.csv").open(encoding="utf-8") as stream:
        first = next(iter(csv.DictReader(stream)))
        assert first.keys() >= {"benchmark", "accuracy", "wilson_low"}


def test_header_to_run_reads_the_inspect_header_shape():
    header = {
        "status": "success",
        "eval": {
            "task": "inspect_evals/gpqa_diamond",
            "model": "openai-api/apigo/apigo/lyra-auto",
            "model_generate_config": {"reasoning_effort": "high"},
            "dataset": {"samples": 198, "sample_ids": [1, 2, 3]},
        },
        "stats": {
            "started_at": "2026-09-12T13:15:34+00:00",
            "completed_at": "2026-09-12T13:17:02+00:00",
        },
    }
    parsed = inspect_report.header_to_run(header, "a.eval")
    assert parsed["task"] == "gpqa_diamond"
    assert parsed["model"] == "apigo/lyra-auto"
    assert parsed["n_planned"] == 3
    assert parsed["wall_time_s"] == pytest.approx(88.0)
