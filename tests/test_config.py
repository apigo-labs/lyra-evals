from __future__ import annotations

from pathlib import Path

import pytest

from vohu_evals.config import canonical_hash, load_yaml, validate_campaign


def test_canonical_hash_is_order_independent() -> None:
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_campaigns_validate() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in (root / "configs" / "campaigns").glob("*.yaml"):
        validate_campaign(load_yaml(path))


def test_empty_campaign_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one run"):
        validate_campaign(
            {
                "schema_version": "1.0",
                "campaign": "empty",
                "protocol": "openai_chat_completions",
                "gateway_model": "apigo/vohu",
                "runs": [],
                "budget": {"max_usd": 1},
            }
        )


def test_non_chat_campaign_is_rejected() -> None:
    with pytest.raises(ValueError, match="openai_chat_completions"):
        validate_campaign(
            {
                "schema_version": "1.0",
                "campaign": "responses-is-not-a-target-protocol",
                "protocol": "openai_responses",
                "gateway_model": "apigo/vohu",
                "runs": [{"benchmark": "ifeval", "profile": "vohu-quality-v1", "trials": 1}],
                "budget": {"max_usd": 1},
            }
        )
