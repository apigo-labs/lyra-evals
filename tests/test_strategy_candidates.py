from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_constraint_led_strategy_is_generic_china_only_and_bounded() -> None:
    strategy = yaml.safe_load(
        (ROOT / "configs" / "strategies" / "accuracy-constraint-led-v1.yaml").read_text()
    )
    assert strategy["runtime_branching"] == "none"
    assert strategy["researcher_models"] == ["glm-5.2", "glm-5.1", "minimax-m3"]
    assert strategy["verifier_model"] == "glm-5.1"
    assert strategy["synthesizer_model"] == "minimax-m3"
    assert strategy["max_attempts"] == 10
    assert strategy["execution_timeout_ms"] == 1_200_000
    prompts = "\n".join(strategy["prompts"].values()).lower()
    for forbidden in (
        "browsecomp",
        "frames",
        "case_id",
        "question id",
        "reference answer",
        "gold answer",
        "claude",
        "gpt",
        "gemini",
        "qwen",
    ):
        assert forbidden not in prompts
    assert all(len(prompt) <= 16_000 for prompt in strategy["prompts"].values())


def test_verifier_search_strategy_is_generic_explicit_and_bounded() -> None:
    strategy = yaml.safe_load(
        (ROOT / "configs" / "strategies" / "accuracy-verifier-search-v2.yaml").read_text()
    )
    assert strategy["runtime_branching"] == "none"
    assert strategy["researcher_models"] == ["glm-5.2", "glm-5.1", "minimax-m3"]
    assert strategy["researcher_adapters"] == ["zhipu_chat", "zhipu_chat", "text_chat"]
    assert strategy["verifier_model"] == "glm-5.1"
    assert strategy["verifier_adapters"] == ["zhipu_chat", "text_chat"]
    assert strategy["synthesizer_model"] == "minimax-m3"
    assert strategy["max_attempts"] == 10
    assert strategy["execution_timeout_ms"] == 1_200_000
    prompts = "\n".join(strategy["prompts"].values()).lower()
    for forbidden in (
        "browsecomp",
        "frames",
        "case_id",
        "question id",
        "reference answer",
        "gold answer",
        "claude",
        "gpt",
        "gemini",
        "qwen",
    ):
        assert forbidden not in prompts
    assert all(len(prompt) <= 16_000 for prompt in strategy["prompts"].values())


def test_provider_health_strategy_is_generic_china_only_and_not_question_routed() -> None:
    strategy = yaml.safe_load(
        (ROOT / "configs" / "strategies" / "accuracy-provider-health-v3.yaml").read_text()
    )
    assert strategy["selection_signal"] == "aggregate_provider_health_only"
    assert strategy["runtime_branching"] == "none"
    assert strategy["researcher_models"] == ["glm-5.2", "glm-5.1", "deepseek-v4-pro"]
    assert strategy["researcher_adapters"] == ["zhipu_chat", "zhipu_chat", "text_chat"]
    assert strategy["verifier_model"] == "glm-5.1"
    assert strategy["verifier_adapters"] == ["zhipu_chat", "text_chat"]
    assert strategy["synthesizer_model"] == "glm-5.2"
    assert strategy["max_attempts"] == 10
    assert strategy["execution_timeout_ms"] == 1_200_000
    prompts = "\n".join(strategy["prompts"].values()).lower()
    for forbidden in (
        "browsecomp",
        "frames",
        "case_id",
        "question id",
        "reference answer",
        "gold answer",
        "claude",
        "gpt",
        "gemini",
        "qwen",
    ):
        assert forbidden not in prompts
    assert all(len(prompt) <= 16_000 for prompt in strategy["prompts"].values())


def test_stage_strength_strategy_changes_roles_without_question_routing() -> None:
    strategy = yaml.safe_load(
        (ROOT / "configs" / "strategies" / "accuracy-stage-strength-v4.yaml").read_text()
    )
    assert strategy["selection_signal"] == "aggregate_stage_reliability_and_role_capability_only"
    assert strategy["runtime_branching"] == "none"
    assert strategy["researcher_models"] == ["glm-5.2", "glm-5.1", "deepseek-v4-pro"]
    assert strategy["verifier_model"] == "glm-5.2"
    assert strategy["verifier_adapters"] == ["zhipu_chat", "text_chat"]
    assert strategy["synthesizer_model"] == "deepseek-v4-pro"
    prompts = "\n".join(strategy["prompts"].values()).lower()
    for forbidden in (
        "browsecomp",
        "frames",
        "case_id",
        "question id",
        "reference answer",
        "gold answer",
        "claude",
        "gpt",
        "gemini",
        "qwen",
    ):
        assert forbidden not in prompts


def test_managed_web_strategy_is_generic_strict_and_role_probe_selected() -> None:
    strategy = yaml.safe_load(
        (ROOT / "configs" / "strategies" / "accuracy-managed-web-v5.yaml").read_text()
    )
    assert strategy["selection_dataset"] == "synthetic-managed-web-role-probe-v1"
    assert strategy["selection_signal"] == "aggregate_action_protocol_and_role_reliability_only"
    assert strategy["runtime_branching"] == "none"
    assert strategy["evidence_mode"] == "strict"
    assert strategy["researcher_models"] == ["glm-5.2", "glm-5.2", "glm-5.1"]
    assert strategy["researcher_adapters"] == ["managed_web", "managed_web", "managed_web"]
    assert strategy["verifier_model"] == "glm-5.1"
    assert strategy["verification_rounds"] == 2
    assert strategy["synthesizer_model"] == "glm-5.1"
    assert strategy["quorum"] == 2
    assert strategy["max_attempts"] == 21
    assert strategy["max_parallel_researchers"] == 3
    assert strategy["execution_timeout_ms"] == 1_200_000
    assert strategy["managed_tools"] == {
        "version": 1,
        "web_search": True,
        "web_fetch": True,
        "max_rounds": 5,
        "max_search_calls": 9,
        "max_fetch_calls": 6,
        "max_results_per_search": 8,
        "search_timeout_ms": 15_000,
        "fetch_timeout_ms": 15_000,
        "max_excerpt_bytes": 4096,
        "max_fetch_bytes": 262_144,
    }
    prompts = "\n".join(strategy["prompts"].values()).lower()
    for forbidden in (
        "browsecomp",
        "frames",
        "case_id",
        "question id",
        "reference answer",
        "gold answer",
        "claude",
        "gpt",
        "gemini",
        "qwen",
    ):
        assert forbidden not in prompts
    assert all(len(prompt) <= 16_000 for prompt in strategy["prompts"].values())
