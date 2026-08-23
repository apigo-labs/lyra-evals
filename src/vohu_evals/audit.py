from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class AuditError(RuntimeError):
    """Platform could not provide settled VOHU execution evidence."""


@dataclass(frozen=True)
class ExecutionAudit:
    execution_id: str
    attempts: tuple[dict[str, Any], ...]
    usage: dict[str, Any]
    cost_usd: float
    cost_settled: bool = True

    @property
    def attempt_models(self) -> tuple[str, ...]:
        return tuple(
            str(attempt["model"])
            for attempt in self.attempts
            if isinstance(attempt, dict) and attempt.get("model")
        )


class AuditPort(Protocol):
    def resolve(self, request_id: str, execution_id: str) -> ExecutionAudit: ...


class PlatformLogsAuditAdapter:
    """Reads audit evidence outside the official Gateway response contract."""

    def __init__(
        self,
        base_url: str,
        token: str,
        workspace_id: str,
        *,
        timeout_seconds: float = 60,
        poll_interval_seconds: float = 1,
        transport: httpx.BaseTransport | None = None,
        refresh_token: Callable[[], str] | None = None,
        require_settled_cost: bool = True,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            transport=transport,
        )
        self._workspace_id = workspace_id
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._refresh_token = refresh_token
        self._require_settled_cost = require_settled_cost

    def resolve(self, request_id: str, execution_id: str) -> ExecutionAudit:
        if not request_id or not execution_id:
            raise AuditError("Gateway response lacks request or execution identity")
        deadline = time.monotonic() + self._timeout_seconds
        last_reason = "audit record not visible"
        authorization_refreshed = False
        while time.monotonic() < deadline:
            response = self._client.get(
                "/api/v1/logs/detail",
                params={
                    "workspace_id": self._workspace_id,
                    "request_id": request_id,
                    "execution_id": execution_id,
                },
            )
            if (
                response.status_code == 401
                and not authorization_refreshed
                and self._refresh_token is not None
            ):
                try:
                    token = self._refresh_token()
                except Exception:
                    raise AuditError("Platform audit token refresh failed") from None
                self._client.headers["Authorization"] = f"Bearer {token}"
                authorization_refreshed = True
                continue
            if response.status_code in {401, 403}:
                raise AuditError(
                    f"Platform audit authorization failed: HTTP {response.status_code}"
                )
            if response.status_code >= 500:
                last_reason = f"Platform audit HTTP {response.status_code}"
                time.sleep(self._poll_interval_seconds)
                continue
            payload = response.json()
            data = payload.get("data") if payload.get("code") == 0 else None
            execution = data.get("vohu_execution") if isinstance(data, dict) else None
            if not isinstance(execution, dict):
                last_reason = "VOHU execution audit is not visible"
                time.sleep(self._poll_interval_seconds)
                continue
            if execution.get("execution_id") != execution_id:
                raise AuditError("Platform audit execution identity mismatch")
            if not execution.get("usage_reconciled"):
                last_reason = "VOHU execution usage is not reconciled"
                time.sleep(self._poll_interval_seconds)
                continue
            attempts = execution.get("attempts")
            if not isinstance(attempts, list) or len(attempts) != execution.get("attempt_count"):
                last_reason = "VOHU attempt audit is incomplete"
                time.sleep(self._poll_interval_seconds)
                continue
            usage = execution.get("derived_usage")
            if not isinstance(usage, dict):
                raise AuditError("Platform audit derived usage is unavailable")
            if execution.get("settlement_status") != "settled":
                if self._require_settled_cost:
                    last_reason = "VOHU execution cost is not settled"
                    time.sleep(self._poll_interval_seconds)
                    continue
                return ExecutionAudit(execution_id, tuple(attempts), usage, 0.0, False)
            exact_cost = data.get("cost_usd_exact")
            try:
                cost_usd = float(exact_cost if exact_cost is not None else data["cost_usd"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AuditError("Platform audit cost is unavailable") from exc
            return ExecutionAudit(execution_id, tuple(attempts), usage, cost_usd, True)
        raise AuditError(f"Platform audit timed out: {last_reason}")
