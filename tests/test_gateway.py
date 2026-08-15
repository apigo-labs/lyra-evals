from __future__ import annotations

import httpx

from vohu_evals.gateway import (
    APIGOGatewayAdapter,
    _citations,
    _gateway_timeout_seconds,
    _output_text,
)
from vohu_evals.models import BenchmarkRequest


def test_extracts_responses_output_and_citations() -> None:
    payload = {
        "output": [
            {
                "content": [
                    {
                        "text": "answer",
                        "annotations": [{"type": "url_citation", "url": "https://example.com"}],
                    }
                ]
            }
        ]
    }
    assert _output_text(payload) == "answer"
    assert _citations(payload)[0]["url"] == "https://example.com"


def test_extracts_chat_output_and_citations() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": "chat answer",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url_citation": {"url": "https://example.com/chat"},
                        }
                    ],
                }
            }
        ]
    }
    assert _output_text(payload) == "chat answer"
    assert _citations(payload)[0]["url_citation"]["url"] == "https://example.com/chat"


def test_chat_adapter_keeps_official_body_and_reads_audit_identity_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.read() == (
            b'{"model":"vohu/research-benchmark@v1","messages":[{"role":"user",'
            b'"content":"hello"}],"stream":false,"web_search_options":{}}'
        )
        return httpx.Response(
            200,
            json={
                "id": "exec-1",
                "model": "apigo/vohu",
                "choices": [{"message": {"role": "assistant", "content": "answer"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            headers={"x-request-id": "request-1", "x-vohu-execution-id": "exec-1"},
            request=request,
        )

    adapter = APIGOGatewayAdapter(
        "https://gateway.example",
        "secret",
        model="vohu/research-benchmark@v1",
        transport=httpx.MockTransport(handler),
    )
    result = adapter.invoke(BenchmarkRequest(case_id="1", prompt="hello", web_search=True))

    assert result.request_id == "request-1"
    assert result.execution_id == "exec-1"
    assert result.attempt_models == ()


def test_gateway_adapter_rejects_non_chat_target_protocol() -> None:
    import pytest

    with pytest.raises(ValueError, match="openai_chat_completions"):
        APIGOGatewayAdapter(
            "https://gateway.example",
            "secret",
            protocol="openai_responses",
        )


def test_gateway_timeout_covers_official_research_execution_window() -> None:
    assert _gateway_timeout_seconds("apigo/vohu") == 300.0
    assert _gateway_timeout_seconds("apigo/vohu-research") == 1320.0
    assert _gateway_timeout_seconds("vohu/research-benchmark@v1") == 660.0


def test_chat_adapter_applies_runner_attempt_timeout_without_changing_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"]["read"] == 12.5
        assert b"timeout_seconds" not in request.read()
        return httpx.Response(
            200,
            json={
                "id": "exec-timeout",
                "model": "apigo/vohu",
                "choices": [{"message": {"role": "assistant", "content": "answer"}}],
            },
            request=request,
        )

    adapter = APIGOGatewayAdapter(
        "https://gateway.example",
        "secret",
        model="vohu/research-benchmark@v2",
        transport=httpx.MockTransport(handler),
    )

    adapter.invoke(
        BenchmarkRequest(
            case_id="1",
            prompt="hello",
            web_search=True,
            timeout_seconds=12.5,
        )
    )
