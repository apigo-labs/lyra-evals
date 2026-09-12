"""Bounded ACP execution for closed-book and code-generation profiles."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import time
from pathlib import Path

from vohu_evals.console.acp import ACPClient, ACPError, docker_command
from vohu_evals.console.harness import codex_config, configure_effort

RELAY_IMAGE = "lyra-evals-gateway:local"
SUPPORTED = {"ifeval", "gpqa", "livecodebench", "swebench", "tau2"}


def price_bound(catalog: dict, model: str, cap: dict | None = None) -> tuple[float, float]:
    row = next((r for r in catalog["data"]["items"] if r["slug"] == model), None)
    if not row:
        if cap:
            from vohu_evals.console.contracts import PriceCap

            validated = PriceCap.model_validate(cap)
            return validated.input_usd_per_1m, validated.output_usd_per_1m
        raise ValueError(
            "模型可用性与公开报价是两回事：请在模型连接填写内测模型的输入/输出价格上限，再冻结计划"
        )
    pricing = row.get("pricing_headline", {})
    sections = [pricing, pricing.get("above_threshold", {})]
    input_rates = [
        float(value)
        for section in sections
        for key, value in section.items()
        if ("input" in key or "cache_write" in key)
        and key.endswith("usd_per_1m")
        and value is not None
    ]
    output_rates = [
        float(value)
        for section in sections
        for key, value in section.items()
        if ("output" in key or "reasoning" in key)
        and key.endswith("usd_per_1m")
        and value is not None
    ]
    if (
        not input_rates
        or not output_rates
        or min(input_rates + output_rates) <= 0
        or not all(math.isfinite(r) for r in input_rates + output_rates)
    ):
        raise ValueError("价格上界无效")
    return max(input_rates), max(output_rates)


def _write_key_file(path: Path, api_key: str) -> None:
    """Write the shared gateway API key to an episode-scoped file for container mounting.

    The file lives only inside the per-episode temporary directory and is
    removed with it; the Console never persists per-target credentials.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(api_key)
        stream.flush()
        os.fsync(stream.fileno())


async def live_episode(
    name: str,
    variant: dict,
    prompt: str,
    api_key: str,
    root: Path,
    workspace: Path | None = None,
    swe_environment: dict | None = None,
    tau: dict | None = None,
) -> dict:
    started = time.monotonic()
    network = f"{name}-net"
    relay = f"{name}-relay"
    process = None
    client = None
    tau_runtime = None
    diagnostic = root / f"{name}-agent.log"
    root.mkdir(parents=True, exist_ok=True)
    log_stream = diagnostic.open("wb")
    diagnostic.chmod(0o600)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        work = Path(temporary).absolute()
        (work / "ledger").mkdir()
        key_path = work / "key"
        _write_key_file(key_path, api_key)
        config = {
            "model": variant["connection"]["model"],
            "endpoint": "https://api.apigo.ai",
            "path": "/v1/responses" if variant["harness"] == "codex" else "/v1/messages",
            "budget": float(variant["episode_budget"]),
            "max_output_tokens": variant["max_output_tokens"],
            "deadline_seconds": variant["deadline_seconds"],
            "effort": variant["effort"],
            "allow_tools": workspace is not None or tau is not None,
            "input_rate": variant["price_bound"][0],
            "output_rate": variant["price_bound"][1],
        }
        (work / "relay.json").write_text(json.dumps(config))
        # Agent receives a placeholder and can only reach its relay on an internal network.
        env = ["-e", "HOME=/tmp", "-e", "LYRA_GATEWAY_KEY=relay-only"]
        mounts = []
        if variant["harness"] == "codex":
            text = codex_config(config["model"], "https://api.apigo.ai/v1", variant["effort"])
            text = text.replace("https://api.apigo.ai/v1", "http://gateway:8080/v1")
            (work / "config.toml").write_text(text)
            os.chmod(work / "config.toml", 0o644)
            mounts = ["-v", f"{work}/config.toml:/tmp/.codex/config.toml:ro"]
        else:
            env += [
                "-e",
                "ANTHROPIC_BASE_URL=http://gateway:8080",
                "-e",
                "ANTHROPIC_API_KEY=relay-only",
                "-e",
                f"ANTHROPIC_MODEL={config['model']}",
                "-e",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
            ]
        if workspace is not None:
            mounts += [
                "-v",
                f"{workspace.absolute()}:/workspace",
                "-v",
                f"{workspace.absolute()}/.git:/workspace/.git:ro",
            ]
        try:
            code, _ = await docker_command("network", "create", "--internal", network)
            if code:
                raise ACPError("不能创建隔离网络")
            code, _ = await docker_command(
                "run",
                "-d",
                "--rm",
                "--name",
                relay,
                "--network",
                network,
                "--network-alias",
                "gateway",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "256m",
                "--pids-limit",
                "64",
                "-v",
                f"{work}/relay.json:/config/relay.json:ro",
                "-v",
                f"{key_path}:/config/key:ro",
                "-v",
                f"{work}/ledger:/ledger",
                variant["relay_digest"],
            )
            if code:
                raise ACPError("不能创建 Gateway 转发器")
            code, _ = await docker_command("network", "connect", "bridge", relay)
            if code:
                raise ACPError("不能连接 Gateway 转发出口")
            mcp_servers = []
            if tau is not None:
                from vohu_evals.console.tau_runtime import ROOT, TauRuntime

                # The simulator relay mounts a key file named after its own target_id;
                # it shares the same episode-scoped gateway key as the main variant.
                _write_key_file(work / tau["simulator"]["target_id"], api_key)
                tau_runtime = TauRuntime(name, network, work, work, tau)
                initial = await tau_runtime.start()
                prompt = (
                    "Complete the customer interaction using tau_step. "
                    "Follow this policy and tool schemas. "
                    "When terminated is true, finish your answer.\n" + json.dumps(initial)
                )
                bridge = ROOT / "deploy/suites/tau-runtime/mcp.mjs"
                mounts += ["-v", f"{bridge}:/opt/lyra-tau.mjs:ro"]
                mcp_servers = [
                    {"name": "tau2", "command": "node", "args": ["/opt/lyra-tau.mjs"], "env": []}
                ]
            if swe_environment is not None:
                from vohu_evals.suites.swe_live import start_tools

                await start_tools(name, network, workspace, swe_environment)
                bridge = Path(__file__).resolve().parents[3] / "deploy/suites/swe-runtime/mcp.mjs"
                mounts += ["-v", f"{bridge}:/opt/lyra-swe.mjs:ro"]
                mcp_servers.append(
                    {"name": "swe", "command": "node", "args": ["/opt/lyra-swe.mjs"], "env": []}
                )
                prompt += (
                    "\nEdit /workspace to fix this issue. Use swe_exec to run tests "
                    "in the official environment at /testbed (the same working tree)."
                )
            process = await asyncio.create_subprocess_exec(
                "docker",
                "run",
                "--rm",
                "-i",
                "--name",
                name,
                "--network",
                network,
                "--dns",
                "127.0.0.1",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "128",
                "--memory",
                "1g",
                "--cpus",
                "1",
                "--tmpfs",
                "/tmp:rw,nosuid,size=128m,mode=1777",
                "--tmpfs",
                "/tmp/.codex:rw,nosuid,size=64m,uid=1000,gid=1000",
                *([] if workspace else ["--tmpfs", "/workspace:rw,nosuid,size=128m,mode=1777"]),
                *env,
                *mounts,
                variant["image_digest"],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log_stream,
                limit=1024 * 1024,
            )
            client = ACPClient(
                process, timeout=variant["deadline_seconds"], allow_container_tools=True
            )
            await client.call(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "lyra-evals", "version": "0.2.0"},
                },
            )
            session = await client.call(
                "session/new", {"cwd": "/workspace", "mcpServers": mcp_servers}
            )
            evidence = await configure_effort(
                client,
                session,
                variant["effort"],
                codex_config_effort=variant["effort"] if variant["harness"] == "codex" else None,
            )
            result = await client.call(
                "session/prompt",
                {"sessionId": session["sessionId"], "prompt": [{"type": "text", "text": prompt}]},
            )
            billing = json.loads((work / "ledger/requests.json").read_text())
            client_errors = sorted(
                {r["client_error"] for r in billing["requests"] if r.get("client_error")}
            )
            if result.get("stopReason") != "end_turn":
                detail = str(result.get("stopReason"))
                if client_errors:
                    # The relay saw a Gateway completion, so the request was billed; the
                    # failure is on the agent-client side and must be reported as such.
                    detail += "；Gateway 已完成并计费，Agent 客户端侧失败: " + ", ".join(
                        client_errors
                    )
                raise ACPError("Agent 未正常完成回答: " + detail)
            if not any(r.get("status") == "received" for r in billing["requests"]):
                raise ACPError("没有成功的 Gateway 请求证据，拒绝将 ACP 错误文本当作答案评分")
            if (
                evidence.get("effort_configuration") == "codex_config_with_relay_validation"
                and billing["requests"]
            ):
                evidence["serialized_effort"] = variant["effort"]
            tau_score = tau_runtime.score() if tau_runtime else None
            return {
                "tau_score": tau_score,
                "answer": "".join(client.output),
                "latency_ms": round((time.monotonic() - started) * 1000),
                "requests": billing["requests"],
                "reserved_usd": billing["reserved_usd"],
                **evidence,
            }
        finally:
            if client and client.last_error:
                log_stream.write(json.dumps(client.last_error).encode())
            log_stream.close()
            # Preserve the cost evidence even on timeout/cancellation; no raw prompts in ledger.
            ledger = work / "ledger/requests.json"
            await asyncio.shield(docker_command("rm", "-f", name, relay, name + "-swe", timeout=15))
            if tau_runtime:
                await asyncio.shield(tau_runtime.close(root))
            if ledger.exists():
                destination = root / f"{name}-requests.json"
                combined = json.loads(ledger.read_text())
                if tau_runtime:
                    extra = tau_runtime.billing()
                    combined["requests"] += extra["requests"]
                    combined["reserved_usd"] += extra["reserved_usd"]
                destination.write_text(json.dumps(combined))
                os.chmod(destination, 0o600)
            await asyncio.shield(docker_command("network", "rm", network))
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
