"""Two private networks keep simulator credentials and hidden state away from the Agent."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from vohu_evals.console.acp import ACPError, docker_command

ROOT = Path(__file__).resolve().parents[3]


class TauRuntime:
    def __init__(self, name: str, network: str, work: Path, key_root: Path, spec: dict):
        self.name = name + "-tau"
        self.relay = name + "-sim"
        self.network = name + "-simnet"
        self.agent_network = network
        self.work = work
        self.key_root = key_root
        self.spec = spec

    async def start(self) -> dict:
        role = self.spec["simulator"]
        self.ledger = self.work / "sim-ledger"
        self.evidence = self.work / "tau-evidence"
        self.ledger.mkdir()
        self.evidence.mkdir()
        config = {
            "model": role["connection"]["model"],
            "endpoint": "https://api.apigo.ai",
            "path": "/v1/messages"
            if role["protocol"] == "anthropic_messages"
            else "/v1/chat/completions",
            "budget": float(role["budget"]),
            "max_output_tokens": 4096,
            "deadline_seconds": self.spec["deadline_seconds"],
            "effort": "provider_default",
            "input_rate": role["price_bound"][0],
            "output_rate": role["price_bound"][1],
        }
        (self.work / "sim-config.json").write_text(json.dumps(config))
        task = {**self.spec, "model": config["model"], "protocol": role["protocol"]}
        (self.work / "tau-task.json").write_text(json.dumps(task))
        code, _ = await docker_command("network", "create", "--internal", self.network)
        if code:
            raise ACPError("Cannot create simulator network")
        code, _ = await docker_command(
            "run",
            "-d",
            "--rm",
            "--name",
            self.relay,
            "--network",
            self.network,
            "--network-alias",
            "simulator-gateway",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "256m",
            "-v",
            f"{self.work}/sim-config.json:/config/relay.json:ro",
            "-v",
            f"{self.key_root / role['target_id']}:/config/key:ro",
            "-v",
            f"{self.ledger}:/ledger",
            role["relay_digest"],
        )
        if code:
            raise ACPError("Cannot create simulator relay")
        code, _ = await docker_command("network", "connect", "bridge", self.relay)
        if code:
            raise ACPError("Cannot connect simulator relay")
        code, _ = await docker_command(
            "run",
            "-d",
            "--rm",
            "--name",
            self.name,
            "--network",
            self.network,
            "--dns",
            "127.0.0.1",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "1g",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:rw,size=128m",
            "-v",
            f"{self.work}/tau-task.json:/config/task.json:ro",
            "-v",
            f"{self.evidence}:/evidence",
            role["runtime_digest"],
        )
        if code:
            raise ACPError("Cannot start official tau2 environment")
        code, _ = await docker_command(
            "network", "connect", "--alias", "tau-runtime", self.agent_network, self.name
        )
        if code:
            raise ACPError("Cannot connect benchmark tools")
        # The request executes inside the runtime; it never exposes the hidden dataset to Agent.
        script = (
            "import urllib.request; "
            "r=urllib.request.urlopen(urllib.request.Request("
            "'http://127.0.0.1:8090/reset',data=b'{}',"
            "headers={'Content-Type':'application/json'}),timeout=240); print(r.read().decode())"
        )
        for _ in range(30):
            code, _ = await docker_command(
                "exec",
                self.name,
                "python",
                "-c",
                "import socket; socket.create_connection(('127.0.0.1',8090),1).close()",
            )
            if code == 0:
                break
            await asyncio.sleep(1)
        else:
            raise ACPError("tau2 runtime did not become ready")
        code, output = await docker_command(
            "exec", self.name, "python", "-c", script, timeout=self.spec["deadline_seconds"]
        )
        if code:
            raise ACPError("tau2 simulator failed to initialize; no automatic replay")
        return json.loads(output)

    def score(self) -> dict:
        path = self.evidence / "result.json"
        if not path.exists():
            raise ACPError("Agent did not finish the official tau2 episode")
        score = json.loads(path.read_text())
        return {key: score[key] for key in ["correct", "value", "metric", "steps", "truncated"]}

    def billing(self) -> dict:
        path = self.ledger / "requests.json"
        if not path.exists():
            return {"requests": [], "reserved_usd": 0}
        result = json.loads(path.read_text())
        for request in result["requests"]:
            request["role"] = "simulator_and_judge"
        return result

    async def close(self, destination: Path):
        await docker_command("rm", "-f", self.name, self.relay)
        await docker_command("network", "rm", self.network)
        if hasattr(self, "evidence") and (self.evidence / "result.json").exists():
            path = destination / f"{self.name}-official.json"
            path.write_bytes((self.evidence / "result.json").read_bytes())
            os.chmod(path, 0o600)
