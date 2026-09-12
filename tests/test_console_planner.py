from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from vohu_evals.console.budget import BudgetLedger
from vohu_evals.console.planner import PlanInput, build_plan

TARGET = {
    "id": "test",
    "endpoint": "https://gateway.example/v1",
    "model": "gpt-test",
    "protocol": "openai_responses",
}


def spec(**updates):
    return PlanInput.model_validate(
        {
            "name": "Test",
            "benchmarks": ["ifeval"],
            "variants": [
                {"target_id": "test", "harness": "codex", "efforts": ["high", "low", "high"]}
            ],
            **updates,
        }
    )


def test_plan_dedup_snapshot_and_hash():
    plan = build_plan(spec(), [TARGET])
    assert plan["jobs"] == 2 and plan["episodes"] == 6
    assert "never-export-this" not in str(plan)
    assert plan["estimated_cost"] is None and not plan["executable"]
    assert plan["manifest"]["comparison"] == "reference_only"
    assert all(v["effective_effort"] is None for v in plan["manifest"]["variants"])
    alternative = spec()
    alternative.variants[0].efforts = ["low", "high"]
    assert build_plan(alternative, [TARGET])["id"] == plan["id"]
    alternative.variants[0].deadline_seconds += 1
    assert build_plan(alternative, [TARGET])["id"] != plan["id"]
    assert (
        build_plan(spec(), [{**TARGET, "endpoint": "https://changed.example/v1"}])["id"]
        != plan["id"]
    )


def test_invalid_protocol_effort_and_matrix():
    with pytest.raises(ValueError):
        build_plan(spec(), [{**TARGET, "protocol": "openai_chat_completions"}])
    bad = spec()
    bad.variants[0].efforts = ["max"]
    with pytest.raises(ValueError):
        build_plan(bad, [TARGET])
    with pytest.raises(ValueError):
        build_plan(spec(sample_size=1000, trials=10), [TARGET])
    with pytest.raises(ValueError):
        build_plan(spec(), [])


def test_budget_policy_requires_equal_limits():
    variants = [v.model_dump() for v in spec().variants] * 2
    variants[1] = {**variants[1], "episode_budget": Decimal("2")}
    with pytest.raises(ValueError):
        spec(variants=variants, budget_policy="equal_cost")


def test_persistent_plans_api_and_stale_connection(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from vohu_evals.console.server import create_app

    headers = {"X-Lyra-Console": "1", "Content-Type": "application/json"}
    with TestClient(create_app(tmp_path)) as client:
        target = client.post(
            "/api/targets",
            json={"name": "Test", **{k: v for k, v in TARGET.items() if k != "id"}},
            headers=headers,
        ).json()
        data = spec().model_dump(mode="json")
        data["variants"][0]["target_id"] = target["id"]
        result = client.post("/api/plans", json=data, headers=headers)
        assert result.status_code == 201
        plan = result.json()
        assert client.get("/api/plans").json() == [plan]
        assert client.get("/api/capabilities").json()[0]["verified_efforts"] == []
        client.delete(f"/api/targets/{target['id']}", headers=headers)
        result = client.post(
            "/api/runs",
            json={
                "name": "Test",
                "mode": "live",
                "targets": [target["id"]],
                "benchmarks": ["ifeval"],
                "confirm_budget": True,
                "plan_id": plan["id"],
            },
            headers=headers,
        )
        assert result.status_code == 409
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(f"/api/plans/{plan['id']}").json() == plan


def test_competing_reservations_and_late_settlement(tmp_path):
    path = tmp_path / "budget.sqlite3"
    ledger = BudgetLedger(path)
    ledger.define("run", "1")

    def reserve(index):
        worker = BudgetLedger(path)
        try:
            worker.reserve("run", str(index), "0.6")
            return str(index)
        except ValueError:
            return None
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, [1, 2]))
    winner = next(r for r in results if r)
    assert results.count(None) == 1
    assert ledger.summary("run")["pending"] == 1
    ledger.close()
    ledger = BudgetLedger(path)
    ledger.settle(winner, "event-1", "0.2")
    ledger.settle(winner, "event-1", "0.2")
    assert ledger.summary("run")["settled"] == "0.2"
    ledger.reserve("run", "next", "0.8")
    ledger.settle("next", "event-2", "1.1")
    assert ledger.summary("run")["over_budget"]
    with pytest.raises(ValueError):
        ledger.reserve("run", "blocked", "0.01")
    with pytest.raises(ValueError):
        ledger.settle(winner, "event-1", "0.3")
    ledger.close()


def test_codex_config_isolated_and_toml_safe():
    import tomllib

    from vohu_evals.console.harness import codex_config

    config = tomllib.loads(
        codex_config('gpt-test"\ninjected', "https://gateway.example/v1", "high")
    )
    assert config["model"] == 'gpt-test"\ninjected'
    assert config["model_reasoning_effort"] == "high"
    assert config["model_providers"]["apigo"]["wire_api"] == "responses"
    assert config["web_search"] == "disabled"
    assert "model_reasoning_effort" not in tomllib.loads(
        codex_config("test", "https://gateway.example/v1", "provider_default")
    )
    with pytest.raises(ValueError):
        codex_config("test", "https://secret@gateway.example", "high")


def test_effort_negotiation_requires_acknowledgment():
    import asyncio

    from vohu_evals.console.acp import ACPError
    from vohu_evals.console.harness import configure_effort

    session = {
        "sessionId": "test",
        "configOptions": [
            {"id": "effort", "category": "thought_level", "options": [{"value": "high"}]}
        ],
    }

    class Client:
        async def call(self, method, params):
            return {"configOptions": [{"id": "effort", "currentValue": "low"}]}

    with pytest.raises(ACPError):
        asyncio.run(configure_effort(Client(), session, "high"))
    with pytest.raises(ACPError):
        asyncio.run(configure_effort(Client(), session, "max"))
    result = asyncio.run(configure_effort(Client(), session, "provider_default"))
    assert result["effective_effort"] is None


def test_custom_codex_model_requires_frozen_config_and_relay_validation():
    import asyncio

    from vohu_evals.console.acp import ACPError
    from vohu_evals.console.harness import configure_effort

    session = {"sessionId": "custom"}
    with pytest.raises(ACPError):
        asyncio.run(configure_effort(None, session, "high"))
    with pytest.raises(ACPError):
        asyncio.run(configure_effort(None, session, "high", codex_config_effort="low"))
    result = asyncio.run(configure_effort(None, session, "high", codex_config_effort="high"))
    assert result["serialized_effort"] is None
    assert result["effective_effort"] is None
    assert result["effort_configuration"] == "codex_config_with_relay_validation"


def test_lyra_routed_targets_are_blocked_until_ingress_preflight():
    lyra = {
        "id": "lyra",
        "endpoint": "https://api.apigo.ai",
        "model": "apigo/lyra-auto",
        "protocol": "anthropic_messages",
    }
    plan = build_plan(
        spec(variants=[{"target_id": "lyra", "harness": "claude_agent", "efforts": ["high"]}]),
        [lyra],
    )
    assert any("apigo/lyra-auto" in b for b in plan["blockers"])
    assert not plan["executable"]
    plain = build_plan(spec(), [TARGET])
    assert not any("lyra" in b for b in plain["blockers"])
