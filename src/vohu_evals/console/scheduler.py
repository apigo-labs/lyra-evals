from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from vohu_evals.console.acp import docker_command, smoke_episode
from vohu_evals.console.contracts import RunInput
from vohu_evals.console.store import Store

SMOKE_IMAGE = "lyra-evals-smoke:local"
ACP_IMAGE = "lyra-evals-claude-acp:local"
CODEX_IMAGE = "lyra-evals-codex-acp:local"


def now() -> str:
    return datetime.now(UTC).isoformat()


class Scheduler:
    def __init__(self, store: Store, global_limit: int = 8):
        self.store = store
        self.runs = {r["id"]: r for r in store.runs()}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.global_slots = asyncio.Semaphore(global_limit)
        self.global_limit = global_limit
        self.revision = 0
        self.creation_lock = asyncio.Lock()
        self.billing_lock = asyncio.Lock()
        self.billing_error = None
        for run in self.runs.values():
            if run["status"] in {"queued", "running"}:
                run["status"] = "interrupted"
                for job in run["jobs"]:
                    if job["status"] in {"queued", "running"}:
                        job["status"] = "interrupted"
                self.event(run, "控制器重新启动；运行标记为中断，不自动重复请求")

    def event(self, run: dict[str, Any], message: str, job_id: str | None = None) -> None:
        run["events"].append(
            {"seq": len(run["events"]) + 1, "at": now(), "message": message, "job_id": job_id}
        )
        self.store.save_run(run)
        self.revision += 1

    async def health(self) -> dict[str, Any]:
        available, _ = await docker_command("version", "--format", "{{.Server.Version}}", timeout=3)
        smoke = acp = codex = False
        if available == 0:
            results = await asyncio.gather(
                docker_command("image", "inspect", SMOKE_IMAGE, "--format", "{{.Id}}", timeout=3),
                docker_command("image", "inspect", ACP_IMAGE, "--format", "{{.Id}}", timeout=3),
                docker_command("image", "inspect", CODEX_IMAGE, "--format", "{{.Id}}", timeout=3),
            )
            smoke, acp, codex = (r[0] == 0 for r in results)
        relay, _ = await docker_command(
            "image", "inspect", "lyra-evals-gateway:local", "--format", "{{.Id}}", timeout=3
        )
        return {
            "docker": available == 0,
            "image": smoke,
            "acp_image": acp,
            "codex_image": codex,
            "live_enabled": available == 0 and relay == 0 and (acp or codex),
            "version": "0.1.0",
            "reason": (
                f"全局最多 {self.global_limit} 个任务容器。"
                "五赛道执行适配已接入；τ² 需固定模拟器和 runtime 镜像，"
                "SWE 需准备官方评测环境。具体任务仍以启动预检为准。"
            ),
        }

    async def create(self, spec: RunInput) -> dict[str, Any]:
        async with self.creation_lock:
            return await self._create(spec)

    async def _create(self, spec: RunInput) -> dict[str, Any]:
        if spec.mode == "live":
            targets = {t["id"]: t for t in self.store.targets()}
            if any(t not in targets for t in spec.targets):
                raise ValueError("模型配置不存在，请刷新后重新选择")
            if not spec.plan_id:
                raise ValueError("真实运行需要先创建并冻结 effort 计划")
            plan = next((p for p in self.store.plans() if p["id"] == spec.plan_id), None)
            if plan is None:
                raise ValueError("计划不存在")
            if any(
                v["target_id"] not in targets
                or any(
                    targets[v["target_id"]].get(key) != v["connection"].get(key)
                    for key in ("model", "endpoint", "protocol", "price_cap")
                )
                for v in plan["manifest"]["variants"]
            ):
                raise ValueError("计划连接已变化或删除，请重新生成计划")
            if spec.budget != float(plan["budget"]):
                raise ValueError("授权预算必须与冻结计划一致")
            if set(spec.targets) != {v["target_id"] for v in plan["manifest"]["variants"]}:
                raise ValueError("运行目标必须与冻结计划一致")
            if set(spec.benchmarks) != set(plan["manifest"]["benchmarks"]):
                raise ValueError("评测集必须与冻结计划一致")
            role = plan["manifest"].get("simulator")
            if role and (
                role["target_id"] not in targets
                or any(
                    targets[role["target_id"]].get(k) != role["connection"].get(k)
                    for k in ("model", "endpoint", "protocol", "price_cap")
                )
            ):
                raise ValueError("模拟器连接变化，请重新冻结计划")
            from vohu_evals.console.live_scheduler import execute, prepare

            if sum(r["status"] in {"queued", "running"} for r in self.runs.values()) >= 4:
                raise ValueError("最多同时运行 4 个实验")
            manifest = await prepare(plan, spec.budget)
            run = {
                "id": uuid.uuid4().hex,
                "name": spec.name,
                "mode": "live",
                "status": "queued",
                "created_at": now(),
                "max_jobs": manifest["max_jobs"],
                "per_job": manifest["per_job"],
                "max_episodes": manifest["max_episodes"],
                "budget": spec.budget,
                "manifest": manifest,
                "jobs": [],
                "events": [],
            }
            for benchmark in manifest["benchmarks"]:
                for variant in manifest["variants"]:
                    for trial in range(manifest["trials"]):
                        run["jobs"].append(
                            {
                                "id": uuid.uuid4().hex,
                                "benchmark": benchmark,
                                "variant_id": variant["id"],
                                "trial": trial,
                                "target_name": variant["connection"]["model"],
                                "status": "queued",
                                "completed": 0,
                                "scored": 0,
                                "total": manifest["sample_size"],
                                "correct": 0,
                                "cost": None,
                                "billing_status": "pending",
                                "latencies": [],
                                "episodes": [],
                                "error": None,
                            }
                        )
            self.runs[run["id"]] = run
            self.event(run, "已冻结执行镜像、题集、价格上界和预算")
            self.tasks[run["id"]] = asyncio.create_task(execute(self, run))
            return run
        if sum(r["status"] in {"queued", "running"} for r in self.runs.values()) >= 4:
            raise ValueError("最多同时运行 4 个实验，请等待或取消已有运行")
        health = await self.health()
        if not health["docker"]:
            raise ValueError("Docker 未连接，请启动本机 Docker 服务")
        if not health["image"]:
            raise ValueError("自检镜像尚未构建，请运行 make console-smoke-image")
        _, digest = await docker_command("image", "inspect", SMOKE_IMAGE, "--format", "{{.Id}}")
        if not digest.startswith("sha256:"):
            raise ValueError("无法冻结镜像 digest")
        run = {
            "id": uuid.uuid4().hex,
            "name": spec.name,
            "mode": spec.mode,
            "status": "queued",
            "created_at": now(),
            "max_jobs": spec.max_jobs,
            "per_job": spec.per_job,
            "max_episodes": spec.max_episodes,
            "budget": spec.budget,
            "jobs": [],
            "events": [],
            "manifest": {
                **spec.model_dump(),
                "image_digest": digest,
                "protocol": "acp-v1",
                "synthetic": True,
            },
        }
        for benchmark in spec.benchmarks:
            for trial in range(spec.trials):
                run["jobs"].append(
                    {
                        "id": uuid.uuid4().hex,
                        "benchmark": benchmark,
                        "target_name": f"合成 ACP 自检 · trial {trial + 1}",
                        "status": "queued",
                        "completed": 0,
                        "total": spec.sample_size,
                        "correct": 0,
                        "cost": 0.0,
                        "latencies": [],
                        "episodes": [],
                        "error": None,
                    }
                )
        self.runs[run["id"]] = run
        self.event(run, "计划已冻结；沙箱自检不调用模型，不产生 benchmark 成绩")
        self.tasks[run["id"]] = asyncio.create_task(self.execute(run, digest))
        return run

    async def execute(self, run: dict[str, Any], digest: str) -> None:
        slots = asyncio.Semaphore(run["max_episodes"])
        job_slots = asyncio.Semaphore(run["max_jobs"])
        run["status"] = "running"
        self.event(run, "开始运行；并行作业与全局容器上限已生效")

        async def job_work(job: dict[str, Any]) -> None:
            async with job_slots:
                job["status"] = "running"
                job_started = time.monotonic()
                self.event(run, f"作业启动：{job['benchmark']}", job["id"])
                case_slots = asyncio.Semaphore(run["per_job"])

                async def episode(index: int) -> None:
                    async with case_slots, slots, self.global_slots:
                        name = f"lyra-{run['id'][:12]}-{job['id'][:8]}-{index}"
                        self.event(
                            run, f"创建独立容器：{job['benchmark']} / {index + 1}", job["id"]
                        )
                        try:
                            passed, latency = await smoke_episode(name, digest)
                            job["completed"] += 1
                            job["correct"] += int(passed)
                            job["latencies"].append(latency)
                            job.setdefault("episodes", []).append(
                                {
                                    "case_id": str(index),
                                    "status": "scored",
                                    "correct": passed,
                                    "latency_ms": latency,
                                    "cost_usd": 0.0,
                                    "synthetic": True,
                                }
                            )
                            if not passed:
                                job["error"] = "ACP 自检输出不符合约定"
                            self.event(
                                run,
                                f"完成题目：{job['benchmark']} / {index + 1}"
                                f" · 自检{'通过' if passed else '失败'} · {latency} ms",
                                job["id"],
                            )
                        except (RuntimeError, OSError, TimeoutError) as exc:
                            job["completed"] += 1
                            job.setdefault("episodes", []).append(
                                {
                                    "case_id": str(index),
                                    "status": "system_failed",
                                    "correct": False,
                                    "latency_ms": None,
                                    "cost_usd": 0.0,
                                    "synthetic": True,
                                }
                            )
                            job["error"] = f"沙箱自检失败：{type(exc).__name__}"
                            self.event(run, job["error"], job["id"])

                async with asyncio.TaskGroup() as group:
                    for index in range(job["total"]):
                        group.create_task(episode(index))
                job["wall_time_ms"] = round((time.monotonic() - job_started) * 1000)
                job["status"] = "failed" if job["error"] else "completed"
                self.event(run, f"作业结束：{job['benchmark']} · {job['status']}", job["id"])

        try:
            async with asyncio.TaskGroup() as group:
                for job in run["jobs"]:
                    group.create_task(job_work(job))
            run["status"] = (
                "failed" if any(j["status"] == "failed" for j in run["jobs"]) else "completed"
            )
        except asyncio.CancelledError:
            run["status"] = "cancelled"
            for job in run["jobs"]:
                if job["status"] in {"running", "queued"}:
                    job["status"] = "cancelled"
        except Exception:
            run["status"] = "failed"
            for job in run["jobs"]:
                if job["status"] in {"running", "queued"}:
                    job["status"] = "failed"
                    job["error"] = "调度异常；未产生模型成绩"
        finally:
            self.event(run, f"运行结束：{run['status']}")
            self.tasks.pop(run["id"], None)

    async def cancel(self, identifier: str) -> dict[str, Any]:
        run = self.runs[identifier]
        task = self.tasks.get(identifier)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if run["status"] in {"queued", "running"}:
                run["status"] = "cancelled"
                for job in run["jobs"]:
                    job["status"] = "cancelled"
                self.event(run, "排队任务已取消")
        return run

    async def close(self) -> None:
        for identifier in list(self.tasks):
            await self.cancel(identifier)
