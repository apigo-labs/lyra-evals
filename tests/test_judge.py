from __future__ import annotations

import json

import httpx

from vohu_evals.judge import APIGOJudgeAdapter


def test_judge_uses_official_gateway_chat_body_with_protocol_options() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "judge-1",
                "model": "gemini-3.1-pro-preview",
                "choices": [{"message": {"content": '{"verdict":"MET"}'}}],
                "usage": {"total_tokens": 9},
            },
            request=request,
        )

    judge = APIGOJudgeAdapter(
        "http://ai-gateway.localhost",
        "secret",
        "gemini-3.1-pro-preview",
        transport=httpx.MockTransport(handler),
    )
    result = judge.evaluate(
        "criterion",
        system_prompt="system",
        temperature=0.2,
        reasoning_effort="low",
    )

    assert captured == {
        "model": "gemini-3.1-pro-preview",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "criterion"},
        ],
        "temperature": 0.2,
        "stream": False,
        "reasoning_effort": "low",
    }
    assert result.request_id == "judge-1"
