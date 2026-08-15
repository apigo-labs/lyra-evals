from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from vohu_evals.models import BenchmarkRequest, InvocationResult


class GatewayError(RuntimeError):
    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class GatewayPort(Protocol):
    def invoke(self, request: BenchmarkRequest) -> InvocationResult: ...


def _gateway_timeout_seconds(model: str) -> float:
    canonical = model.strip().lower()
    if canonical == "apigo/vohu-research":
        return 1320.0
    if canonical.startswith("vohu/research-benchmark@"):
        return 660.0
    return 300.0


def extract_output_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices", [])
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message", {})
        if isinstance(message, Mapping) and isinstance(message.get("content"), str):
            return str(message["content"])
    if isinstance(payload.get("output_text"), str):
        return str(payload["output_text"])
    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, Mapping):
            continue
        for content in item.get("content", []):
            if isinstance(content, Mapping) and isinstance(content.get("text"), str):
                parts.append(str(content["text"]))
    return "\n".join(parts)


def extract_citations(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    found: list[dict[str, Any]] = []
    choices = payload.get("choices", [])
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message", {})
        if isinstance(message, Mapping):
            for annotation in message.get("annotations", []):
                if isinstance(annotation, dict) and annotation.get("type") in {
                    "url_citation",
                    "citation",
                }:
                    found.append(annotation)
    for item in payload.get("output", []):
        if not isinstance(item, Mapping):
            continue
        for content in item.get("content", []):
            if not isinstance(content, Mapping):
                continue
            for annotation in content.get("annotations", []):
                if isinstance(annotation, dict) and annotation.get("type") in {
                    "url_citation",
                    "citation",
                }:
                    found.append(annotation)
    return tuple(found)


# Backward-compatible aliases for existing callers while rescoring uses the public names.
_output_text = extract_output_text
_citations = extract_citations


class APIGOGatewayAdapter:
    """Calls VOHU targets only through official OpenAI Chat Completions."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model: str = "apigo/vohu",
        protocol: str = "openai_chat_completions",
        timeout_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not model or model != model.strip():
            raise ValueError("Gateway model must be a non-empty canonical ID")
        if protocol != "openai_chat_completions":
            raise ValueError("Gateway target protocol must be openai_chat_completions")
        self.model = model
        self.protocol = protocol
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=(
                _gateway_timeout_seconds(model) if timeout_seconds is None else timeout_seconds
            ),
            transport=transport,
        )

    def invoke(self, request: BenchmarkRequest) -> InvocationResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": False,
        }
        if request.web_search:
            body["web_search_options"] = {}
        started = time.monotonic()
        try:
            request_options = (
                {} if request.timeout_seconds is None else {"timeout": request.timeout_seconds}
            )
            response = self._client.post(
                "/v1/chat/completions",
                json=body,
                **request_options,
            )
        except httpx.TransportError as exc:
            raise GatewayError(str(exc), transient=True) from exc
        latency_ms = round((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            transient = response.status_code == 429 or response.status_code in {502, 503, 504}
            raise GatewayError(
                f"Gateway returned HTTP {response.status_code}: {response.text[:256]}",
                transient=transient,
            )
        payload = response.json()
        return InvocationResult(
            output_text=extract_output_text(payload),
            raw_response=payload,
            request_id=response.headers.get("x-request-id", str(payload.get("id", ""))),
            execution_id=response.headers.get("x-vohu-execution-id") or str(payload.get("id", "")),
            response_model=str(payload.get("model", "")),
            attempt_models=(),
            usage=payload.get("usage", {}),
            cost_usd=0.0,
            latency_ms=latency_ms,
            citations=extract_citations(payload),
        )


class FixtureGatewayAdapter:
    def __init__(self, responses: Mapping[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls = 0

    def invoke(self, request: BenchmarkRequest) -> InvocationResult:
        self.calls += 1
        fixture = self.responses[request.case_id]
        payload = fixture.get("raw_response", {"output_text": fixture["output_text"]})
        return InvocationResult(
            output_text=str(fixture["output_text"]),
            raw_response=payload,
            request_id=f"fixture-{uuid.uuid4().hex[:12]}",
            execution_id=f"fixture-execution-{request.case_id}",
            response_model="apigo/vohu",
            attempt_models=tuple(fixture.get("attempt_models", ["qwen-fixture"])),
            usage=fixture.get("usage", {"input_tokens": 10, "output_tokens": 2}),
            cost_usd=float(fixture.get("cost_usd", 0.001)),
            latency_ms=int(fixture.get("latency_ms", 10)),
            citations=tuple(fixture.get("citations", [])),
            attempts=tuple(fixture.get("attempts", [])),
        )


def dump_response(result: InvocationResult) -> str:
    return json.dumps(result.raw_response, ensure_ascii=False, sort_keys=True)
