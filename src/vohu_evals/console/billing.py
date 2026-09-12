"""APIGO account invoices, separate from model credentials and price estimates."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from vohu_evals.platform_auth import PlatformAuthError, PlatformCredentialManager, jwt_is_fresh

REPO_ROOT = Path(__file__).resolve().parents[3]


def billing_configured() -> bool:
    """Whether enough environment is present to attempt platform billing reconciliation."""
    if not os.environ.get("VOHU_EVALS_PLATFORM_BASE_URL") or not os.environ.get(
        "VOHU_EVALS_WORKSPACE_ID"
    ):
        return False
    token_fresh = jwt_is_fresh(os.environ.get("VOHU_EVALS_PLATFORM_TOKEN"))
    has_login = bool(os.environ.get("VOHU_USER_EMAIL")) and bool(
        os.environ.get("VOHU_USER_PASSWORD")
    )
    return token_fresh or has_login


def _credentials(env_path: Path | None = None) -> PlatformCredentialManager:
    base_url = os.environ.get("VOHU_EVALS_PLATFORM_BASE_URL")
    if not base_url:
        raise ValueError("VOHU_EVALS_PLATFORM_BASE_URL 未配置")
    return PlatformCredentialManager(
        base_url,
        os.environ.get("VOHU_EVALS_PLATFORM_TOKEN"),
        os.environ.get("VOHU_USER_EMAIL"),
        os.environ.get("VOHU_USER_PASSWORD"),
        env_path or REPO_ROOT / ".env",
    )


def invoice(payload: dict, request_id: str, model: str) -> Decimal | None:
    data = payload.get("data", {})
    if payload.get("code") != 0 or data.get("id") != request_id or data.get("model") != model:
        raise ValueError("账单的请求 ID 或模型不匹配")
    if data.get("settled") is not True:
        return None
    try:
        amount = Decimal(str(data["cost_usd_exact"]))
    except (KeyError, InvalidOperation) as exc:
        raise ValueError("账单缺少精确费用") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("账单金额无效")
    return amount


def aggregate_billing(job):
    for episode in job.get("episodes", []):
        requests = episode.get("requests")
        if requests is None:
            continue
        if all(q.get("billing_status") == "settled" for q in requests):
            cost = sum((Decimal(q["billed_cost_usd_exact"]) for q in requests), Decimal(0))
            episode.update(cost_usd=float(cost), cost_usd_exact=str(cost), billing_status="settled")
    episodes = job.get("episodes", [])
    if len(episodes) == job["total"] and all(
        e.get("billing_status") == "settled" for e in episodes
    ):
        cost = sum((Decimal(e["cost_usd_exact"]) for e in episodes), Decimal(0))
        job.update(cost=float(cost), cost_usd_exact=str(cost), billing_status="settled")


async def reconcile(
    scheduler, *, transport=None, credentials: PlatformCredentialManager | None = None
) -> dict:
    workspace_id = os.environ.get("VOHU_EVALS_WORKSPACE_ID")
    if not workspace_id:
        raise ValueError(
            "请先配置 VOHU_EVALS_PLATFORM_BASE_URL 与 VOHU_EVALS_WORKSPACE_ID；"
            "模型 API Key 无法代替"
        )
    manager = credentials or _credentials()
    try:
        token = manager.ensure_fresh()
    except PlatformAuthError as exc:
        raise ValueError(str(exc)) from exc
    updated = 0
    pending = 0
    refreshed = False
    async with httpx.AsyncClient(
        base_url=manager.base_url + "/api/v1",
        timeout=20,
        transport=transport,
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    ) as client:

        async def get(path: str, params: dict) -> httpx.Response:
            nonlocal token, refreshed
            response = await client.get(path, params=params)
            if response.status_code == 401 and not refreshed:
                try:
                    token = manager.ensure_fresh(force=True)
                except PlatformAuthError as exc:
                    raise ValueError(str(exc)) from exc
                refreshed = True
                client.headers["Authorization"] = f"Bearer {token}"
                response = await client.get(path, params=params)
            if response.status_code in {401, 403}:
                raise ValueError("平台账单凭据失效或缺少工作区访问权限")
            return response

        for run in scheduler.runs.values():
            if run.get("mode") != "live" or run["status"] in {"queued", "running"}:
                continue
            before = json.dumps(run, sort_keys=True)
            variants = {v["id"]: v for v in run["manifest"].get("variants", [])}
            for job in run["jobs"]:
                model = variants.get(job.get("variant_id"), {}).get("connection", {}).get("model")
                for episode in job.get("episodes", []):
                    for record in episode.get("requests", []):
                        if record.get("billing_status") == "settled":
                            continue
                        if not record.get("request_id") or not model:
                            pending += 1
                            continue
                        response = await get(
                            "logs/detail",
                            params={
                                "workspace_id": workspace_id,
                                "request_id": record["request_id"],
                            },
                        )
                        response.raise_for_status()
                        payload = response.json()
                        if payload.get("code") == 40405:
                            pending += 1
                            continue
                        detail = payload.get("data") or {}
                        if (
                            payload.get("code") == 0
                            and detail.get("id") == record["request_id"]
                            and detail.get("settled") is True
                            and "cost_usd_exact" not in detail
                        ):
                            # The deployed detail projection omits exact money; the list
                            # projection retains it. Never replace it with rounded floats.
                            stamp = datetime.fromisoformat(detail["time"].replace("Z", "+00:00"))
                            listing = await get(
                                "logs/list",
                                params={
                                    "workspace_id": workspace_id,
                                    "q": record["request_id"],
                                    "time": "custom",
                                    "from": (stamp - timedelta(seconds=1)).isoformat(),
                                    "to": (stamp + timedelta(seconds=1)).isoformat(),
                                    "page_size": 100,
                                },
                            )
                            listing.raise_for_status()
                            listed = listing.json()
                            matches = [
                                item
                                for item in (listed.get("data") or {}).get("items", [])
                                if item.get("id") == record["request_id"]
                            ]
                            if listed.get("code") != 0 or len(matches) != 1:
                                pending += 1
                                continue
                            payload = {"code": 0, "data": matches[0]}
                        cost = invoice(payload, record["request_id"], record.get("model", model))
                        if cost is None:
                            pending += 1
                        else:
                            record.update(billing_status="settled", billed_cost_usd_exact=str(cost))
                            updated += 1
                        # Persist each receipt; subsequent requests may fail or be cancelled.
                        aggregate_billing(job)
                        scheduler.store.save_run(run)
                aggregate_billing(job)
            if json.dumps(run, sort_keys=True) != before:
                scheduler.event(run, "账单同步完成；未结算请求继续保留预留")
    return {"settled_requests": updated, "pending_requests": pending}


async def billing_loop(scheduler):
    while True:
        await asyncio.sleep(60)
        if billing_configured():
            try:
                async with scheduler.billing_lock:
                    await reconcile(scheduler)
                scheduler.billing_error = None
            except (ValueError, OSError, httpx.HTTPError):
                scheduler.billing_error = "账单同步失败，请检查平台令牌、工作区权限或网络"
