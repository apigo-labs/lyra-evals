"""Export the Platform request log (`logs/list`) for a date range, for cost attribution.

Platform bills every Gateway request as its own line item, including the Fusion routing models
(`apigo/lyra-*`), so this export is the authoritative cost source for `scripts/inspect_report.py`.
Credentials come from `.env` through the same manager the Console billing path uses; the token is
never printed or written to the export.

Usage:
  uv run python scripts/platform_logs_export.py --out .local/platform-logs-today.json
  uv run python scripts/platform_logs_export.py --from 2026-09-12T00:00:00+00:00 \
      --to 2026-09-13T00:00:00+00:00 --out .local/platform-logs.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from vohu_evals.platform_auth import PlatformAuthError, PlatformCredentialManager, load_local_env

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE_SIZE = 200
MAX_PAGES = 200

# Only billing-relevant, non-sensitive fields leave the Platform response.
KEPT_FIELDS = [
    "id",
    "time",
    "request_path",
    "model",
    "duration_ms",
    "cost_usd_exact",
    "settled",
    "billing_status",
    "ok",
    "error_code",
    "tokens",
]


def credentials(env_path: Path | None = None) -> PlatformCredentialManager:
    """Same construction as `vohu_evals.console.billing._credentials`, env loaded from `.env`."""
    path = env_path or REPO_ROOT / ".env"
    load_local_env(path)
    base_url = os.environ.get("VOHU_EVALS_PLATFORM_BASE_URL")
    if not base_url:
        raise ValueError("VOHU_EVALS_PLATFORM_BASE_URL 未配置")
    return PlatformCredentialManager(
        base_url,
        os.environ.get("VOHU_EVALS_PLATFORM_TOKEN"),
        os.environ.get("VOHU_USER_EMAIL"),
        os.environ.get("VOHU_USER_PASSWORD"),
        path,
    )


def window_params(time_range: str, start: str | None, end: str | None) -> dict:
    if time_range == "custom":
        if not start or not end:
            raise ValueError("custom 时间窗需要同时提供 --from 与 --to")
        return {"time": "custom", "from": start, "to": end}
    return {"time": time_range}


def fetch(
    manager: PlatformCredentialManager,
    workspace_id: str,
    params: dict,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[dict]:
    token = manager.ensure_fresh()
    items: list[dict] = []
    seen: set[str] = set()
    refreshed = False
    # The deployed endpoint caps page_size below what we ask for, so the first page's length,
    # not the requested size, decides when the listing is exhausted.
    effective_size: int | None = None
    with httpx.Client(
        base_url=manager.base_url + "/api/v1",
        timeout=30,
        transport=transport,
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    ) as client:
        for page in range(1, MAX_PAGES + 1):
            query = {"workspace_id": workspace_id, "page": page, "page_size": PAGE_SIZE, **params}
            response = client.get("logs/list", params=query)
            if response.status_code == 401 and not refreshed:
                refreshed = True
                client.headers["Authorization"] = f"Bearer {manager.ensure_fresh(force=True)}"
                response = client.get("logs/list", params=query)
            if response.status_code in {401, 403}:
                raise ValueError("平台凭据失效或缺少工作区访问权限")
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise ValueError("平台日志接口返回了错误码")
            batch = (payload.get("data") or {}).get("items") or []
            if effective_size is None:
                effective_size = len(batch)
            for entry in batch:
                identifier = entry.get("id")
                if identifier in seen:
                    continue
                seen.add(identifier)
                items.append({field: entry.get(field) for field in KEPT_FIELDS})
            if not batch or len(batch) < effective_size:
                break
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--time", default="today", help="today / custom（配合 --from、--to）")
    parser.add_argument("--from", dest="start", default=None, help="ISO 起点（custom 时必填）")
    parser.add_argument("--to", dest="end", default=None, help="ISO 终点（custom 时必填）")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / ".local/platform-logs-today.json")
    args = parser.parse_args(argv)

    workspace_id = os.environ.get("VOHU_EVALS_WORKSPACE_ID")
    try:
        manager = credentials()
        workspace_id = workspace_id or os.environ.get("VOHU_EVALS_WORKSPACE_ID")
        if not workspace_id:
            raise ValueError("VOHU_EVALS_WORKSPACE_ID 未配置")
        items = fetch(manager, workspace_id, window_params(args.time, args.start, args.end))
    except (ValueError, PlatformAuthError, httpx.HTTPError) as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    print(f"{len(items)} 条账单记录 -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
