from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BENCHMARKS = [
    {
        "id": "ifeval",
        "name": "IFEval",
        "description": "指令遵循与整题约束",
        "status": "评分已接入",
        "cases": 541,
        "tools": "无工具 · 无网络",
        "live_ready": False,
        "reason": "官方评分已接入；Claude ACP 无工具 profile、正式费用审计与数据就绪门禁待贯通。",
    },
    {
        "id": "gpqa",
        "name": "GPQA Diamond",
        "description": "高难科学知识与推理",
        "status": "脚手架",
        "cases": 198,
        "tools": "闭卷 · 无工具",
        "live_ready": False,
        "reason": "待冻结合法数据工件、选项打乱协议和 Claude ACP 执行配置。",
    },
    {
        "id": "livecodebench",
        "name": "LiveCodeBench",
        "description": "代码生成与执行正确性",
        "status": "待接入",
        "cases": None,
        "tools": "生成代码 · 独立评分",
        "live_ready": False,
        "reason": "待固定数据版本与时间范围，接入独立隐藏测试沙箱。",
    },
    {
        "id": "tau2",
        "name": "τ²-bench",
        "description": "多轮工具与业务任务完成",
        "status": "待接入",
        "cases": None,
        "tools": "领域工具 · 用户模拟器",
        "live_ready": False,
        "reason": "待接入用户交互桥、领域工具和最终环境状态评分。",
    },
    {
        "id": "swebench",
        "name": "SWE-bench",
        "description": "真实仓库问题修复",
        "status": "待接入",
        "cases": None,
        "tools": "文件 · 终端 · 可见测试",
        "live_ready": False,
        "reason": "待固定子集、实例镜像和 patch 评分流程。",
    },
]
BENCHMARK_IDS = {item["id"] for item in BENCHMARKS}


class PriceCap(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    input_usd_per_1m: float = Field(gt=0, le=10000)
    output_usd_per_1m: float = Field(gt=0, le=10000)


class TargetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    endpoint: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    protocol: Literal["anthropic_messages", "openai_chat_completions", "openai_responses"]
    api_key: str = Field(min_length=1, max_length=4096, repr=False)

    @field_validator("endpoint")
    @classmethod
    def endpoint_valid(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Endpoint 必须是无凭据、查询参数及 fragment 的 HTTP(S) 基础地址")
        return value.rstrip("/")


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    mode: Literal["smoke", "live"]
    benchmarks: list[str] = Field(min_length=1, max_length=5)
    targets: list[str] = Field(default_factory=list, max_length=32)
    sample_size: int = Field(default=3, ge=1, le=10000)
    trials: int = Field(default=1, ge=1, le=10)
    max_jobs: int = Field(default=2, ge=1, le=8)
    per_job: int = Field(default=2, ge=1, le=8)
    max_episodes: int = Field(default=4, ge=1, le=16)
    budget: float = Field(default=1, gt=0, le=10000, allow_inf_nan=False)
    confirm_budget: bool = False
    plan_id: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_matrix(self) -> RunInput:
        if (
            len(set(self.benchmarks)) != len(self.benchmarks)
            or not set(self.benchmarks) <= BENCHMARK_IDS
        ):
            raise ValueError("评测集必须来自固定清单且不能重复")
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("模型不能重复")
        if self.mode == "live" and (not self.targets or not self.confirm_budget):
            raise ValueError("真实评测需要模型及显式预算确认")
        if self.mode == "smoke" and (self.sample_size > 20 or self.targets):
            raise ValueError("自检每项最多 20 题，不接受真实模型")
        if (
            len(self.benchmarks) * max(1, len(self.targets)) * self.trials * self.sample_size
            > 10000
        ):
            raise ValueError("单次计划最多 10000 个 Episode")
        return self
