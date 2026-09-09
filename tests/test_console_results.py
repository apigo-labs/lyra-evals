from fastapi.testclient import TestClient

from vohu_evals.console.results import csv_export, result_rows
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
