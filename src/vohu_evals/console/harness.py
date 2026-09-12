"""Secret-free Codex provider configuration and strict ACP effort negotiation."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from vohu_evals.console.acp import ACPClient, ACPError


def codex_config(model: str, endpoint: str, effort: str) -> str:
    url = urlsplit(endpoint)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Codex 入口必须是无凭据的 HTTPS Gateway 地址")
    if effort not in {"provider_default", "low", "medium", "high", "xhigh"}:
        raise ValueError("不支持的 Codex effort 候选值")
    if not model or len(model) > 200:
        raise ValueError("模型 ID 无效")
    # JSON strings are valid TOML basic strings; credentials are only referenced by env name.
    lines = [
        f"model = {json.dumps(model)}",
        'model_provider = "apigo"',
        'approval_policy = "never"',
        'web_search = "disabled"',
    ]
    if effort != "provider_default":
        lines.append(f"model_reasoning_effort = {json.dumps(effort)}")
    lines += [
        "[model_providers.apigo]",
        'name = "APIGO"',
        f"base_url = {json.dumps(endpoint.rstrip('/'))}",
        'env_key = "LYRA_GATEWAY_KEY"',
        'wire_api = "responses"',
    ]
    return "\n".join(lines) + "\n"


async def configure_effort(
    client: ACPClient, session: dict, effort: str, *, codex_config_effort: str | None = None
) -> dict:
    """Only set an effort actually advertised by this ACP session; never infer effective effort."""
    evidence = {"requested_effort": effort, "serialized_effort": None, "effective_effort": None}
    if effort == "provider_default":
        return evidence
    candidates = [
        option
        for option in session.get("configOptions", [])
        if option.get("category") == "thought_level"
    ]
    if not candidates and codex_config_effort == effort:
        # Custom-provider models may not be present in ACP's model catalog.
        # Codex still receives the frozen config.toml; the relay must validate
        # reasoning.effort on every outgoing request before any paid call.
        return {**evidence, "effort_configuration": "codex_config_with_relay_validation"}
    if len(candidates) != 1:
        raise ACPError("ACP 未声明唯一 effort 配置，拒绝回退")
    option = candidates[0]
    values = {entry.get("value") for entry in option.get("options", []) if "value" in entry}
    if effort not in values:
        raise ACPError("ACP 会话不支持请求的 effort")
    result = await client.call(
        "session/set_config_option",
        {
            "sessionId": session["sessionId"],
            "configId": option["id"],
            "value": effort,
        },
    )
    confirmed = next(
        (item for item in result.get("configOptions", []) if item.get("id") == option["id"]), {}
    )
    if confirmed.get("currentValue") != effort:
        raise ACPError("ACP 未确认 effort 设置，拒绝静默忽略")
    evidence["serialized_effort"] = effort
    return evidence
