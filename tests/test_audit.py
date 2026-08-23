from __future__ import annotations

import httpx
import pytest

from vohu_evals.audit import AuditError, PlatformLogsAuditAdapter


def test_platform_audit_reads_reconciled_attempts_usage_and_exact_cost() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/platform/api/v1/logs/detail"
        assert request.url.params["workspace_id"] == "ws_1"
        assert request.url.params["request_id"] == "request-1"
        assert request.url.params["execution_id"] == "exec-1"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "cost_usd": 0.2,
                    "cost_usd_exact": "0.123456",
                    "vohu_execution": {
                        "execution_id": "exec-1",
                        "attempt_count": 2,
                        "usage_reconciled": True,
                        "settlement_status": "settled",
                        "derived_usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "total_tokens": 12,
                        },
                        "attempts": [
                            {"model": "glm-5.2", "usage": {"total_tokens": 5}},
                            {"model": "qwen3.8-max", "usage": {"total_tokens": 7}},
                        ],
                    },
                },
            },
            request=request,
        )

    audit = PlatformLogsAuditAdapter(
        "https://website.example/platform",
        "jwt",
        "ws_1",
        transport=httpx.MockTransport(handler),
    ).resolve("request-1", "exec-1")

    assert audit.attempt_models == ("glm-5.2", "qwen3.8-max")
    assert audit.usage["total_tokens"] == 12
    assert audit.cost_usd == 0.123456
    assert audit.cost_settled is True


def test_platform_audit_can_preserve_unsettled_cost_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "cost_usd": 0,
                    "vohu_execution": {
                        "execution_id": "exec-1",
                        "attempt_count": 1,
                        "usage_reconciled": True,
                        "settlement_status": "pending",
                        "derived_usage": {"total_tokens": 12},
                        "attempts": [{"model": "kimi-k3", "status": "success"}],
                    },
                },
            },
            request=request,
        )

    audit = PlatformLogsAuditAdapter(
        "https://website.example/platform",
        "jwt",
        "ws_1",
        transport=httpx.MockTransport(handler),
        require_settled_cost=False,
    ).resolve("request-1", "exec-1")

    assert audit.cost_usd == 0
    assert audit.cost_settled is False
    assert audit.attempt_models == ("kimi-k3",)


def test_platform_audit_refreshes_once_after_unauthorized() -> None:
    authorizations: list[str] = []
    refreshes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers["Authorization"])
        if len(authorizations) == 1:
            return httpx.Response(401, request=request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "cost_usd": 0,
                    "vohu_execution": {
                        "execution_id": "exec-1",
                        "attempt_count": 0,
                        "usage_reconciled": True,
                        "settlement_status": "settled",
                        "derived_usage": {},
                        "attempts": [],
                    },
                },
            },
            request=request,
        )

    def refresh() -> str:
        nonlocal refreshes
        refreshes += 1
        return "fresh-jwt"

    PlatformLogsAuditAdapter(
        "https://website.example/platform",
        "expired-jwt",
        "ws_1",
        transport=httpx.MockTransport(handler),
        refresh_token=refresh,
    ).resolve("request-1", "exec-1")

    assert refreshes == 1
    assert authorizations == ["Bearer expired-jwt", "Bearer fresh-jwt"]


def test_platform_audit_does_not_refresh_permission_denial() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    audit = PlatformLogsAuditAdapter(
        "https://website.example/platform",
        "jwt",
        "ws_1",
        transport=httpx.MockTransport(handler),
        refresh_token=lambda: pytest.fail("403 must not trigger credential refresh"),
    )

    with pytest.raises(AuditError, match="HTTP 403"):
        audit.resolve("request-1", "exec-1")


def test_platform_audit_sanitizes_refresh_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    def refresh() -> str:
        raise RuntimeError("private-password")

    audit = PlatformLogsAuditAdapter(
        "https://website.example/platform",
        "jwt",
        "ws_1",
        transport=httpx.MockTransport(handler),
        refresh_token=refresh,
    )

    with pytest.raises(AuditError) as raised:
        audit.resolve("request-1", "exec-1")
    assert str(raised.value) == "Platform audit token refresh failed"
    assert raised.value.__cause__ is None
