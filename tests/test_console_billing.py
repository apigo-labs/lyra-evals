import asyncio
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from vohu_evals.console.billing import (
    aggregate_billing,
    billing_configured,
    invoice,
    reconcile,
)
from vohu_evals.platform_auth import PlatformAuthError


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


class FakeCredentials:
    """Stands in for PlatformCredentialManager without touching env or disk."""

    def __init__(self, tokens):
        self._tokens = list(tokens)
        self.base_url = "https://website.example.com/platform"
        self.calls = []

    def ensure_fresh(self, *, force: bool = False):
        self.calls.append(force)
        if not self._tokens:
            raise PlatformAuthError("no more tokens")
        return self._tokens.pop(0)


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


def test_billing_configured_requires_base_url_workspace_and_a_way_to_authenticate(monkeypatch):
    for name in (
        "VOHU_EVALS_PLATFORM_BASE_URL",
        "VOHU_EVALS_WORKSPACE_ID",
        "VOHU_EVALS_PLATFORM_TOKEN",
        "VOHU_USER_EMAIL",
        "VOHU_USER_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    assert billing_configured() is False
    monkeypatch.setenv("VOHU_EVALS_PLATFORM_BASE_URL", "https://website.example.com/platform")
    monkeypatch.setenv("VOHU_EVALS_WORKSPACE_ID", "workspace")
    assert billing_configured() is False
    monkeypatch.setenv("VOHU_USER_EMAIL", "user@example.com")
    monkeypatch.setenv("VOHU_USER_PASSWORD", "secret")
    assert billing_configured() is True


@pytest.mark.parametrize("list_fallback", [False, True])
def test_reconcile_idempotent_receipts(monkeypatch, list_fallback):
    monkeypatch.setenv("VOHU_EVALS_WORKSPACE_ID", "workspace")
    saved = []
    events = []
    store = SimpleNamespace(save_run=lambda r: saved.append(r.copy()))
    record = {"request_id": "req-1", "model": "test"}
    run = {
        "mode": "live",
        "status": "failed",
        "manifest": {"variants": [{"id": "v", "connection": {"model": "different-agent"}}]},
        "jobs": [{"variant_id": "v", "total": 1, "episodes": [{"requests": [record]}]}],
    }
    scheduler = SimpleNamespace(store=store, runs={"r": run}, event=lambda *a: events.append(a))

    def handler(request):
        assert request.url.host == "website.example.com"
        assert request.url.path.startswith("/platform/api/v1/")
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

    credentials = FakeCredentials(["token-1"])
    first = asyncio.run(
        reconcile(scheduler, transport=httpx.MockTransport(handler), credentials=credentials)
    )
    second = asyncio.run(
        reconcile(
            scheduler,
            transport=httpx.MockTransport(lambda r: pytest.fail("duplicate request")),
            credentials=FakeCredentials(["token-2"]),
        )
    )
    assert first["settled_requests"] == 1 and second["settled_requests"] == 0
    assert run["jobs"][0]["cost_usd_exact"] == "1.23E-7"
    assert len(events) == 1 and saved


def test_missing_receipt_does_not_block_other_requests(monkeypatch):
    monkeypatch.setenv("VOHU_EVALS_WORKSPACE_ID", "workspace")
    store = SimpleNamespace(save_run=lambda r: None)
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

    result = asyncio.run(
        reconcile(
            scheduler,
            transport=httpx.MockTransport(handler),
            credentials=FakeCredentials(["token-1"]),
        )
    )
    assert result == {"settled_requests": 1, "pending_requests": 1}
    assert found["billing_status"] == "settled" and "billing_status" not in missing
    assert "cost" not in run["jobs"][0]


def test_reconcile_requires_workspace_id(monkeypatch):
    monkeypatch.delenv("VOHU_EVALS_WORKSPACE_ID", raising=False)
    scheduler = SimpleNamespace(store=SimpleNamespace(), runs={}, event=lambda *a: None)
    with pytest.raises(ValueError):
        asyncio.run(reconcile(scheduler, credentials=FakeCredentials(["token"])))


def test_reconcile_refreshes_once_on_401_then_stops_on_repeat_401(monkeypatch):
    monkeypatch.setenv("VOHU_EVALS_WORKSPACE_ID", "workspace")
    record = {"request_id": "req-1", "model": "test"}
    run = {
        "mode": "live",
        "status": "failed",
        "manifest": {"variants": [{"id": "v", "connection": {"model": "test"}}]},
        "jobs": [{"variant_id": "v", "total": 1, "episodes": [{"requests": [record]}]}],
    }
    store = SimpleNamespace(save_run=lambda r: None)
    scheduler = SimpleNamespace(store=store, runs={"r": run}, event=lambda *a: None)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"code": 401})

    credentials = FakeCredentials(["stale-token", "still-stale-token"])
    with pytest.raises(ValueError):
        asyncio.run(
            reconcile(scheduler, transport=httpx.MockTransport(handler), credentials=credentials)
        )
    # One initial request plus exactly one retry after a single forced refresh.
    assert len(calls) == 2
    assert credentials.calls == [False, True]


def test_reconcile_stops_immediately_on_403_without_refresh(monkeypatch):
    monkeypatch.setenv("VOHU_EVALS_WORKSPACE_ID", "workspace")
    record = {"request_id": "req-1", "model": "test"}
    run = {
        "mode": "live",
        "status": "failed",
        "manifest": {"variants": [{"id": "v", "connection": {"model": "test"}}]},
        "jobs": [{"variant_id": "v", "total": 1, "episodes": [{"requests": [record]}]}],
    }
    store = SimpleNamespace(save_run=lambda r: None)
    scheduler = SimpleNamespace(store=store, runs={"r": run}, event=lambda *a: None)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403, json={"code": 403})

    credentials = FakeCredentials(["token"])
    with pytest.raises(ValueError):
        asyncio.run(
            reconcile(scheduler, transport=httpx.MockTransport(handler), credentials=credentials)
        )
    assert len(calls) == 1
    assert credentials.calls == [False]
