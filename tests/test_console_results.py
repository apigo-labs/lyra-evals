from fastapi.testclient import TestClient

from vohu_evals.console.results import csv_export, result_rows, wilson_interval
from vohu_evals.console.server import create_app
from vohu_evals.console.store import Store


def fixture_run():
    return {
        "id": "run",
        "name": "=unsafe",
        "mode": "live",
        "status": "completed",
        "created_at": "2026-09-09",
        "events": [],
        "manifest": {},
        "jobs": [
            {
                "id": "job",
                "benchmark": "gpqa",
                "target_name": "model",
                "status": "completed",
                "total": 4,
                "completed": 4,
                "correct": 3,
                "cost": 0.12,
                "latencies": [100, 200, 300, 900],
            }
        ],
    }


def test_unknown_billing_and_partial_accuracy():
    run = fixture_run()
    row = result_rows([run])[0]
    assert row["accuracy"] == 0.75
    assert row["cost_usd"] is None
    assert row["latency_p95_ms"] == 900
    run["jobs"][0].update(billing_status="settled")
    assert result_rows([run])[0]["cost_per_correct_usd"] == 0.04
    run["jobs"][0].update(status="running", completed=3)
    row = result_rows([run])[0]
    assert row["accuracy"] is None
    assert row["provisional_accuracy"] == 0.75
    assert row["cost_per_correct_usd"] is None


def test_synthetic_never_scores():
    run = fixture_run()
    run["mode"] = "smoke"
    row = result_rows([run])[0]
    assert row["accuracy"] is None
    assert row["correct"] is None
    assert row["comparison"] == "not_comparable"
    assert "'=unsafe" in csv_export([row])


def test_ifeval_micro_average_and_missing_evidence():
    run = fixture_run()
    job = run["jobs"][0]
    job.update(benchmark="ifeval", total=2, completed=2, scored=2, correct=1)
    job["episodes"] = [
        {
            "case_id": "a",
            "score": {
                "details": {
                    "strict_prompt": True,
                    "loose_prompt": True,
                    "strict_instruction_list": [True],
                    "loose_instruction_list": [True],
                }
            },
        },
        {
            "case_id": "b",
            "score": {
                "details": {
                    "strict_prompt": False,
                    "loose_prompt": True,
                    "strict_instruction_list": [True, False, False],
                    "loose_instruction_list": [True, True, True],
                }
            },
        },
    ]
    row = result_rows([run])[0]
    assert row["ifeval_instruction_strict_accuracy"] == 0.5
    assert row["ifeval_prompt_strict_accuracy"] == 0.5
    assert row["ifeval_prompt_loose_accuracy"] == 1
    assert row["ifeval_instruction_count"] == 4
    assert row["accuracy_ci95_low"] < 0.5 < row["accuracy_ci95_high"]
    job["episodes"][1]["case_id"] = "a"
    assert result_rows([run])[0]["ifeval_instruction_strict_accuracy"] is None
    job.update(scored=1)
    row = result_rows([run])[0]
    assert row["accuracy"] is None
    assert row["accuracy_ci95_low"] is None
    assert row["scoring_coverage"] == 0.5


def test_wilson_boundary_and_empty_samples():
    assert wilson_interval(0, 0) == (None, None)
    low, high = wilson_interval(0, 541)
    assert low == 0
    assert 0.007 < high < 0.008
    low, high = wilson_interval(541, 541)
    assert 0.992 < low < 0.993
    assert high == 1


def test_results_api_filters_and_export(tmp_path):
    store = Store(tmp_path)
    run = fixture_run()
    store.save_run(run)
    smoke = {**fixture_run(), "id": "smoke", "mode": "smoke"}
    store.save_run(smoke)
    store.db.close()
    with TestClient(create_app(tmp_path)) as client:
        result = client.get("/api/results").json()
        assert len(result["rows"]) == 1
        assert len(client.get("/api/results?include_synthetic=true").json()["rows"]) == 2
        assert client.get("/api/results?model=missing").json()["rows"] == []
        exported = client.get("/api/results?format=csv&run_id=run")
        assert "attachment" in exported.headers["content-disposition"]
        assert "'=unsafe" in exported.text
        assert "api_key" not in exported.text
        assert client.get("/api/results?format=exe").status_code == 422
