from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

import httpx
import pytest

from vohu_evals.platform_auth import (
    PlatformAuthError,
    PlatformCredentialManager,
    jwt_is_fresh,
    load_local_env,
)


def _jwt(expires_at: int) -> str:
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": expires_at}).encode()).decode().rstrip("=")
    )
    return f"header.{payload}.signature"


def test_jwt_freshness_uses_expiry_with_leeway() -> None:
    assert jwt_is_fresh(_jwt(1_200), now=1_000, leeway_seconds=60)
    assert not jwt_is_fresh(_jwt(1_050), now=1_000, leeway_seconds=60)
    assert not jwt_is_fresh("not-a-jwt", now=1_000)


def test_local_env_only_fills_missing_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# local only\nEXPLICIT=file-value\nexport FROM_FILE='password=with-symbols'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXPLICIT", "process-value")
    monkeypatch.delenv("FROM_FILE", raising=False)

    load_local_env(env_path)

    assert os.environ["EXPLICIT"] == "process-value"
    assert os.environ["FROM_FILE"] == "password=with-symbols"


def test_login_refreshes_env_atomically_without_changing_other_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "KEEP=value\nexport VOHU_EVALS_PLATFORM_TOKEN = 'expired'\n", encoding="utf-8"
    )
    new_token = _jwt(2_000)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/platform/api/v1/auth/login"
        assert json.loads(request.content) == {
            "email": "user@example.com",
            "password": "private-password",
            "remember": True,
        }
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "authentication_state": "authenticated",
                    "access_token": new_token,
                },
            },
            request=request,
        )

    monkeypatch.delenv("VOHU_EVALS_PLATFORM_TOKEN", raising=False)
    manager = PlatformCredentialManager(
        "http://app.localhost/platform",
        "expired",
        "user@example.com",
        "private-password",
        env_path,
        transport=httpx.MockTransport(handler),
        clock=lambda: 1_000,
    )

    assert manager.ensure_fresh() == new_token
    assert env_path.read_text(encoding="utf-8") == (
        f"KEEP=value\nVOHU_EVALS_PLATFORM_TOKEN={new_token}\n"
    )
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert manager.ensure_fresh() == new_token


def test_mfa_fails_closed_without_writing_token(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("VOHU_EVALS_PLATFORM_TOKEN=expired\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 0, "data": {"authentication_state": "mfa_required"}},
            request=request,
        )

    manager = PlatformCredentialManager(
        "http://app.localhost/platform",
        "expired",
        "user@example.com",
        "private-password",
        env_path,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(PlatformAuthError, match="requires MFA"):
        manager.ensure_fresh()
    assert env_path.read_text(encoding="utf-8") == "VOHU_EVALS_PLATFORM_TOKEN=expired\n"


def test_invalid_login_token_is_not_persisted(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "authentication_state": "authenticated",
                    "access_token": "invalid-token",
                },
            },
            request=request,
        )

    manager = PlatformCredentialManager(
        "http://app.localhost/platform",
        None,
        "user@example.com",
        "private-password",
        env_path,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(PlatformAuthError, match="expired or invalid"):
        manager.ensure_fresh()
    assert not env_path.exists()


def test_login_failure_does_not_leak_credentials_or_response_body(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": "private-password", "token": "server-secret"},
            request=request,
        )

    manager = PlatformCredentialManager(
        "http://app.localhost/platform",
        None,
        "user@example.com",
        "private-password",
        tmp_path / ".env",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(PlatformAuthError) as raised:
        manager.ensure_fresh()
    message = str(raised.value)
    assert "private-password" not in message
    assert "server-secret" not in message
    assert not (tmp_path / ".env").exists()
