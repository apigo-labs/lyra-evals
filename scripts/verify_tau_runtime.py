"""Opt-in zero-inference acceptance: official mock task through the Docker MCP bridge."""

import asyncio
import json
import tempfile
import uuid
from pathlib import Path

from vohu_evals.console.acp import docker_command
from vohu_evals.console.tau_runtime import ROOT, TauRuntime


async def main():
    name = "lyra-tau-check-" + uuid.uuid4().hex[:10]
    network = name + "-net"
    with tempfile.TemporaryDirectory(dir=ROOT / ".local") as directory:
        work = Path(directory)
        (work / "fake-key").write_text("offline-not-a-credential")
        runtime = TauRuntime(
            name,
            network,
            work,
            work,
            {
                "domain": "mock",
                "task_id": "create_task_1_with_env_assertions",
                "seed": 0,
                "deadline_seconds": 120,
                "synthetic": True,
                "simulator": {
                    "target_id": "fake-key",
                    "budget": 0,
                    "protocol": "openai_chat_completions",
                    "connection": {"model": "offline"},
                    "price_bound": [1, 1],
                    "runtime_digest": "lyra-evals-tau-runtime:local",
                    "relay_digest": "lyra-evals-gateway:local",
                },
            },
        )
        await docker_command("network", "create", "--internal", network)
        process = None
        try:
            initial = await runtime.start()
            assert set(initial) == {"observation", "policy", "tools"}
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
                "--entrypoint",
                "node",
                "-v",
                f"{ROOT}/deploy/suites/tau-runtime/mcp.mjs:/bridge.mjs:ro",
                "lyra-evals-codex-acp:local",
                "/bridge.mjs",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            async def call(identifier, method, params):
                process.stdin.write(
                    (
                        json.dumps(
                            {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
                        )
                        + "\n"
                    ).encode()
                )
                await process.stdin.drain()
                response = json.loads(await asyncio.wait_for(process.stdout.readline(), 120))
                assert "error" not in response
                return response["result"]

            await call(1, "initialize", {"protocolVersion": "2024-11-05"})
            assert (await call(2, "tools/list", {}))["tools"][0]["name"] == "tau_step"
            for i, action in enumerate(
                [
                    {
                        "name": "create_task",
                        "arguments": {"user_id": "user_1", "title": "Important Meeting"},
                    },
                    {"name": "done", "arguments": {}},
                ]
            ):
                result = await call(
                    i + 3,
                    "tools/call",
                    {"name": "tau_step", "arguments": {"action": json.dumps(action)}},
                )
                assert not result.get("isError"), result
            assert runtime.score()["correct"]
            assert not runtime.billing()["requests"], "Offline acceptance must never invoke a model"
            print(
                "PASS: official tau2 mock -> Docker MCP -> state assertions -> reward 1; "
                "zero model requests"
            )
        finally:
            await docker_command("rm", "-f", name)
            if process and process.returncode is None:
                await process.wait()
            await runtime.close(work)
            await docker_command("network", "rm", network)


if __name__ == "__main__":
    asyncio.run(main())
