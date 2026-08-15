from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class CompositionPolicyError(RuntimeError):
    """The actual VOHU composition cannot support a valid benchmark run."""


def validate_china_model_composition(
    allowed_models: frozenset[str], actual_models: Iterable[str], *, require_audit: bool
) -> tuple[str, ...]:
    actual = tuple(actual_models)
    if require_audit and not actual:
        raise CompositionPolicyError("VOHU attempt-model audit is required but missing")
    disallowed = sorted(set(actual).difference(allowed_models))
    if disallowed:
        raise CompositionPolicyError(f"non-allowlisted VOHU models: {', '.join(disallowed)}")
    return actual


def validate_official_composition_snapshot(
    allowed_models: frozenset[str], snapshot: dict[str, Any]
) -> dict[str, tuple[str, ...]]:
    modes = snapshot.get("modes")
    if not isinstance(modes, dict) or not modes:
        raise CompositionPolicyError("official composition snapshot has no modes")
    validated: dict[str, tuple[str, ...]] = {}
    for slug, raw in modes.items():
        if "researcher_models" in raw:
            researchers = raw.get("researcher_models")
            adapters = raw.get("researcher_adapters")
            verifier = str(raw.get("verifier_model", "")).strip()
            verifier_adapters = raw.get("verifier_adapters")
            synthesizer = str(raw.get("synthesizer_model", "")).strip()
            evidence_mode = str(raw.get("evidence_mode", "")).strip()
            schema_version = raw.get("schema_version")
            if (
                schema_version not in {3, 4}
                or evidence_mode not in {"strict", "answer_first"}
                or not isinstance(researchers, list)
                or len(researchers) != 3
                or any(not isinstance(model, str) or not model.strip() for model in researchers)
                or not isinstance(adapters, list)
                or len(adapters) != 3
                or any(
                    not isinstance(adapter, str)
                    or adapter not in {"zhipu_chat", "alibaba_chat", "openai_chat", "text_chat"}
                    for adapter in adapters
                )
                or all(adapter == "text_chat" for adapter in adapters)
                or (evidence_mode == "strict" and "text_chat" in adapters)
                or len({model.strip().lower() for model in researchers}) < 2
                or (schema_version == 3 and verifier_adapters is not None)
                or (
                    schema_version == 4
                    and (
                        not isinstance(verifier_adapters, list)
                        or len(verifier_adapters) not in {1, 2}
                        or verifier_adapters[0] not in {"zhipu_chat", "alibaba_chat", "openai_chat"}
                        or (len(verifier_adapters) == 2 and verifier_adapters[1] != "text_chat")
                    )
                )
                or not verifier
                or not synthesizer
            ):
                raise CompositionPolicyError(f"{slug} has an invalid accuracy composition shape")
            models = (*[model.strip() for model in researchers], verifier, synthesizer)
            validate_china_model_composition(allowed_models, models, require_audit=True)
            validated[str(slug)] = tuple(models)
            continue
        candidates = raw.get("candidate_models") or []
        synthesizer = str(raw.get("synthesizer_model", "")).strip()
        if not synthesizer:
            raise CompositionPolicyError(f"{slug} has no synthesizer")
        models = (*candidates, synthesizer)
        if len(candidates) > 4 or len(set(models)) != len(models):
            raise CompositionPolicyError(f"{slug} has an invalid composition shape")
        validate_china_model_composition(allowed_models, models, require_audit=True)
        validated[str(slug)] = tuple(models)
    return validated


def validate_expected_composition(
    expected_models: frozenset[str], actual_models: Iterable[str]
) -> tuple[str, ...]:
    actual = tuple(actual_models)
    if not expected_models:
        return actual
    actual_set = frozenset(actual)
    if actual_set != expected_models:
        missing = sorted(expected_models.difference(actual_set))
        unexpected = sorted(actual_set.difference(expected_models))
        raise CompositionPolicyError(
            f"official composition mismatch; missing={missing}, unexpected={unexpected}"
        )
    return actual
