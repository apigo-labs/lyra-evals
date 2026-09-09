from __future__ import annotations

import asyncio
import json
import time
from typing import Any


class ACPError(RuntimeError):
    pass


async def docker_command(*args: str, timeout: float = 15) -> tuple[int, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except OSError:
        return 127, ""
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
        return process.returncode or 0, stdout.decode().strip()
    except TimeoutError:
        process.kill()
        await process.wait()
        return 124, ""


class ACPClient:
    """Bounded JSON-RPC stdio client. Host filesystem/terminal calls always denied."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        timeout: float = 30,
        allow_container_tools: bool = False,
    ):
        self.process = process
        self.timeout = timeout
        self.allow_container_tools = allow_container_tools
        self.last_error: dict | None = None
        self.sequence = 0
        self.output: list[str] = []

    async def send(self, payload: dict[str, Any]) -> None:
        if not self.process.stdin:
            raise ACPError("ACP stdin unavailable")
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        await self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        async with asyncio.timeout(self.timeout):
            while True:
                if not self.process.stdout:
                    raise ACPError("ACP stdout unavailable")
                line = await self.process.stdout.readline()
                if not line:
                    raise ACPError("ACP 进程提前退出")
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ACPError("ACP 返回非法 JSON") from exc
                if "method" in event and "id" in event:
                    if (
                        event["method"] == "session/request_permission"
                        and self.allow_container_tools
                    ):
                        option = next(
                            (
                                o
                                for o in event.get("params", {}).get("options", [])
                                if o.get("kind") == "allow_once"
                            ),
                            None,
                        )
                        outcome = (
                            {"outcome": "selected", "optionId": option["optionId"]}
                            if option
                            else {"outcome": "cancelled"}
                        )
                        await self.send(
                            {"jsonrpc": "2.0", "id": event["id"], "result": {"outcome": outcome}}
                        )
                        continue
                    await self.send(
                        {
                            "jsonrpc": "2.0",
                            "id": event["id"],
                            "error": {
                                "code": -32601,
                                "message": "Client capability not available in isolated profile",
                            },
                        }
                    )
                elif event.get("method") == "session/update":
                    update = event.get("params", {}).get("update", {})
                    if update.get("sessionUpdate") == "tool_call":
                        self.output.clear()
                    if update.get("sessionUpdate") == "agent_message_chunk":
                        text = update.get("content", {}).get("text", "")
                        if isinstance(text, str):
                            self.output.append(text)
                            if sum(map(len, self.output)) > 100000:
                                raise ACPError("ACP 输出超过限制")
                elif event.get("id") == request_id:
                    if "error" in event:
                        self.last_error = event["error"]
                        raise ACPError("ACP 请求失败")
                    result = event.get("result")
                    if not isinstance(result, dict):
                        raise ACPError("ACP result 类型错误")
                    return result


async def smoke_episode(name: str, image: str) -> tuple[bool, int]:
    """Runs a synthetic ACP adapter inside a network-disabled fresh container."""
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        "-i",
        "--name",
        name,
        "--label",
        "lyra.console=true",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--user",
        "65534:65534",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        image,
        stdout=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=1024 * 1024,
    )
    try:
        client = ACPClient(process)
        initialized = await client.call(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "lyra-evals", "version": "0.1.0"},
            },
        )
        if initialized.get("protocolVersion") != 1:
            raise ACPError("ACP 协议版本不匹配")
        session = await client.call("session/new", {"cwd": "/tmp", "mcpServers": []})
        session_id = session.get("sessionId")
        if not isinstance(session_id, str):
            raise ACPError("ACP 会话 ID 缺失")
        result = await client.call(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": "LYRA_SANDBOX_CHECK"}]},
        )
        passed = (
            result.get("stopReason") == "end_turn" and "".join(client.output) == "LYRA_SANDBOX_OK"
        )
        return passed, round((time.monotonic() - started) * 1000)
    finally:
        # Await cleanup even when the scheduler cancels the coroutine.
        await asyncio.shield(docker_command("rm", "-f", name, timeout=10))
        if process.returncode is None:
            process.kill()
            await process.wait()
