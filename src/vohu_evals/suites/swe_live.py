"""SWE working trees and the official evaluator, with no gold patch in Agent context."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

from vohu_evals.suites.adapters import swe_command, swe_predictions, swe_score


async def command(*args: str, cwd: Path, timeout: int = 300) -> str:
    process = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise RuntimeError("SWE preparation/evaluation command failed")
        return output.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def prepare_workspace(row: dict, path: Path, environment: dict):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", row["repo"]) or not re.fullmatch(
        r"[0-9a-f]{40}", row["base_commit"]
    ):
        raise ValueError("Invalid SWE repository or base commit")
    path.mkdir(parents=True, exist_ok=False)
    from vohu_evals.console.acp import docker_command

    container = "lyra-swe-copy-" + os.urandom(8).hex()
    try:
        code, _ = await docker_command(
            "create",
            "--name",
            container,
            "--network",
            "none",
            "--platform",
            "linux/amd64",
            environment["image"],
            "true",
        )
        if code:
            raise RuntimeError("Cannot open frozen SWE workspace")
        await command("docker", "cp", f"{container}:/testbed/.", str(path.absolute()), cwd=path)
    finally:
        await asyncio.shield(docker_command("rm", "-f", container))
    # Official images may add a dependency-compatibility commit above the dataset base.
    # Preserve that frozen environment; reject an unrelated checkout.
    await command("git", "merge-base", "--is-ancestor", row["base_commit"], "HEAD", cwd=path)
    await command("git", "config", "core.filemode", "false", cwd=path)
    # Parent remains private; allow the unprivileged container UID to edit its own checkout.
    for directory, _dirs, files in os.walk(path):
        os.chmod(directory, 0o777)
        for name in files:
            file = Path(directory) / name
            if not file.is_symlink():
                file.chmod(0o666 | (file.stat().st_mode & 0o111))


async def collect_patch(path: Path) -> str:
    await command(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "add",
        "--intent-to-add",
        "--",
        ".",
        cwd=path,
    )
    # .git is separately mounted read-only in the Agent container. Never execute diff drivers.
    return await command(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules",
        "--binary",
        "HEAD",
        cwd=path,
    )


async def grade_patch(row: dict, patch: str, root: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    dataset, predictions = output / "dataset.json", output / "predictions.json"
    dataset.write_text(json.dumps([row]))
    predictions.write_text(
        json.dumps(swe_predictions([row], {row["case_id"]: patch}, "frozen-acp"))
    )
    run_id = "lyra-" + output.name
    args = swe_command(
        root / ".local/upstream/swebench",
        root / ".local/benchmark-runtime/bin/python",
        dataset,
        predictions,
        run_id,
    )
    from vohu_evals.console.acp import docker_command

    try:
        await command(*args, cwd=output, timeout=3600)
    finally:
        await asyncio.shield(
            docker_command("rm", "-f", f"sweb.eval.{row['case_id'].lower()}.{run_id}")
        )
    report_path = output / f"frozen-acp.{run_id}.json"
    score = swe_score(json.loads(report_path.read_text()), [row["case_id"]])
    return {"correct": bool(score["correct"]), "value": score["value"], "metric": "resolved_rate"}


async def preflight(rows: list[dict], root: Path) -> dict:
    from vohu_evals.console.acp import docker_command
    from vohu_evals.suites.adapters import UPSTREAM_REVISIONS

    frozen = {}
    for row in rows:
        key = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        path = root / ".local/swe-specs" / f"{key}.json"
        if not path.exists():
            raise ValueError(
                "SWE 实例环境尚未冻结, 请先运行 scripts/swe_runtime.py --prepare; 不会发起模型调用"
            )
        payload = json.loads(path.read_text())
        if payload["revision"] != UPSTREAM_REVISIONS["swebench"]:
            raise ValueError("SWE runtime 版本不匹配, 请重新准备实例环境")
        code, _ = await docker_command("image", "inspect", payload["image"], "--format", "{{.Id}}")
        if code:
            raise ValueError("SWE 实例镜像缺失, 请重新准备; 不会发起模型调用")
        frozen[row["case_id"]] = {
            "spec_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "image": payload["image"],
        }
    return frozen


async def start_tools(name: str, network: str, workspace: Path, environment: dict):
    from vohu_evals.console.acp import docker_command

    worker = Path(__file__).resolve().parents[3] / "deploy/suites/swe-runtime/worker.py"
    code, _ = await docker_command(
        "run",
        "-d",
        "--rm",
        "--name",
        name + "-swe",
        "--platform",
        "linux/amd64",
        "--network",
        network,
        "--network-alias",
        "swe-runtime",
        "--dns",
        "127.0.0.1",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "4g",
        "--cpus",
        "2",
        "--pids-limit",
        "256",
        "--entrypoint",
        "/usr/bin/python3",
        "-v",
        f"{worker}:/lyra-worker.py:ro",
        "-v",
        f"{workspace.absolute()}:/testbed",
        "-v",
        f"{workspace.absolute()}/.git:/testbed/.git:ro",
        environment["image"],
        "/lyra-worker.py",
    )
    if code:
        raise RuntimeError("Cannot start SWE tool environment")
    for _ in range(20):
        code, _ = await docker_command(
            "exec",
            name + "-swe",
            "/usr/bin/python3",
            "-c",
            "import socket; socket.create_connection(('127.0.0.1',8091),1).close()",
        )
        if not code:
            return
        await asyncio.sleep(1)
    raise RuntimeError("SWE tool environment not ready")
