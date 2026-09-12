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
    baselines = inspect_report.compute_baselines(report, detailed)
    document = inspect_report.render_html(
        report,
        {
            "title": "标题",
            "run_set": "calibration",
            "generated": "2026-09-12",
            "samples_per_cell": 2,
            "n_cells": len(report),
        },
        baselines,
    )
    for label in inspect_report.BENCHMARK_LABELS.values():
        assert label in document
    assert document.startswith("<!doctype html>")
    assert "<svg" in document
    assert "report.csv" in document and "samples.csv" in document
    assert "观察到" in document and "显著" not in document
    assert "Fusion auto" in document
    assert len(detailed) == len(rows)
    # The baseline section, its plain reading and the new scatter marks all made it into the page.
    assert "路由基线对比" in document
    assert "BestSingle" in document
    assert inspect_report.BASELINE_LABELS["oracle"] in document
    assert "路由器至少要压过随机混合线" in document and "才谈得上会选模型" in document
    assert "可路由题越少" in document and "这套题越看不出路由能力" in document
    assert "stroke-dasharray" in document


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
    assert len(written["rows"]) == len(runs)
    assert set(written["baselines"]) == {"ifeval", "gpqa_diamond", "livecodebench_v6"}
    block = written["baselines"]["ifeval"]
    assert block["best_single"]["model"] == "gpt-6-astra"
    assert block["pool"] == ["gpt-6-astra", "gpt-5.6-terra"]
    assert "cost_fallback_samples" in block and "cost_fallback_note" in block
    with (out / "report.csv").open(encoding="utf-8") as stream:
        written_rows = list(csv.DictReader(stream))
    assert written_rows[0].keys() >= {"benchmark", "accuracy", "wilson_low"}
    baseline_rows = [row for row in written_rows if row["model"].startswith("baseline:")]
    assert {row["model"] for row in baseline_rows} == {
        "baseline:best_single",
        "baseline:oracle",
        "baseline:random",
    }
    assert all(row["accuracy"] and row["cost_usd_per_sample"] for row in baseline_rows)
    assert all(row["wilson_low"] == "" and row["n_planned"] == "" for row in baseline_rows)


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


# --------------------------------------------------------------------------- router baselines


def _baseline_fixture():
    """Four fixed models + one Fusion variant over four frozen ifeval samples.

    Per-sample cost is driven by the duration match, so the layout is explicit:
      cheap  $0.001/sample, right on s1 only          -> cheapest, and wrong on s2/s3
      mid    $0.004/sample, right on s1, s2           -> on the front
      pricey $0.010/sample, right on s1, s2, s3       -> best accuracy (3/4)
      dud    $0.020/sample, right on s1, s2, s3       -> same accuracy, dearer: dominated
    s4 is solved by nobody. s2 is routable (cheap fails, others succeed); so is s3.
    """
    plan = {
        "cheap": (0.001, {"s1"}),
        "mid": (0.004, {"s1", "s2"}),
        "pricey": (0.010, {"s1", "s2", "s3"}),
        "dud": (0.020, {"s1", "s2", "s3"}),
    }
    sample_ids = ["s1", "s2", "s3", "s4"]
    rows, runs, entries = [], [], []
    minute = 0
    for model, (unit, correct) in plan.items():
        for index, sample_id in enumerate(sample_ids):
            seconds = 5.0 + index
            rows.append(
                sample_row(
                    "ifeval",
                    f"openai-api/apigo/{model}",
                    sample_id,
                    "1" if sample_id in correct else "0",
                    seconds,
                )
            )
            entries.append(billing(model, minute, unit, int(seconds * 1000)))
            minute += 1
        runs.append(
            run(
                "ifeval",
                model,
                n_planned=4,
                started="2026-09-12T13:00:00+00:00",
                completed="2026-09-12T13:59:00+00:00",
            )
        )
    return rows, runs, entries, sample_ids


def _baselines_for(rows, runs, entries):
    report, detailed = inspect_report.build_rows(rows, runs, entries, {})
    return inspect_report.compute_baselines(report, detailed), report, detailed


def test_best_single_picks_the_top_accuracy_and_breaks_ties_by_cost():
    rows, runs, entries, _ = _baseline_fixture()
    baselines, _, _ = _baselines_for(rows, runs, entries)
    best = baselines["ifeval"]["best_single"]
    # `pricey` and `dud` both score 3/4; the cheaper one wins the tie.
    assert best["model"] == "pricey"
    assert best["accuracy"] == pytest.approx(0.75)
    assert best["cost_usd_per_sample"] == pytest.approx(0.010)


def test_oracle_picks_the_cheapest_correct_model_and_counts_unsolved_samples():
    rows, runs, entries, _ = _baseline_fixture()
    baselines, _, _ = _baselines_for(rows, runs, entries)
    oracle = baselines["ifeval"]["oracle"]
    # s1 solved by everyone -> cheap ($0.001); s2 -> mid ($0.004); s3 -> pricey ($0.010);
    # s4 nobody solved -> the cheapest attempt ($0.001).
    assert oracle["accuracy"] == pytest.approx(0.75)
    assert oracle["cost_usd_per_sample"] == pytest.approx((0.001 + 0.004 + 0.010 + 0.001) / 4)
    # s2 and s3: the cheapest model failed but another one succeeded.
    assert oracle["routable_share"] == pytest.approx(0.5)
    assert oracle["unsolved_share"] == pytest.approx(0.25)


def test_random_uniform_averages_accuracy_and_cost_over_the_fixed_pool():
    rows, runs, entries, _ = _baseline_fixture()
    baselines, _, _ = _baselines_for(rows, runs, entries)
    uniform = baselines["ifeval"]["random_uniform"]
    assert uniform["accuracy"] == pytest.approx((0.25 + 0.5 + 0.75 + 0.75) / 4)
    assert uniform["cost_usd_per_sample"] == pytest.approx((0.001 + 0.004 + 0.010 + 0.020) / 4)


def test_pareto_front_drops_the_dominated_model():
    rows, runs, entries, _ = _baseline_fixture()
    baselines, _, _ = _baselines_for(rows, runs, entries)
    front = baselines["ifeval"]["pareto_front"]
    # `dud` costs twice `pricey` for the same accuracy, so it never reaches the front.
    assert [point["model"] for point in front] == ["cheap", "mid", "pricey"]


def test_fusion_variants_are_never_in_the_candidate_pool():
    rows, runs, entries, _ = _baseline_fixture()
    rows += [
        sample_row("ifeval", "openai-api/apigo/apigo/lyra-auto", sample_id, "1", 5.0)
        for sample_id in ("s1", "s2", "s3", "s4")
    ]
    runs.append(
        run(
            "ifeval",
            "apigo/lyra-auto",
            n_planned=4,
            started="2026-09-12T13:00:00+00:00",
            completed="2026-09-12T13:59:00+00:00",
        )
    )
    baselines, _, _ = _baselines_for(rows, runs, entries)
    block = baselines["ifeval"]
    assert sorted(block["pool"]) == ["cheap", "dud", "mid", "pricey"]
    assert block["oracle"]["accuracy"] == pytest.approx(0.75)  # unchanged by the 4/4 Fusion run
    assert [variant["model"] for variant in block["variants"]] == ["apigo/lyra-auto"]


def test_per_sample_cost_falls_back_to_the_run_average_and_is_counted():
    rows, runs, entries, _ = _baseline_fixture()
    # Drop the billing line item that matches `mid`'s 8s sample: that sample has to fall back.
    entries = [
        entry for entry in entries if not (entry["model"] == "mid" and entry["duration_ms"] == 8000)
    ]
    baselines, report, detailed = _baselines_for(rows, runs, entries)
    block = baselines["ifeval"]
    assert block["cost_fallback_samples"]["by_model"]["mid"] == 1
    assert block["cost_fallback_samples"]["total"] == 1
    assert "运行平均每题成本" in block["cost_fallback_note"]
    # Exactly one `mid` sample lost its duration match and took the run average instead.
    unmatched = [
        row for row in detailed if row["variant"] == "mid" and row["cost_usd_matched"] == ""
    ]
    assert len(unmatched) == 1
    # `mid` now settles 3 x $0.004 over 4 samples, so its run average is $0.003 and the
    # per-sample mean mixes three matched values with that one fallback.
    average = next(row for row in report if row["model"] == "mid")["cost_usd_per_sample"]
    assert average == pytest.approx(0.003)
    mid_point = next(p for p in block["pareto_front"] if p["model"] == "mid")
    assert mid_point["cost_usd_per_sample"] == pytest.approx((0.004 * 3 + 0.003) / 4)


def test_headroom_reports_no_room_when_oracle_equals_best_single():
    # Two fixed models with identical answers: the oracle cannot beat the best single model.
    rows, runs, entries = [], [], []
    minute = 0
    for model, unit in (("cheap", 0.001), ("pricey", 0.010)):
        for index, sample_id in enumerate(("s1", "s2")):
            seconds = 5.0 + index
            rows.append(
                sample_row(
                    "ifeval",
                    f"openai-api/apigo/{model}",
                    sample_id,
                    "1" if sample_id == "s1" else "0",
                    seconds,
                )
            )
            entries.append(billing(model, minute, unit, int(seconds * 1000)))
            minute += 1
        runs.append(run("ifeval", model, n_planned=2, completed="2026-09-12T13:59:00+00:00"))
    rows += [
        sample_row("ifeval", "openai-api/apigo/apigo/lyra-auto", "s1", "1", 5.0),
        sample_row("ifeval", "openai-api/apigo/apigo/lyra-auto", "s2", "0", 6.0),
    ]
    runs.append(
        run("ifeval", "apigo/lyra-auto", n_planned=2, completed="2026-09-12T13:59:00+00:00")
    )
    baselines, _, _ = _baselines_for(rows, runs, entries)
    block = baselines["ifeval"]
    assert block["oracle"]["accuracy"] == block["best_single"]["accuracy"]
    (variant,) = block["variants"]
    assert variant["headroom_is_empty"] is True
    assert variant["headroom_captured"] is None
    assert "无空间" in inspect_report._baseline_table(block)


def test_random_mix_line_classifies_a_fusion_point_above_and_below():
    front = [
        {"model": "cheap", "cost_usd_per_sample": 0.001, "accuracy": 0.25},
        {"model": "pricey", "cost_usd_per_sample": 0.011, "accuracy": 0.75},
    ]
    # Halfway in cost, the input-agnostic mix reaches 0.50.
    assert inspect_report.random_mix_accuracy(front, 0.006) == pytest.approx(0.5)
    assert inspect_report.classify_vs_mix(front, 0.006, 0.70) == "是"
    assert inspect_report.classify_vs_mix(front, 0.006, 0.30) == "否"
    assert inspect_report.classify_vs_mix(front, 0.006, 0.50) == "持平"
    # Outside the front's cost range the line is clamped, never extrapolated.
    assert inspect_report.random_mix_accuracy(front, 0.0001) == pytest.approx(0.25)
    assert inspect_report.random_mix_accuracy(front, 0.9) == pytest.approx(0.75)
    assert inspect_report.classify_vs_mix(front, None, 0.5) == "—"


def test_random_mix_line_also_judges_the_cost_axis():
    front = [
        {"model": "cheap", "cost_usd_per_sample": 0.001, "accuracy": 0.25},
        {"model": "pricey", "cost_usd_per_sample": 0.011, "accuracy": 0.75},
    ]
    # Same accuracy as the cheapest front model, for less money: still a win over mixing.
    assert inspect_report.random_mix_cost(front, 0.25) == pytest.approx(0.001)
    assert inspect_report.classify_vs_mix(front, 0.0004, 0.25) == "是"
    assert inspect_report.classify_vs_mix(front, 0.001, 0.25) == "持平"
    # Same accuracy, ten times the money: the mix line does it cheaper.
    assert inspect_report.classify_vs_mix(front, 0.010, 0.25) == "否"
    # More accurate than any mix can reach at any price.
    assert inspect_report.random_mix_cost(front, 0.9) is None
    assert inspect_report.classify_vs_mix(front, 0.5, 0.9) == "是"


def test_baseline_table_and_csv_rows_carry_the_three_baselines():
    rows, runs, entries, _ = _baseline_fixture()
    rows += [
        sample_row("ifeval", "openai-api/apigo/apigo/lyra-auto", sample_id, score, 5.0)
        for sample_id, score in (("s1", "1"), ("s2", "1"), ("s3", "0"), ("s4", "0"))
    ]
    runs.append(
        run("ifeval", "apigo/lyra-auto", n_planned=4, completed="2026-09-12T13:59:00+00:00")
    )
    baselines, _, _ = _baselines_for(rows, runs, entries)
    table = inspect_report._baseline_table(baselines["ifeval"])
    assert "BestSingle" in table and "pricey" in table
    assert inspect_report.BASELINE_LABELS["oracle"] in table
    assert inspect_report.BASELINE_LABELS["random"] in table
    assert "Fusion auto" in table
    csv_rows = inspect_report.baseline_csv_rows(baselines)
    assert [row["model"] for row in csv_rows] == [
        "baseline:best_single",
        "baseline:oracle",
        "baseline:random",
    ]
    assert all(row["benchmark"] == "ifeval" for row in csv_rows)
    assert all(row["n_correct"] == "" for row in csv_rows)
