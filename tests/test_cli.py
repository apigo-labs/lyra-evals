from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
import yaml

from vohu_evals.cli import (
    PLATFORM_LOGIN_ENV,
    REQUIRED_EXECUTION_ENV,
    _doctor,
    _preflight,
    _profile_policy,
    _target_validation_failures,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_local_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    targets = tmp_path / "targets"
    targets.mkdir()
    allowed_models = {"glm-5.1", "glm-5.2", "kimi-k3", "minimax-m3"}
    snapshots: dict[str, dict] = {}
    for path in sorted((ROOT / "configs" / "profiles").glob("*.yaml")):
        profile = yaml.safe_load(path.read_text(encoding="utf-8"))
        if profile.get("composition_source") == "workspace_custom":
            allowed_models.update(profile.get("candidate_models", []))
            allowed_models.add(profile["synthesizer_model"])
            continue
        snapshot_name = profile["composition_snapshot"]
        snapshot = snapshots.setdefault(snapshot_name, {"snapshot_id": snapshot_name, "modes": {}})
        mode = profile["official_mode"]
        version = int(profile["expected_version"])
        if mode == "apigo/vohu-research":
            if version <= 5:
                models = ("glm-5.2", "kimi-k3", "minimax-m3")
            elif version <= 9:
                models = ("glm-5.2", "glm-5.1", "minimax-m3")
            else:
                models = ("glm-5.2", "glm-5.1", "deepseek-v4-pro")
            allowed_models.update(models)
            snapshot["modes"][mode] = {
                "version": version,
                "snapshot_hash": profile["expected_snapshot_hash"],
                "schema_version": profile["expected_schema_version"],
                "evidence_mode": profile["expected_evidence_mode"],
                "researcher_models": [models[0], models[1], models[0]],
                "verifier_model": models[1],
                "synthesizer_model": models[2],
            }
        else:
            candidate = f"test-{mode.rsplit('/', 1)[-1]}-candidate"
            synthesizer = f"test-{mode.rsplit('/', 1)[-1]}-synthesizer"
            allowed_models.update((candidate, synthesizer))
            snapshot["modes"][mode] = {
                "version": version,
                "candidate_models": [candidate],
                "synthesizer_model": synthesizer,
            }
    (targets / "china-models-v1.yaml").write_text(
        yaml.safe_dump({"models": [{"model_id": model} for model in sorted(allowed_models)]}),
        encoding="utf-8",
    )
    for name, snapshot in snapshots.items():
        (targets / f"{name}.yaml").write_text(yaml.safe_dump(snapshot), encoding="utf-8")
    monkeypatch.setenv("VOHU_EVALS_TARGETS_DIR", str(targets))


def test_missing_local_targets_do_not_block_offline_validation(tmp_path: Path) -> None:
    assert _target_validation_failures(tmp_path / "absent-targets") == []


def _fresh_jwt() -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 3600}).encode())
    return f"header.{payload.decode().rstrip('=')}.signature"


def test_preflight_defaults_to_no_network(capsys: pytest.CaptureFixture[str]) -> None:
    result = _preflight(
        ROOT / "configs" / "campaigns" / "web-research-v1.yaml",
        "vohu-research-v1",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["network_call"] is False
    assert payload["requires_web_search"] is True
    assert payload["requires_citations"] is True
    assert payload["expected_models"] == ["glm-5.1", "glm-5.2", "minimax-m3"]


def test_browsecomp_preflight_does_not_require_observable_citations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _preflight(
        ROOT / "configs" / "campaigns" / "browsecomp-v1.yaml",
        "vohu-research-v1",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["requires_web_search"] is True
    assert payload["requires_citations"] is False


def test_preflight_rejects_campaign_profile_model_mismatch() -> None:
    with pytest.raises(ValueError, match="gateway model"):
        _preflight(
            ROOT / "configs" / "campaigns" / "web-research-v1.yaml",
            "vohu-quality-v1",
            execute=False,
            confirmed_budget=None,
        )


def test_executing_preflight_requires_exact_budget() -> None:
    with pytest.raises(ValueError, match="exactly match"):
        _preflight(
            ROOT / "configs" / "campaigns" / "text-v1.yaml",
            "vohu-quality-v1",
            execute=True,
            confirmed_budget=0,
        )


def test_workspace_custom_profile_freezes_gateway_model_and_china_composition() -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v1"
    )

    assert identity == "vohu/research-benchmark"
    assert gateway_model == "vohu/research-benchmark@v1"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "glm-5.1", "minimax-m3"})
    assert expected_models.issubset(allowed)


def test_workspace_custom_research_v2_adds_cross_provider_fallback() -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v2"
    )

    assert identity == "vohu/research-benchmark"
    assert gateway_model == "vohu/research-benchmark@v2"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "kimi-k3", "minimax-m3"})
    assert expected_models.issubset(allowed)


def test_browsecomp_v2_campaign_freezes_research_v2(capsys: pytest.CaptureFixture[str]) -> None:
    result = _preflight(
        ROOT / "configs" / "campaigns" / "browsecomp-v2.yaml",
        "vohu-research-v2",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["gateway_model"] == "vohu/research-benchmark@v2"
    assert payload["expected_models"] == ["glm-5.2", "kimi-k3", "minimax-m3"]
    assert payload["requires_citations"] is False


def test_browsecomp_v3_campaign_freezes_platform_accuracy_snapshot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v3"
    )
    assert identity == "apigo/vohu-research"
    assert gateway_model == "apigo/vohu-research"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "kimi-k3", "minimax-m3"})
    assert expected_models.issubset(allowed)

    result = _preflight(
        ROOT / "configs" / "campaigns" / "browsecomp-v3.yaml",
        "vohu-research-v3",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["gateway_model"] == "apigo/vohu-research"
    assert payload["expected_models"] == ["glm-5.2", "kimi-k3", "minimax-m3"]
    assert payload["requires_citations"] is False


def test_browsecomp_v4_campaign_freezes_long_timeout_platform_snapshot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v4"
    )
    assert identity == "apigo/vohu-research"
    assert gateway_model == "apigo/vohu-research"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "kimi-k3", "minimax-m3"})
    assert expected_models.issubset(allowed)

    result = _preflight(
        ROOT / "configs" / "campaigns" / "browsecomp-v4.yaml",
        "vohu-research-v4",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["gateway_model"] == "apigo/vohu-research"
    assert payload["expected_models"] == ["glm-5.2", "kimi-k3", "minimax-m3"]
    assert payload["requires_citations"] is False
    assert payload["preflight_max_usd"] == 5


def test_browsecomp_v5_campaign_freezes_researcher_retry_budget(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v5"
    )
    assert identity == "apigo/vohu-research"
    assert gateway_model == "apigo/vohu-research"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "kimi-k3", "minimax-m3"})
    assert expected_models.issubset(allowed)

    result = _preflight(
        ROOT / "configs" / "campaigns" / "browsecomp-v5.yaml",
        "vohu-research-v5",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["gateway_model"] == "apigo/vohu-research"
    assert payload["expected_models"] == ["glm-5.2", "kimi-k3", "minimax-m3"]
    assert payload["requires_citations"] is False
    assert payload["preflight_max_usd"] == 5


def test_browsecomp_v6_campaign_freezes_stage_retry_strategy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v6"
    )
    assert identity == "apigo/vohu-research"
    assert gateway_model == "apigo/vohu-research"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "glm-5.1", "minimax-m3"})
    assert expected_models.issubset(allowed)

    result = _preflight(
        ROOT / "configs" / "campaigns" / "browsecomp-v6.yaml",
        "vohu-research-v6",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["gateway_model"] == "apigo/vohu-research"
    assert payload["expected_models"] == ["glm-5.1", "glm-5.2", "minimax-m3"]
    assert payload["requires_citations"] is False
    assert payload["preflight_max_usd"] == 5


def test_browsecomp_v7_campaign_freezes_twenty_minute_execution_window(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v7"
    )
    assert identity == "apigo/vohu-research"
    assert gateway_model == "apigo/vohu-research"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "glm-5.1", "minimax-m3"})
    assert expected_models.issubset(allowed)

    result = _preflight(
        ROOT / "configs" / "campaigns" / "browsecomp-v7.yaml",
        "vohu-research-v7",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["expected_models"] == ["glm-5.1", "glm-5.2", "minimax-m3"]
    assert payload["requires_citations"] is False


def test_frames_development_campaign_freezes_constraint_led_snapshot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v8"
    )
    assert identity == "apigo/vohu-research"
    assert gateway_model == "apigo/vohu-research"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "glm-5.1", "minimax-m3"})
    assert expected_models.issubset(allowed)

    result = _preflight(
        ROOT / "configs" / "campaigns" / "frames-development-v1.yaml",
        "vohu-research-v8",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["expected_models"] == ["glm-5.1", "glm-5.2", "minimax-m3"]
    assert payload["requires_web_search"] is True
    assert payload["requires_citations"] is False


def test_frames_development_v2_freezes_verifier_search_snapshot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v9"
    )
    assert identity == "apigo/vohu-research"
    assert gateway_model == "apigo/vohu-research"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "glm-5.1", "minimax-m3"})
    assert expected_models.issubset(allowed)

    result = _preflight(
        ROOT / "configs" / "campaigns" / "frames-development-v2.yaml",
        "vohu-research-v9",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["expected_models"] == ["glm-5.1", "glm-5.2", "minimax-m3"]
    assert payload["requires_web_search"] is True
    assert payload["requires_citations"] is False


def test_frames_development_v3_freezes_provider_health_snapshot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v10"
    )
    assert identity == "apigo/vohu-research"
    assert gateway_model == "apigo/vohu-research"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "glm-5.1", "deepseek-v4-pro"})
    assert expected_models.issubset(allowed)

    result = _preflight(
        ROOT / "configs" / "campaigns" / "frames-development-v3.yaml",
        "vohu-research-v10",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["expected_models"] == ["deepseek-v4-pro", "glm-5.1", "glm-5.2"]
    assert payload["requires_web_search"] is True
    assert payload["requires_citations"] is False


def test_frames_development_v4_freezes_stage_strength_snapshot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v11"
    )
    assert identity == "apigo/vohu-research"
    assert gateway_model == "apigo/vohu-research"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "glm-5.1", "deepseek-v4-pro"})
    assert expected_models.issubset(allowed)

    result = _preflight(
        ROOT / "configs" / "campaigns" / "frames-development-v4.yaml",
        "vohu-research-v11",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["expected_models"] == ["deepseek-v4-pro", "glm-5.1", "glm-5.2"]
    assert payload["requires_web_search"] is True
    assert payload["requires_citations"] is False


def test_frames_development_v5_freezes_dual_verification_snapshot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        ROOT, "vohu-research-v12"
    )
    assert identity == "apigo/vohu-research"
    assert gateway_model == "apigo/vohu-research"
    assert gateway_protocol == "openai_chat_completions"
    assert expected_models == frozenset({"glm-5.2", "glm-5.1", "deepseek-v4-pro"})
    assert expected_models.issubset(allowed)
    result = _preflight(
        ROOT / "configs" / "campaigns" / "frames-development-v5.yaml",
        "vohu-research-v12",
        execute=False,
        confirmed_budget=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["expected_models"] == ["deepseek-v4-pro", "glm-5.1", "glm-5.2"]
    assert payload["requires_web_search"] is True


def test_doctor_reports_presence_without_leaking_credentials(monkeypatch, capsys) -> None:
    monkeypatch.setattr("vohu_evals.cli.benchmark_readiness", lambda *_: [])
    for name in REQUIRED_EXECUTION_ENV:
        monkeypatch.setenv(name, "do-not-print-this-secret")
    monkeypatch.setenv("VOHU_EVALS_PLATFORM_TOKEN", _fresh_jwt())
    for name in PLATFORM_LOGIN_ENV:
        monkeypatch.delenv(name, raising=False)

    assert _doctor("ifeval") == 0
    output = capsys.readouterr().out

    assert "do-not-print-this-secret" not in output
    assert '"can_execute": true' in output
    assert '"platform_token_fresh": true' in output


def test_doctor_accepts_login_fallback_for_expired_token(monkeypatch, capsys) -> None:
    monkeypatch.setattr("vohu_evals.cli.benchmark_readiness", lambda *_: [])
    for name in REQUIRED_EXECUTION_ENV:
        monkeypatch.setenv(name, "configured")
    for name in PLATFORM_LOGIN_ENV:
        monkeypatch.setenv(name, "private-credential")

    assert _doctor("ifeval") == 0
    output = capsys.readouterr().out

    assert "private-credential" not in output
    assert '"platform_token_fresh": false' in output
    assert '"can_execute": true' in output
