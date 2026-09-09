"""Explicit Docker/ACP infrastructure acceptance, no model calls or credentials."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from urllib.request import Request, urlopen

from vohu_evals.console.acp import ACPClient, docker_command

BASE = "http://127.0.0.1:8768/api"


def request(path: str, payload: dict | None = None):
    req = Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "X-Lyra-Console": "1"},
    )
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def wait_run(identifier: str):
    for _ in range(60):
        run = next(r for r in request("/runs") if r["id"] == identifier)
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.5)
    raise AssertionError("Runtime acceptance exceeded deadline")


async def verify_acp(image: str):
    name = f"lyra-acceptance-{uuid.uuid4().hex[:8]}"
    process = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        "-i",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--tmpfs",
        "/tmp:rw,nosuid,size=64m",
        "--memory",
        "512m",
        "-e",
        "HOME=/tmp",
        image,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=1024 * 1024,
    )
    try:
        client = ACPClient(process)
        result = await client.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        assert result["protocolVersion"] == 1
        print(f"{image} initialize: passed (no prompt/model call)")
    finally:
        await docker_command("rm", "-f", name)
        if process.returncode is None:
            process.kill()
            await process.wait()


def main():
    run = request(
        "/runs",
        {
            "name": "验收 · 三评测集并发 (沙箱自检)",
            "mode": "smoke",
            "benchmarks": ["ifeval", "gpqa", "livecodebench"],
            "sample_size": 3,
            "max_jobs": 3,
            "per_job": 2,
            "max_episodes": 3,
        },
    )
    completed = wait_run(run["id"])
    assert completed["status"] == "completed", completed
    assert sum(j["correct"] for j in completed["jobs"]) == 9
    assert completed["manifest"]["synthetic"] is True
    print("Three benchmark queues, 9 fresh Docker/ACP episodes: passed")
    run = request(
        "/runs",
        {
            "name": "验收 · 取消与回收 (沙箱自检)",
            "mode": "smoke",
            "benchmarks": ["ifeval", "gpqa"],
            "sample_size": 20,
            "max_jobs": 2,
            "per_job": 1,
            "max_episodes": 2,
        },
    )
    time.sleep(0.2)
    cancelled = request(f"/runs/{run['id']}/cancel", {})
    assert cancelled["status"] == "cancelled"
    print("Cancellation: passed")
    asyncio.run(verify_acp("lyra-evals-claude-acp:local"))
    asyncio.run(verify_acp("lyra-evals-codex-acp:local"))
    code, output = asyncio.run(
        docker_command("ps", "-a", "--filter", "label=lyra.console=true", "--format", "{{.Names}}")
    )
    assert code == 0
    assert not any(name.startswith(f"lyra-{run['id'][:12]}") for name in output.splitlines())
    print("Cancelled run container cleanup: passed")


if __name__ == "__main__":
    main()
