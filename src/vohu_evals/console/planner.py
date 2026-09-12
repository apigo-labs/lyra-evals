"""Offline, immutable experiment planning. Capability claims never enable execution."""

from __future__ import annotations

import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vohu_evals.console.contracts import BENCHMARK_IDS


class VariantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1, max_length=100)
    harness: Literal["codex", "claude_agent"]
    efforts: list[str] = Field(min_length=1, max_length=8)
    max_output_tokens: int = Field(default=16384, ge=1, le=1000000)
    deadline_seconds: int = Field(default=600, ge=1, le=86400)
    episode_budget: Decimal = Field(default=Decimal("1"), gt=0, le=10000, allow_inf_nan=False)

    @model_validator(mode="after")
    def efforts_valid(self):
        allowed = {"provider_default", "low", "medium", "high", "xhigh", "max"}
        if not set(self.efforts) <= allowed:
            raise ValueError("未知 effort；必须使用受支持的原生参数")
        return self


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    benchmarks: list[str] = Field(min_length=1, max_length=5)
    variants: list[VariantInput] = Field(min_length=1, max_length=32)
    swe_subset: Literal["lite", "verified"] = "verified"
    sample_size: int = Field(default=3, ge=1, le=10000)
    trials: int = Field(default=1, ge=1, le=10)
    tau_simulator_target_id: str | None = None
    tau_simulator_budget: Decimal = Field(
        default=Decimal("0.5"), gt=0, le=10000, allow_inf_nan=False
    )
    seed: int = Field(default=0, ge=0, le=2147483647)
    budget: Decimal = Field(default=Decimal("5"), gt=0, le=10000, allow_inf_nan=False)
    budget_policy: Literal["capability", "equal_cost", "equal_time"] = "capability"
    max_jobs: int = Field(default=2, ge=1, le=8)
    per_job: int = Field(default=2, ge=1, le=8)
    max_episodes: int = Field(default=4, ge=1, le=16)

    @model_validator(mode="after")
    def valid_matrix(self):
        if (
            len(set(self.benchmarks)) != len(self.benchmarks)
            or not set(self.benchmarks) <= BENCHMARK_IDS
        ):
            raise ValueError("评测集无效或重复")
        if (
            self.budget_policy == "equal_cost"
            and len({v.episode_budget for v in self.variants}) > 1
        ):
            raise ValueError("等成本比较必须使用相同单题预算")
        if (
            self.budget_policy == "equal_time"
            and len({v.deadline_seconds for v in self.variants}) > 1
        ):
            raise ValueError("等时间比较必须使用相同 deadline")
        return self


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def capabilities(target: dict) -> dict:
    # These are configurable adapter values, deliberately NOT provider capability assertions.
    protocol = target["protocol"]
    harnesses = (
        ["codex"]
        if protocol == "openai_responses"
        else ["claude_agent"]
        if protocol == "anthropic_messages"
        else []
    )
    if target["model"].startswith("gpt-"):
        harnesses = [h for h in harnesses if h == "codex"]
    if target["model"].startswith("claude-"):
        harnesses = [h for h in harnesses if h == "claude_agent"]
    return {
        "target_id": target["id"],
        "harnesses": harnesses,
        "candidate_efforts": ["provider_default", "low", "medium", "high", "xhigh"]
        if "codex" in harnesses
        else ["provider_default", "low", "medium", "high", "max"]
        if harnesses
        else [],
        "verified_efforts": [],
        "status": "unverified",
        "mapping_version": "1",
        "reason": "仅为适配器候选参数；本目标原生 effort、工具与流式尚未预检",
    }


def build_plan(spec: PlanInput, targets: list[dict], suite_root: Path | None = None) -> dict:
    by_id = {t["id"]: t for t in targets}
    expanded = {}
    for choice in spec.variants:
        target = by_id.get(choice.target_id)
        if target is None:
            raise ValueError("模型连接不存在")
        capability = capabilities(target)
        if choice.harness not in capability["harnesses"]:
            raise ValueError("Harness 与模型/协议不兼容，不允许静默转换")
        for effort in sorted(set(choice.efforts)):
            if effort not in capability["candidate_efforts"]:
                raise ValueError("effort 不属于该适配器的候选参数")
            variant = {
                **choice.model_dump(mode="json", exclude={"efforts"}),
                "episode_budget": str(choice.episode_budget.normalize()),
                "effort": effort,
                "effort_status": "unverified",
                "effective_effort": None,
                "connection": {
                    **{k: target[k] for k in ("id", "endpoint", "model", "protocol")},
                    "price_cap": target.get("price_cap"),
                },
                "capability": capability,
                "harness_version": None,
                "image_digest": None,
            }
            identifier = digest(variant)
            expanded[identifier] = {"id": identifier, **variant}
    simulator = None
    if "tau2" in spec.benchmarks:
        target = by_id.get(spec.tau_simulator_target_id)
        if target is None:
            raise ValueError("τ² 需要选择固定的用户模拟器/Judge 模型")
        simulator = {
            "target_id": target["id"],
            "budget": str(spec.tau_simulator_budget),
            "connection": {
                **{k: target[k] for k in ("id", "endpoint", "model", "protocol")},
                "price_cap": target.get("price_cap"),
            },
            "protocol": "anthropic_messages"
            if target["protocol"] == "anthropic_messages"
            else "openai_chat_completions",
        }
    variants = [expanded[k] for k in sorted(expanded)]
    jobs = len(spec.benchmarks) * len(variants) * spec.trials
    episodes = jobs * spec.sample_size
    if episodes > 10000:
        raise ValueError("展开后单次计划最多 10000 个 Episode")
    blockers = [
        "目标与 effort 未完成真实协议预检",
        "执行前将冻结 Agent / 转发器镜像 digest",
        "当前验收为 reference_only；工具与评分工件需继续完善比较证据",
        "Gateway 实际账单尚未对账，当前提供公开价格上界估算",
        "执行前检查公开价格与全部单题预留，缺价格或超预算拒绝启动",
    ]
    datasets = {}
    if suite_root is not None:
        from vohu_evals.suites.packs import dataset_root, load_pack

        for benchmark in sorted(spec.benchmarks):
            try:
                source, cases = load_pack(
                    dataset_root(suite_root, benchmark, spec.swe_subset), benchmark
                )
            except FileNotFoundError:
                blockers.append(f"{benchmark}: 数据尚未准备")
                continue
            if spec.sample_size > len(cases):
                raise ValueError(f"{benchmark}: 请求题数超过已冻结题集")
            case_ids = sorted(row["case_id"] for row in cases)
            random.Random(f"{spec.seed}:{benchmark}").shuffle(case_ids)
            datasets[benchmark] = {
                "sha256": source["sha256"],
                "source": source["source"],
                "case_ids": case_ids[: spec.sample_size],
                "total": len(cases),
            }
    manifest = {
        **spec.model_dump(mode="json", exclude={"variants"}),
        "budget": str(spec.budget.normalize()),
        "benchmarks": sorted(spec.benchmarks),
        "schema_version": 2,
        "datasets": datasets,
        "simulator": simulator,
        "variants": variants,
        "profiles": {b: {"hash": None, "status": "unverified"} for b in sorted(spec.benchmarks)},
        "web_search": False,
        "comparison": "reference_only",
        "comparison_reason": "跨 Harness 或执行证据未完成验证",
    }
    return {
        "id": digest(manifest),
        "manifest": manifest,
        "jobs": jobs,
        "episodes": episodes,
        "estimated_cost": None,
        "reserved_cost": "0",
        "budget": manifest["budget"],
        "episode_caps_total": str(
            sum(Decimal(v["episode_budget"]) for v in variants)
            * len(spec.benchmarks)
            * spec.trials
            * spec.sample_size
            + (
                spec.tau_simulator_budget * len(variants) * spec.trials * spec.sample_size
                if simulator
                else 0
            )
        ),
        "blockers": blockers,
        "executable": False,
    }
