from __future__ import annotations

import pytest

from vohu_evals.policy import (
    CompositionPolicyError,
    validate_china_model_composition,
    validate_expected_composition,
    validate_official_composition_snapshot,
)


def test_allows_only_explicit_models() -> None:
    assert validate_china_model_composition(
        frozenset({"qwen", "deepseek"}), ["qwen", "deepseek"], require_audit=True
    ) == ("qwen", "deepseek")


def test_rejects_non_china_model() -> None:
    with pytest.raises(CompositionPolicyError, match="non-allowlisted"):
        validate_china_model_composition(
            frozenset({"qwen"}), ["qwen", "gpt-5.6-sol"], require_audit=True
        )


def test_requires_attempt_audit() -> None:
    with pytest.raises(CompositionPolicyError, match="audit is required"):
        validate_china_model_composition(frozenset({"qwen"}), [], require_audit=True)


def test_validates_official_snapshot_against_allowlist() -> None:
    snapshot = {
        "modes": {
            "apigo/vohu-quality": {
                "candidate_models": ["glm", "kimi"],
                "synthesizer_model": "qwen",
            }
        }
    }
    assert validate_official_composition_snapshot(frozenset({"glm", "kimi", "qwen"}), snapshot) == {
        "apigo/vohu-quality": ("glm", "kimi", "qwen")
    }


def test_validates_accuracy_research_snapshot_with_independent_model_reuse() -> None:
    snapshot = {
        "modes": {
            "apigo/vohu-research": {
                "version": 2,
                "schema_version": 3,
                "evidence_mode": "answer_first",
                "researcher_models": ["glm-5.2", "glm-5.1", "glm-5.2"],
                "researcher_adapters": ["zhipu_chat", "zhipu_chat", "zhipu_chat"],
                "verifier_model": "kimi-k3",
                "synthesizer_model": "minimax-m3",
            }
        }
    }
    allowed = frozenset({"glm-5.2", "glm-5.1", "kimi-k3", "minimax-m3"})

    assert validate_official_composition_snapshot(allowed, snapshot) == {
        "apigo/vohu-research": (
            "glm-5.2",
            "glm-5.1",
            "glm-5.2",
            "kimi-k3",
            "minimax-m3",
        )
    }


def test_validates_schema_four_verifier_search_snapshot() -> None:
    snapshot = {
        "modes": {
            "apigo/vohu-research": {
                "schema_version": 4,
                "evidence_mode": "answer_first",
                "researcher_models": ["glm-a", "glm-b", "minimax-s"],
                "researcher_adapters": ["zhipu_chat", "zhipu_chat", "text_chat"],
                "verifier_model": "glm-b",
                "verifier_adapters": ["zhipu_chat", "text_chat"],
                "synthesizer_model": "minimax-s",
            }
        }
    }

    assert validate_official_composition_snapshot(
        frozenset({"glm-a", "glm-b", "minimax-s"}), snapshot
    ) == {
        "apigo/vohu-research": (
            "glm-a",
            "glm-b",
            "minimax-s",
            "glm-b",
            "minimax-s",
        )
    }


def test_rejects_foreign_model_in_official_snapshot() -> None:
    snapshot = {
        "modes": {
            "apigo/vohu-quality": {
                "candidate_models": ["glm", "gpt-5.6-sol"],
                "synthesizer_model": "qwen",
            }
        }
    }
    with pytest.raises(CompositionPolicyError, match=r"gpt-5\.6-sol"):
        validate_official_composition_snapshot(frozenset({"glm", "qwen"}), snapshot)


def test_rejects_profile_composition_mismatch() -> None:
    with pytest.raises(CompositionPolicyError, match="composition mismatch"):
        validate_expected_composition(
            frozenset({"glm-5.2", "kimi-k3", "qwen3.8-max"}),
            ("glm-5.2", "qwen3.8-max"),
        )
