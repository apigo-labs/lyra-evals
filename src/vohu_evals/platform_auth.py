from __future__ import annotations

import base64
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx


class PlatformAuthError(RuntimeError):
    """Platform credentials could not produce a usable audit token."""


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_local_env(env_path: Path) -> None:
    """Load local dotenv values without overriding explicitly supplied environment values."""
    if not env_path.exists():
        return
    if env_path.is_symlink():
        raise PlatformAuthError("Refusing to load a symlinked .env file")
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PlatformAuthError("Could not read the local .env file") from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise PlatformAuthError("Local .env contains an invalid entry")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not _ENV_NAME.fullmatch(name):
            raise PlatformAuthError("Local .env contains an invalid variable name")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def jwt_is_fresh(token: str | None, *, now: float | None = None, leeway_seconds: int = 60) -> bool:
    """Use the unverified JWT expiry only to schedule refresh, never to authorize access."""
    if not token:
        return False
    try:
        payload_part = token.split(".")[1]
        padding = "=" * (-len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part + padding))
        expires_at = float(payload["exp"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    current_time = time.time() if now is None else now
    return expires_at > current_time + leeway_seconds


class PlatformCredentialManager:
    """Refreshes Platform JWTs through the official password-login contract."""

    def __init__(
        self,
        base_url: str,
        token: str | None,
        email: str | None,
        password: str | None,
        env_path: Path,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=10,
            transport=transport,
        )
        self._token = token
        self._email = email
        self._password = password
        self._env_path = env_path
        self._clock = clock

    @property
    def base_url(self) -> str:
        return str(self._client.base_url).rstrip("/")

    def ensure_fresh(self, *, force: bool = False) -> str:
        if not force and jwt_is_fresh(self._token, now=self._clock()):
            return str(self._token)
        if not self._email or not self._password:
            raise PlatformAuthError(
                "Platform JWT is expired or invalid and login credentials are unavailable"
            )
        token = self._login()
        if not jwt_is_fresh(token, now=self._clock()):
            raise PlatformAuthError("Platform login returned an expired or invalid access token")
        _replace_env_value(self._env_path, "VOHU_EVALS_PLATFORM_TOKEN", token)
        self._token = token
        os.environ["VOHU_EVALS_PLATFORM_TOKEN"] = token
        return token

    def _login(self) -> str:
        try:
            response = self._client.post(
                "/api/v1/auth/login",
                json={"email": self._email, "password": self._password, "remember": True},
            )
        except httpx.HTTPError:
            raise PlatformAuthError("Platform login request failed") from None
        if response.status_code >= 400:
            raise PlatformAuthError(f"Platform login failed: HTTP {response.status_code}")
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise PlatformAuthError("Platform login returned an invalid response") from exc
        data = (
            payload.get("data") if isinstance(payload, dict) and payload.get("code") == 0 else None
        )
        if not isinstance(data, dict):
            raise PlatformAuthError("Platform login was rejected")
        state = data.get("authentication_state")
        if state == "mfa_required":
            raise PlatformAuthError("Platform login requires MFA; automatic refresh is unavailable")
        token = data.get("access_token")
        if state != "authenticated" or not isinstance(token, str) or not token:
            raise PlatformAuthError("Platform login did not return an authenticated access token")
        return token


def _replace_env_value(env_path: Path, name: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        raise PlatformAuthError("Platform login returned an invalid access token")
    if env_path.is_symlink():
        raise PlatformAuthError("Refusing to update a symlinked .env file")
    try:
        original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    except OSError as exc:
        raise PlatformAuthError("Could not read the local .env file") from exc
    lines = original.splitlines(keepends=True)
    replacement = f"{name}={value}"
    matches = 0
    updated: list[str] = []
    assignment = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=")
    for line in lines:
        ending = "\n" if line.endswith("\n") else ""
        content = line[:-1] if ending else line
        if assignment.match(content):
            matches += 1
            updated.append(replacement + ending)
        else:
            updated.append(line)
    if matches > 1:
        raise PlatformAuthError(f"Local .env contains duplicate {name} entries")
    if matches == 0:
        if updated and not updated[-1].endswith("\n"):
            updated[-1] += "\n"
        updated.append(replacement + "\n")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(prefix=".env.", dir=env_path.parent)
        temp_path = Path(raw_path)
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.writelines(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, env_path)
    except OSError as exc:
        raise PlatformAuthError("Could not update the local .env file") from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
