"""Real ACP jobs with frozen datasets, per-episode reservations and local grading."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.request import urlopen

from vohu_evals.console.acp import docker_command
from vohu_evals.console.live import RELAY_IMAGE, SUPPORTED, live_episode, price_bound
from vohu_evals.console.planner import digest
from vohu_evals.suites.adapters import grade_livecodebench, grade_text
from vohu_evals.suites.packs import agent_input, load_pack

ROOT = Path(__file__).resolve().parents[3]


def catalog():
    with urlopen("https://www.apigo.ai/platform/api/v1/models/list", timeout=20) as response:
        return json.load(response)


async def prepare(plan: dict, budget: float) -> dict:
    manifest = copy.deepcopy(plan["manifest"])
    if set(manifest["benchmarks"]) - SUPPORTED:
        raise ValueError("不支持的评测集")
    if "tau2" in manifest["benchmarks"] and not manifest.get("simulator"):
        raise ValueError("τ² simulator configuration missing")
    if Decimal(plan["episode_caps_total"]) > Decimal(str(budget)):
        raise ValueError("所有单题预留上限之和超过本次授权预算，请减少题数、变体或单题上限")
    if "swebench" in manifest["benchmarks"]:
        from vohu_evals.suites.swe_live import preflight

        _, rows = load_pack(ROOT / ".local/suites", "swebench")
        selected = set(manifest["datasets"]["swebench"]["case_ids"])
        manifest["swe_environments"] = await preflight(
            [r for r in rows if r["case_id"] in selected], ROOT
        )
    prices = await asyncio.to_thread(catalog)
    if "tau2" in manifest["benchmarks"]:
        role = manifest.get("simulator")
        if not role:
            raise ValueError("τ² simulator configuration missing")
        if role["connection"]["endpoint"].rstrip("/") not in {
            "https://api.apigo.ai",
            "https://api.apigo.ai/v1",
        }:
            raise ValueError("Simulator must use APIGO")
        role["price_bound"] = price_bound(
            prices, role["connection"]["model"], role["connection"].get("price_cap")
        )
        for field, tag in [
            ("runtime_digest", "lyra-evals-tau-runtime:local"),
            ("relay_digest", RELAY_IMAGE),
        ]:
            code, value = await docker_command("image", "inspect", tag, "--format", "{{.Id}}")
            if code:
                raise ValueError("缺少 τ² runtime / Gateway 镜像")
            role[field] = value
    for variant in manifest["variants"]:
        connection = variant["connection"]
        if connection["endpoint"].rstrip("/") not in {
            "https://api.apigo.ai",
            "https://api.apigo.ai/v1",
        }:
            raise ValueError("真实验收仅允许 APIGO Gateway")
        variant["pricing_basis"] = (
            "public_upper_bound"
            if any(r["slug"] == connection["model"] for r in prices["data"]["items"])
            else "operator_supplied_upper_bound"
        )
        variant["price_bound"] = price_bound(
            prices, connection["model"], connection.get("price_cap")
        )
        image = (
            "lyra-evals-codex-acp:local"
            if variant["harness"] == "codex"
            else "lyra-evals-claude-acp:local"
        )
        images = [("image_digest", image), ("relay_digest", RELAY_IMAGE)]
        if "livecodebench" in manifest["benchmarks"]:
            images.append(("grader_digest", "lyra-evals-lcb-grader:local"))
        for field, tag in images:
            code, value = await docker_command("image", "inspect", tag, "--format", "{{.Id}}")
            if code or not value.startswith("sha256:"):
                raise ValueError(f"缺少执行镜像：{tag}")
            variant[field] = value
    for benchmark in manifest["benchmarks"]:
        frozen, _ = load_pack(ROOT / ".local/suites", benchmark)
        if frozen["sha256"] != manifest.get("datasets", {}).get(benchmark, {}).get("sha256"):
            raise ValueError("题集与计划不一致，请重新冻结")
    manifest["controller_sha256"] = digest(
        {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in [
                "src/vohu_evals/console/acp.py",
                "src/vohu_evals/console/live.py",
                "src/vohu_evals/console/tau_runtime.py",
                "src/vohu_evals/suites/swe_live.py",
                "scripts/swe_runtime.py",
                "deploy/suites/swe-runtime/worker.py",
                "deploy/suites/swe-runtime/mcp.mjs",
                "deploy/suites/tau-runtime/worker.py",
                "deploy/suites/tau-runtime/mcp.mjs",
                "src/vohu_evals/console/live_scheduler.py",
                "src/vohu_evals/suites/adapters.py",
                "src/vohu_evals/suites/packs.py",
                "benchmarks/ifeval/plugin.py",
            ]
        }
    )
    manifest.update(
        synthetic=False,
        comparison="reference_only",
        execution_profile="acp-gateway-bounded-v1",
        pricing_sha256=digest(prices),
        pricing_basis="public_or_operator_supplied_upper_bound",
        authorized_budget=str(budget),
    )
    manifest["execution_sha256"] = digest(manifest)
    return manifest


async def execute(scheduler, run: dict):
    from vohu_evals.console.scheduler import now

    run["status"] = "running"
    scheduler.event(
        run, "真实 ACP 执行开始；每题独立网络与预留上限；费用为价格上界估算，账单待结算"
    )
    manifest = run["manifest"]
    variants = {v["id"]: v for v in manifest["variants"]}
    slots = asyncio.Semaphore(run["max_episodes"])
    jobs = asyncio.Semaphore(run["max_jobs"])

    async def work(job):
        async with jobs:
            start = time.monotonic()
            job["status"] = "running"
            scheduler.event(run, "启动真实作业", job["id"])
            _, rows = load_pack(ROOT / ".local/suites", job["benchmark"])
            by_id = {row["case_id"]: row for row in rows}
            variant = variants[job["variant_id"]]
            case_slots = asyncio.Semaphore(run["per_job"])

            async def episode(case_id):
                async with case_slots, slots, scheduler.global_slots:
                    episode_started = time.monotonic()
                    row = by_id[case_id]
                    name = "lyra-live-" + uuid.uuid4().hex[:20]
                    result = {
                        "case_id": case_id,
                        "status": "system_failed",
                        "correct": False,
                        "cost_usd": None,
                        "billing_status": "pending",
                        "latency_ms": None,
                    }
                    try:
                        workspace = None
                        if job["benchmark"] == "swebench":
                            from vohu_evals.suites.swe_live import prepare_workspace

                            workspace = ROOT / ".local/execution" / f"{name}-workspace"
                            async with asyncio.timeout(300):
                                await prepare_workspace(
                                    row, workspace, manifest["swe_environments"][case_id]
                                )
                        public = agent_input(job["benchmark"], row)
                        prompt = public.get("prompt", "")
                        if job["benchmark"] == "livecodebench":
                            prompt += (
                                "\nReturn only the Python solution, without Markdown fences.\n"
                                + json.dumps(
                                    {
                                        k: v
                                        for k, v in public.items()
                                        if k not in {"case_id", "prompt"}
                                    }
                                )
                            )
                        async with asyncio.timeout(variant["deadline_seconds"]):
                            response = await live_episode(
                                name,
                                variant,
                                prompt,
                                scheduler.store.secrets / variant["target_id"],
                                ROOT / ".local/execution",
                                **(
                                    {
                                        "workspace": workspace,
                                        "swe_environment": manifest["swe_environments"][case_id],
                                    }
                                    if workspace
                                    else {}
                                ),
                                **(
                                    {
                                        "tau": {
                                            "domain": row["domain"],
                                            "task_id": row["task_id"],
                                            "seed": manifest["seed"],
                                            "simulator": manifest["simulator"],
                                            "deadline_seconds": variant["deadline_seconds"],
                                        }
                                    }
                                    if job["benchmark"] == "tau2"
                                    else {}
                                ),
                            )
                        result.update({k: v for k, v in response.items() if k != "answer"})
                        answer = response["answer"]
                        # Raw answers stay outside API/export and version control.
                        answer_path = ROOT / ".local/execution" / f"{name}-answer.txt"
                        answer_path.parent.mkdir(parents=True, exist_ok=True)
                        answer_path.write_text(answer)
                        answer_path.chmod(0o600)
                        if job["benchmark"] == "tau2":
                            score = response["tau_score"]
                        elif job["benchmark"] == "swebench":
                            from vohu_evals.suites.swe_live import collect_patch, grade_patch

                            patch = await collect_patch(workspace)
                            score = await grade_patch(
                                row, patch, ROOT, ROOT / ".local/execution" / f"{name}-score"
                            )
                        elif job["benchmark"] == "livecodebench":
                            match = re.fullmatch(
                                r"\s*```(?:python)?\s*\n(.*?)\n```\s*", answer, re.S
                            )
                            score = await grade_livecodebench(
                                row, match[1] if match else answer, image=variant["grader_digest"]
                            )
                        else:
                            score = await asyncio.to_thread(
                                grade_text, job["benchmark"], row, answer, ROOT
                            )
                        result.update(status="scored", correct=score["correct"], score=score)
                    except (RuntimeError, OSError, ValueError, TimeoutError) as exc:
                        result["error"] = type(exc).__name__ + (
                            ": " + str(exc) if type(exc).__name__ == "ACPError" else ""
                        )
                    finally:
                        result["agent_duration_ms"] = result.get("latency_ms")
                        result["latency_ms"] = round((time.monotonic() - episode_started) * 1000)
                        evidence = ROOT / ".local/execution" / f"{name}-requests.json"
                        if evidence.exists():
                            billing = json.loads(evidence.read_text())
                            result["requests"] = billing["requests"]
                            result["reserved_usd"] = billing["reserved_usd"]
                        job["episodes"].append(result)
                        job["completed"] += 1
                        job["scored"] += int(result["status"] == "scored")
                        job["correct"] += int(result["correct"])
                        if result["latency_ms"] is not None:
                            job["latencies"].append(result["latency_ms"])
                        if result["status"] != "scored":
                            job["error"] = "部分题目未评分；详情见逐题证据"
                        requests = [q for e in job["episodes"] for q in e.get("requests", [])]
                        job["estimated_cost_usd"] = (
                            sum(q["estimated_cost_usd"] for q in requests)
                            if requests
                            and all(q.get("estimated_cost_usd") is not None for q in requests)
                            else None
                        )
                        job["reserved_usd"] = sum(e.get("reserved_usd", 0) for e in job["episodes"])
                        scheduler.event(
                            run,
                            f"完成 {job['completed']}/{job['total']} · {result['status']}",
                            job["id"],
                        )

            async with asyncio.TaskGroup() as group:
                for case_id in manifest["datasets"][job["benchmark"]]["case_ids"]:
                    group.create_task(episode(case_id))
            job["wall_time_ms"] = round((time.monotonic() - start) * 1000)
            job["status"] = "completed" if job["scored"] == job["total"] else "failed"

    try:
        async with asyncio.TaskGroup() as group:
            for job in run["jobs"]:
                group.create_task(work(job))
        run["status"] = (
            "completed" if all(j["status"] == "completed" for j in run["jobs"]) else "failed"
        )
    except asyncio.CancelledError:
        run["status"] = "cancelled"
    except Exception:
        run["status"] = "failed"
    finally:
        for job in run["jobs"]:
            if job["status"] in {"running", "queued"}:
                job["status"] = run["status"]
        run["finished_at"] = now()
        scheduler.event(run, f"真实运行结束：{run['status']}")
        scheduler.tasks.pop(run["id"], None)
