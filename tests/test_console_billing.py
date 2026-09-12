import asyncio
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from vohu_evals.console.billing import (
    BillingConfig,
    aggregate_billing,
    invoice,
    reconcile,
    save_config,
)


def receipt(**updates):
    return {
        "code": 0,
        "data": {
            "id": "req-1",
            "model": "test",
            "settled": True,
            "cost_usd_exact": "0.000000123",
            **updates,
        },
    }


def test_receipt_identity_and_exact_amount():
    assert invoice(receipt(), "req-1", "test") == Decimal("0.000000123")
    assert invoice(receipt(settled=False), "req-1", "test") is None
    for updates in [
        {"id": "other"},
        {"model": "other"},
        {"cost_usd_exact": "NaN"},
        {"cost_usd_exact": "-1"},
    ]:
        with pytest.raises(ValueError):
            invoice(receipt(**updates), "req-1", "test")


def test_partial_and_missing_evidence_never_become_free():
    job = {
        "total": 2,
        "episodes": [
            {"requests": [{"billing_status": "settled", "billed_cost_usd_exact": "0.1"}]},
            {},
        ],
    }
    aggregate_billing(job)
    assert job["episodes"][0]["cost_usd_exact"] == "0.1"
    assert "cost" not in job
    job["episodes"][1]["requests"] = []
    aggregate_billing(job)
    assert job["cost_usd_exact"] == "0.1"


@pytest.mark.parametrize("list_fallback", [False, True])
def test_reconcile_private_configuration_and_idempotent_receipts(tmp_path, list_fallback):
    saved = []
    events = []
    store = SimpleNamespace(secrets=tmp_path, save_run=lambda r: saved.append(r.copy()))
    config = BillingConfig(workspace_id="workspace", token="private-token-value")
    save_config(store, config)
    assert (tmp_path / "platform-billing.json").stat().st_mode & 0o777 == 0o600
    assert config.token not in repr(config)
    record = {"request_id": "req-1", "model": "test"}
    run = {
        "mode": "live",
        "status": "failed",
        "manifest": {"variants": [{"id": "v", "connection": {"model": "different-agent"}}]},
        "jobs": [{"variant_id": "v", "total": 1, "episodes": [{"requests": [record]}]}],
    }
    scheduler = SimpleNamespace(store=store, runs={"r": run}, event=lambda *a: events.append(a))

    def handler(request):
        assert request.url.host == "www.apigo.ai"
        assert request.url.params["workspace_id"] == "workspace"
        if request.url.path.endswith("/logs/list"):
            assert list_fallback
            assert request.url.params["q"] == "req-1"
            return httpx.Response(200, json={"code": 0, "data": {"items": [receipt()["data"]]}})
        assert request.url.path == "/platform/api/v1/logs/detail"
        if list_fallback:
            payload = receipt()
            del payload["data"]["cost_usd_exact"]
            payload["data"]["time"] = "2026-09-09T00:00:00Z"
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=receipt())

    first = asyncio.run(reconcile(scheduler, transport=httpx.MockTransport(handler)))
    second = asyncio.run(
        reconcile(
            scheduler, transport=httpx.MockTransport(lambda r: pytest.fail("duplicate request"))
        )
    )
    assert first["settled_requests"] == 1 and second["settled_requests"] == 0
    assert run["jobs"][0]["cost_usd_exact"] == "1.23E-7"
    assert len(events) == 1 and saved


def test_missing_receipt_does_not_block_other_requests(tmp_path):
    store = SimpleNamespace(secrets=tmp_path, save_run=lambda r: None)
    save_config(store, BillingConfig(workspace_id="workspace", token="private-token-value"))
    missing = {"request_id": "missing"}
    found = {"request_id": "req-1"}
    run = {
        "mode": "live",
        "status": "failed",
        "manifest": {"variants": [{"id": "v", "connection": {"model": "test"}}]},
        "jobs": [{"variant_id": "v", "total": 1, "episodes": [{"requests": [missing, found]}]}],
    }
    scheduler = SimpleNamespace(store=store, runs={"r": run}, event=lambda *a: None)

    def handler(request):
        payload = (
            {"code": 40405, "data": None, "message": "log not found"}
            if request.url.params["request_id"] == "missing"
            else receipt()
        )
        return httpx.Response(200, json=payload)

    result = asyncio.run(reconcile(scheduler, transport=httpx.MockTransport(handler)))
    assert result == {"settled_requests": 1, "pending_requests": 1}
    assert found["billing_status"] == "settled" and "billing_status" not in missing
    assert "cost" not in run["jobs"][0]
