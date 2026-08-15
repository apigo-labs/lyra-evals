from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_campaign(data: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "campaign",
        "protocol",
        "gateway_model",
        "runs",
        "budget",
    }
    missing = required.difference(data)
    if missing:
        raise ValueError(f"campaign missing fields: {sorted(missing)}")
    if not data["runs"]:
        raise ValueError("campaign must contain at least one run")
    if data["protocol"] != "openai_chat_completions":
        raise ValueError("campaign protocol must be openai_chat_completions")
    if not isinstance(data["gateway_model"], str) or not data["gateway_model"].strip():
        raise ValueError("campaign gateway_model must be a non-empty string")
    for run in data["runs"]:
        if set(run) < {"benchmark", "profile", "trials"}:
            raise ValueError("each campaign run requires benchmark, profile and trials")
