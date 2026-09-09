import asyncio
from types import SimpleNamespace

import pytest

from vohu_evals.console import live_scheduler
from vohu_evals.console.live import price_bound
from vohu_evals.console.store import Store
from vohu_evals.suites.packs import freeze_pack


def test_price_bound_accounts_for_cache_and_long_context():
    catalog = {
        "data": {
            "items": [
                {
                    "slug": "model",
                    "pricing_headline": {
                        "input_usd_per_1m": 1,
                        "output_usd_per_1m": 2,
                        "cache_write_1h_usd_per_1m": 3,
                        "above_threshold": {"input_usd_per_1m": 4, "output_usd_per_1m": 5},
                    },
                }
            ]
        }
    }
    assert price_bound(catalog, "model") == (4, 5)
    with pytest.raises(ValueError):
        price_bound(catalog, "missing")


def test_budget_and_suite_rejection_precede_network(monkeypatch):
    def no_network():
        raise AssertionError("No network during rejected preflight")

    monkeypatch.setattr(live_scheduler, "catalog", no_network)
    with pytest.raises(ValueError):
        asyncio.run(live_scheduler.prepare({"manifest": {"benchmarks": ["tau2"]}}, 1))
    with pytest.raises(ValueError):
        asyncio.run(
            live_scheduler.prepare(
                {"manifest": {"benchmarks": ["gpqa"]}, "episode_caps_total": "2"}, 1
            )
        )


def test_real_dispatch_scoring_and_evidence_without_model_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(live_scheduler, "ROOT", tmp_path)
    freeze_pack(
        tmp_path / ".local/suites",
        "gpqa",
        [{"case_id": "one", "prompt": "test", "expected": "A"}],
        {"name": "fixture"},
    )

    async def agent(*args):
        return {
            "answer": "Answer: A",
            "latency_ms": 123,
            "requests": [{"request_id": "req", "estimated_cost_usd": 0.01}],
            "reserved_usd": 0.1,
        }

    monkeypatch.setattr(live_scheduler, "live_episode", agent)
    store = Store(tmp_path / "console")
    run = {
        "id": "run",
        "name": "test",
        "max_jobs": 1,
        "max_episodes": 1,
        "per_job": 1,
        "manifest": {
            "variants": [{"id": "v", "target_id": "t", "deadline_seconds": 5}],
            "datasets": {"gpqa": {"case_ids": ["one"]}},
        },
        "jobs": [
            {
                "id": "job",
                "variant_id": "v",
                "benchmark": "gpqa",
                "episodes": [],
                "completed": 0,
                "scored": 0,
                "correct": 0,
                "latencies": [],
                "total": 1,
            }
        ],
    }
    scheduler = SimpleNamespace(
        store=store,
        global_slots=asyncio.Semaphore(1),
        event=lambda r, *a: store.save_run(r),
        tasks={},
    )
    asyncio.run(live_scheduler.execute(scheduler, run))
    assert run["status"] == "completed"
    job = store.runs()[0]["jobs"][0]
    assert job["correct"] == 1
    assert job["estimated_cost_usd"] == 0.01
    assert "answer" not in job["episodes"][0]
    assert job["episodes"][0]["cost_usd"] is None
    store.db.close()


def test_acp_permission_only_enables_container_tools():
    import json

    from vohu_evals.console.acp import ACPClient

    class Writer:
        def __init__(self):
            self.messages = []

        def write(self, data):
            self.messages.append(json.loads(data))

        async def drain(self):
            pass

    async def check(allow):
        reader = asyncio.StreamReader()
        writer = Writer()
        for event in [
            {
                "id": 2,
                "method": "session/request_permission",
                "params": {"options": [{"kind": "allow_once", "optionId": "one"}]},
            },
            {"id": 3, "method": "fs/read_text_file", "params": {"path": "/private"}},
            {"id": 1, "result": {"stopReason": "end_turn"}},
        ]:
            reader.feed_data((json.dumps(event) + "\n").encode())
        reader.feed_eof()
        client = ACPClient(
            SimpleNamespace(stdin=writer, stdout=reader), allow_container_tools=allow
        )
        await client.call("session/prompt", {})
        response = writer.messages[1]
        if allow:
            assert response["result"]["outcome"] == {"outcome": "selected", "optionId": "one"}
        else:
            assert "error" in response
        assert "error" in writer.messages[2]

    asyncio.run(check(True))
    asyncio.run(check(False))


def test_internal_model_uses_explicit_price_cap_only_when_public_price_missing():
    from vohu_evals.console.contracts import PriceCap

    catalog = {"data": {"items": []}}
    cap = {"input_usd_per_1m": 3, "output_usd_per_1m": 12}
    assert price_bound(catalog, "apigo/lyra-auto", cap) == (3, 12)
    with pytest.raises(ValueError):
        price_bound(catalog, "apigo/lyra-auto")
    with pytest.raises(ValueError):
        PriceCap(input_usd_per_1m=float("inf"), output_usd_per_1m=12)
