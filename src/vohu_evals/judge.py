from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from vohu_evals.gateway import GatewayError


@dataclass(frozen=True)
class JudgeResult:
    output_text: str
    request_id: str
    response_model: str
    usage: dict[str, Any]
    latency_ms: int


class JudgePort(Protocol):
    model: str

    def evaluate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0,
        reasoning_effort: str | None = None,
    ) -> JudgeResult: ...


class APIGOJudgeAdapter:
    """Runs evaluator-only model calls through APIGO's official Chat Completions API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not model or model != model.strip() or model.startswith(("apigo/vohu", "vohu/")):
            raise ValueError("Judge must be a concrete non-VOHU model")
        self.model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    def evaluate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0,
        reasoning_effort: str | None = None,
    ) -> JudgeResult:
        started = time.monotonic()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            body: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "stream": False,
            }
            if reasoning_effort is not None:
                body["reasoning_effort"] = reasoning_effort
            response = self._client.post("/v1/chat/completions", json=body)
        except httpx.TransportError as exc:
            raise GatewayError(str(exc), transient=True) from exc
        latency_ms = round((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            transient = response.status_code == 429 or response.status_code in {502, 503, 504}
            raise GatewayError(
                f"Gateway judge returned HTTP {response.status_code}: {response.text[:256]}",
                transient=transient,
            )
        payload = response.json()
        choices = payload.get("choices", [])
        output = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                output = message["content"]
        return JudgeResult(
            output_text=output,
            request_id=response.headers.get("x-request-id", str(payload.get("id", ""))),
            response_model=str(payload.get("model", "")),
            usage=payload.get("usage", {}),
            latency_ms=latency_ms,
        )
