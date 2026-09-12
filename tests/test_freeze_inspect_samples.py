from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from vohu_evals.suites.packs import freeze_pack

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "freeze_inspect_samples.py"
_SPEC = importlib.util.spec_from_file_location("freeze_inspect_samples", SCRIPT_PATH)
freeze_inspect_samples = importlib.util.module_from_spec(_SPEC)
sys.modules["freeze_inspect_samples"] = freeze_inspect_samples
_SPEC.loader.exec_module(freeze_inspect_samples)  # type: ignore[union-attr]


def make_ifeval_pack(root: Path, count: int = 40) -> None:
    rows = []
    for i in range(count):
        # vary instruction count 1..3 so we get multiple strata
        n_instr = 1 + (i % 3)
        rows.append(
            {
                "case_id": str(1000 + i),
                "key": 1000 + i,
                "instruction_id_list": [f"type_{j}" for j in range(n_instr)],
                "kwargs": [{} for _ in range(n_instr)],
                "prompt": f"prompt {i}",
            }
        )
    freeze_pack(root, "ifeval", rows, {"revision": "test"})


# ---------------------------------------------------------------------------
# apportion()
# ---------------------------------------------------------------------------


def test_apportion_sums_exactly_with_rounding():
    populations = {"a": 7, "b": 13, "c": 5}
    result = freeze_inspect_samples.apportion(populations, 10)
    assert sum(result.values()) == 10
    assert all(0 <= result[k] <= populations[k] for k in populations)


def test_apportion_deterministic_tie_break():
    populations = {"a": 1, "b": 1, "c": 1}
    r1 = freeze_inspect_samples.apportion(populations, 2)
    r2 = freeze_inspect_samples.apportion(populations, 2)
    assert r1 == r2
    assert sum(r1.values()) == 2


def test_apportion_rejects_total_exceeding_population():
    with pytest.raises(ValueError):
        freeze_inspect_samples.apportion({"a": 2, "b": 2}, 10)


# ---------------------------------------------------------------------------
# build_strata() / uniform fallback
# ---------------------------------------------------------------------------


def test_build_strata_groups_by_label():
    pairs = [("1", "a"), ("2", "a"), ("3", "b")]
    strata, field, reason = freeze_inspect_samples.build_strata(pairs, "some_field")
    assert field == "some_field"
    assert reason is None
    assert strata == {"a": ["1", "2"], "b": ["3"]}


def test_build_strata_uniform_fallback_when_label_missing():
    pairs = [("1", None), ("2", None), ("3", None)]
    strata, field, reason = freeze_inspect_samples.build_strata(pairs, "domain")
    assert field == "uniform"
    assert reason is not None and "domain" in reason
    assert set(strata["uniform"]) == {"1", "2", "3"}


def test_build_strata_uniform_fallback_when_partially_missing():
    pairs = [("1", "a"), ("2", None), ("3", "b")]
    strata, field, reason = freeze_inspect_samples.build_strata(pairs, "domain")
    assert field == "uniform"
    assert "1/3" in reason or "missing" in reason
    assert set(strata["uniform"]) == {"1", "2", "3"}


# ---------------------------------------------------------------------------
# freeze_suite(): determinism, disjointness, exclusion
# ---------------------------------------------------------------------------


def _synthetic_pairs(n=60):
    # three strata of 20 ids each
    pairs = []
    for i in range(n):
        stratum = ["low", "mid", "high"][i % 3]
        pairs.append((f"id-{i:03d}", stratum))
    return pairs


def test_freeze_suite_is_deterministic():
    pairs = _synthetic_pairs()
    r1 = freeze_inspect_samples.freeze_suite(pairs, "level", 6, 12, 20260909, set())
    r2 = freeze_inspect_samples.freeze_suite(pairs, "level", 6, 12, 20260909, set())
    assert r1["calibration_ids"] == r2["calibration_ids"]
    assert r1["main_ids"] == r2["main_ids"]
    assert r1["strata"] == r2["strata"]


def test_freeze_suite_calibration_and_main_are_disjoint():
    pairs = _synthetic_pairs()
    result = freeze_inspect_samples.freeze_suite(pairs, "level", 6, 12, 20260909, set())
    assert set(result["calibration_ids"]).isdisjoint(result["main_ids"])
    assert len(result["calibration_ids"]) == 6
    assert len(result["main_ids"]) == 12


def test_freeze_suite_excludes_historical_ids():
    pairs = _synthetic_pairs()
    excluded = {"id-000", "id-001", "id-002"}
    result = freeze_inspect_samples.freeze_suite(pairs, "level", 6, 12, 20260909, excluded)
    assert result["excluded_ids_count"] == 3
    all_drawn = set(result["calibration_ids"]) | set(result["main_ids"])
    assert all_drawn.isdisjoint(excluded)
    assert result["population_after_exclusion"] == len(pairs) - 3


def test_freeze_suite_different_seed_changes_draw():
    pairs = _synthetic_pairs()
    r1 = freeze_inspect_samples.freeze_suite(pairs, "level", 6, 12, 1, set())
    r2 = freeze_inspect_samples.freeze_suite(pairs, "level", 6, 12, 2, set())
    assert r1["calibration_ids"] != r2["calibration_ids"] or r1["main_ids"] != r2["main_ids"]


def test_freeze_suite_uniform_fallback_reported():
    pairs = [(f"id-{i}", None) for i in range(20)]
    result = freeze_inspect_samples.freeze_suite(pairs, "domain", 3, 5, 20260909, set())
    assert result["stratum_field"] == "uniform"
    assert result["uniform_reason"] is not None


# ---------------------------------------------------------------------------
# load_ifeval_pairs(): real loader against a tiny synthetic pack
# ---------------------------------------------------------------------------


def test_load_ifeval_pairs_extracts_key_and_instruction_count(tmp_path: Path):
    packs_root = tmp_path / "suites"
    make_ifeval_pack(packs_root, count=12)
    pairs, manifest, coverage = freeze_inspect_samples.load_ifeval_pairs(packs_root)
    assert len(pairs) == 12
    ids = {id_ for id_, _ in pairs}
    assert ids == {str(1000 + i) for i in range(12)}
    strata_labels = {stratum for _, stratum in pairs}
    assert strata_labels == {"1", "2", "3"}
    assert manifest["suite"] == "ifeval"
    assert sum(coverage.values()) > 0


def test_load_ifeval_pairs_requires_key_field(tmp_path: Path):
    packs_root = tmp_path / "suites"
    rows = [{"case_id": "1", "prompt": "x", "instruction_id_list": [], "kwargs": []}]
    freeze_pack(packs_root, "ifeval", rows, {"revision": "test"})
    with pytest.raises(freeze_inspect_samples.SuiteUnavailable):
        freeze_inspect_samples.load_ifeval_pairs(packs_root)


# ---------------------------------------------------------------------------
# CLI: end-to-end with a tiny synthetic pack, refuse-to-overwrite behaviour
# ---------------------------------------------------------------------------


def test_cli_end_to_end_and_refuses_overwrite(tmp_path: Path):
    packs_root = tmp_path / "suites"
    make_ifeval_pack(packs_root, count=30)
    out_dir = tmp_path / "out"

    argv = [
        "--suites",
        "ifeval",
        "--out",
        str(out_dir),
        "--packs-root",
        str(packs_root),
        "--console-db",
        str(tmp_path / "no-console.sqlite3"),
        "--inspect-logs",
        str(tmp_path / "no-logs"),
        "--size",
        "ifeval=5,15",
    ]
    rc = freeze_inspect_samples.main(argv)
    assert rc == 0

    json_path = out_dir / "ifeval.json"
    cal_path = out_dir / "ifeval.calibration.txt"
    main_path = out_dir / "ifeval.main.txt"
    assert json_path.exists() and cal_path.exists() and main_path.exists()

    payload = json.loads(json_path.read_text())
    assert payload["seed"] == freeze_inspect_samples.DEFAULT_SEED
    assert len(payload["calibration_ids"]) == 5
    assert len(payload["main_ids"]) == 15
    assert set(payload["calibration_ids"]).isdisjoint(payload["main_ids"])

    cal_ids_from_txt = cal_path.read_text().split(",")
    assert sorted(cal_ids_from_txt) == sorted(payload["calibration_ids"])

    # Running again without --force must refuse to overwrite.
    rc2 = freeze_inspect_samples.main(argv)
    assert rc2 == 1
    # And output must be unchanged.
    assert json.loads(json_path.read_text()) == payload

    # With --force it succeeds and (same inputs) reproduces identical output.
    rc3 = freeze_inspect_samples.main([*argv, "--force"])
    assert rc3 == 0
    assert json.loads(json_path.read_text()) == payload


def test_cli_excludes_console_historical_case_ids(tmp_path: Path):
    packs_root = tmp_path / "suites"
    make_ifeval_pack(packs_root, count=30)
    out_dir = tmp_path / "out"

    console_db = tmp_path / "console.sqlite3"
    con = sqlite3.connect(str(console_db))
    con.execute("create table runs (id text, payload text)")
    payload = {
        "jobs": [
            {
                "benchmark": "ifeval",
                "episodes": [{"case_id": "1000"}, {"case_id": "1001"}],
            }
        ]
    }
    con.execute("insert into runs values (?, ?)", ("run-1", json.dumps(payload)))
    con.commit()
    con.close()

    argv = [
        "--suites",
        "ifeval",
        "--out",
        str(out_dir),
        "--packs-root",
        str(packs_root),
        "--console-db",
        str(console_db),
        "--inspect-logs",
        str(tmp_path / "no-logs"),
        "--size",
        "ifeval=5,15",
    ]
    rc = freeze_inspect_samples.main(argv)
    assert rc == 0
    result = json.loads((out_dir / "ifeval.json").read_text())
    assert result["excluded_ids_count"] == 2
    drawn = set(result["calibration_ids"]) | set(result["main_ids"])
    assert "1000" not in drawn and "1001" not in drawn
