"""Probe which Gateway protocols a set of models accept, using minimal paid requests.

Each probe sends a 16-token request. Output never includes credentials or request
bodies beyond the model reply text; it is meant to settle "which protocol does this
model ID accept" before a plan reserves budget for a whole batch.

Usage:
  uv run python scripts/probe_lyra_protocols.py                 # reads VOHU_EVALS_API_KEY from .env
  uv run python scripts/probe_lyra_protocols.py --key-file PATH  # explicit key file
  uv run python scripts/probe_lyra_protocols.py --models apigo/lyra-auto gpt-5.6-luna
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vohu_evals.platform_auth import load_local_env  # noqa: E402

DEFAULT_MODELS = ["apigo/lyra-auto", "apigo/lyra-budget", "apigo/lyra-quality"]
PROMPT = "Reply with exactly: OK"

PROTOCOLS = {
    "openai_chat": (
        "/v1/chat/completions",
        lambda m: {"model": m, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": 16},
    ),
    "anthropic_messages": (
        "/v1/messages",
        lambda m: {"model": m, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": 16},
    ),
    "openai_responses": (
        "/v1/responses",
        lambda m: {"model": m, "input": PROMPT, "max_output_tokens": 16},
    ),
}


def reply_text(payload: dict) -> str:
    if payload.get("choices"):
        return str(payload["choices"][0].get("message", {}).get("content") or "")
    if isinstance(payload.get("content"), list):
        return "".join(b.get("text", "") for b in payload["content"] if isinstance(b, dict))
    if payload.get("output_text"):
        return str(payload["output_text"])
    parts = []
    for item in payload.get("output", []) or []:
        for block in item.get("content", []) or []:
            if isinstance(block, dict) and block.get("text"):
                parts.append(block["text"])
    return "".join(parts)


def probe(client: httpx.Client, model: str, protocol: str) -> dict:
    path, build = PROTOCOLS[protocol]
    try:
        response = client.post(path, json=build(model))
    except httpx.HTTPError as exc:
        return {
            "model": model,
            "protocol": protocol,
            "status": "transport",
            "error": str(exc)[:200],
        }
    row = {
        "model": model,
        "protocol": protocol,
        "status": response.status_code,
        "request_id": response.headers.get("x-request-id") or response.headers.get("request-id"),
    }
    try:
        payload = response.json()
    except ValueError:
        row["error"] = response.text[:200]
        return row
    if response.is_success:
        row["served_model"] = payload.get("model")
        row["text"] = reply_text(payload)[:60]
        row["usage"] = payload.get("usage")
    else:
        error = payload.get("error") or payload.get("message") or payload
        row["error"] = json.dumps(error, ensure_ascii=False)[:300]
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--protocols", nargs="+", default=list(PROTOCOLS), choices=list(PROTOCOLS))
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--json", action="store_true", help="print JSON rows instead of a table")
    args = parser.parse_args()

    load_local_env(ROOT / ".env")
    key = (
        args.key_file.read_text().strip() if args.key_file else os.environ.get("VOHU_EVALS_API_KEY")
    )
    if not key:
        print("missing API key: set VOHU_EVALS_API_KEY in .env or pass --key-file", file=sys.stderr)
        return 2
    base_url = (
        args.base_url or os.environ.get("VOHU_EVALS_GATEWAY_BASE_URL") or "https://api.apigo.ai"
    )
    headers = {
        "authorization": f"Bearer {key}",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    rows = []
    with httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=90) as client:
        for model in args.models:
            for protocol in args.protocols:
                rows.append(probe(client, model, protocol))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        usage = json.dumps(row.get("usage"))[:140]
        detail = row.get("error") or (
            f"served={row.get('served_model')} text={row.get('text')!r} usage={usage}"
        )
        print(f"{row['model']:<22} {row['protocol']:<19} {row['status']!s:<9} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
