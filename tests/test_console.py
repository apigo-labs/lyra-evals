from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from vohu_evals.console.contracts import RunInput, TargetInput
from vohu_evals.console.scheduler import Scheduler
from vohu_evals.console.server import create_app
from vohu_evals.console.store import Store

HEADERS = {"X-Lyra-Console": "1", "Content-Type": "application/json"}


def model_input():
    return {
        "name": "Test",
        "endpoint": "https://gateway.example.com",
        "model": "test/model",
        "protocol": "anthropic_messages",
    }


def test_target_metadata_only_no_per_target_secret_files(tmp_path: Path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/targets", json=model_input(), headers=HEADERS)
        assert response.status_code == 201
        target = response.json()
        assert "api_key" not in target
        assert "has_key" not in target
        with sqlite3.connect(tmp_path / "console.sqlite3") as db:
            metadata = db.execute("SELECT metadata FROM targets").fetchone()[0]
            assert "api_key" not in metadata
        # Credentials are never per-target files on disk anymore; the Console
        # holds only model/protocol/endpoint metadata and reads the shared
        # gateway key from VOHU_EVALS_API_KEY at run time.
        assert not (tmp_path / "secrets").exists()
        bad = client.post(
            "/api/targets", json={**model_input(), "protocol": "invalid"}, headers=HEADERS
        )
        assert bad.status_code == 422
        assert client.delete(f"/api/targets/{target['id']}", headers=HEADERS).status_code == 200


def test_cross_origin_and_host_rejected(tmp_path: Path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/targets", json=model_input()).status_code == 403
        assert (
            client.post(
                "/api/targets",
                json=model_input(),
                headers={**HEADERS, "Origin": "https://attacker.example"},
            ).status_code
            == 403
        )
        assert client.get("/api/targets", headers={"Host": "attacker.example"}).status_code == 403
        assert (
            client.get("/api/targets", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        )


def test_live_fails_closed_before_any_model_call(tmp_path: Path):
    with TestClient(create_app(tmp_path)) as client:
        target = client.post("/api/targets", json=model_input(), headers=HEADERS).json()
        response = client.post(
            "/api/runs",
            json={
                "name": "Live",
                "mode": "live",
                "benchmarks": ["ifeval"],
                "targets": [target["id"]],
                "confirm_budget": True,
            },
            headers=HEADERS,
        )
        assert response.status_code == 409
        assert client.get("/api/runs").json() == []


def test_contract_rejects_unsafe_endpoint_and_duplicate_work():
    with pytest.raises(ValueError):
        TargetInput(**{**model_input(), "endpoint": "https://user:pass@example.com"})
    with pytest.raises(ValueError):
        RunInput(name="bad", mode="smoke", benchmarks=["ifeval", "ifeval"])
    with pytest.raises(ValueError):
        RunInput(name="bad", mode="smoke", benchmarks=["ifeval"], targets=["real"])


def test_global_concurrency_across_experiments_and_persistence(tmp_path: Path, monkeypatch):
    running = peak = 0

    async def fake_episode(name, image):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return True, 12

    monkeypatch.setattr("vohu_evals.console.scheduler.smoke_episode", fake_episode)

    async def check():
        store = Store(tmp_path)
        scheduler = Scheduler(store, global_limit=2)
        runs = []
        for index in range(2):
            run = {
                "id": str(index),
                "status": "queued",
                "max_jobs": 2,
                "max_episodes": 4,
                "per_job": 3,
                "events": [],
                "jobs": [
                    {
                        "id": f"{index}-{b}",
                        "benchmark": b,
                        "status": "queued",
                        "completed": 0,
                        "correct": 0,
                        "total": 3,
                        "latencies": [],
                        "error": None,
                    }
                    for b in ["ifeval", "gpqa"]
                ],
            }
            runs.append(run)
        await asyncio.gather(*(scheduler.execute(r, "synthetic-digest") for r in runs))
        assert peak == 2
        assert all(r["status"] == "completed" for r in store.runs())
        assert sum(j["completed"] for r in store.runs() for j in r["jobs"]) == 12
        assert all(
            [e["seq"] for e in r["events"]] == list(range(1, len(r["events"]) + 1)) for r in runs
        )
        store.db.close()

    asyncio.run(check())


def test_restart_does_not_replay_paid_requests(tmp_path: Path):
    store = Store(tmp_path)
    store.save_run(
        {
            "id": "old",
            "status": "running",
            "events": [],
            "jobs": [{"status": "running"}, {"status": "completed"}],
        }
    )
    scheduler = Scheduler(store)
    assert scheduler.runs["old"]["status"] == "interrupted"
    assert scheduler.runs["old"]["jobs"][1]["status"] == "completed"
    assert scheduler.tasks == {}
    assert "api_key" not in json.dumps(store.runs())
    store.db.close()
