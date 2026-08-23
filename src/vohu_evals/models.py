from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = (30.0, 90.0, 120.0)


class RunStage(StrEnum):
    FIXTURE = "fixture"
    SMOKE = "smoke"
    CALIBRATION = "calibration"
    PUBLICATION = "publication"


class CaseStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    SYSTEM_FAILED = "system_failed"
    INVALID_OUTPUT = "invalid_output"


@dataclass(frozen=True)
class Budget:
    max_usd: float
    max_requests: int
    max_wall_time_seconds: int

    def __post_init__(self) -> None:
        if self.max_usd <= 0 or self.max_requests <= 0 or self.max_wall_time_seconds <= 0:
            raise ValueError("all budget limits must be positive")


@dataclass(frozen=True)
class Case:
    case_id: str
    prompt: str
    expected: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    fixture_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class BenchmarkRequest:
    case_id: str
    prompt: str
    web_search: bool = False
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class InvocationResult:
    output_text: str
    raw_response: dict[str, Any]
    request_id: str
    execution_id: str | None
    response_model: str
    attempt_models: tuple[str, ...]
    usage: dict[str, Any]
    cost_usd: float
    latency_ms: int
    citations: tuple[dict[str, Any], ...] = ()
    attempts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class CaseScore:
    value: float
    correct: bool
    metric: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    benchmark: str
    profile: str
    stage: RunStage
    trial_id: int
    protocol_version: str
    budget: Budget
    allowed_models: frozenset[str]
    gateway_protocol: str = "openai_chat_completions"
    expected_models: frozenset[str] = frozenset()
    expected_models_match: str = "exact"
    require_attempt_audit: bool = True
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff_seconds: tuple[float, ...] = DEFAULT_RETRY_BACKOFF_SECONDS

    def __post_init__(self) -> None:
        if self.expected_models_match not in {"exact", "subset"}:
            raise ValueError("expected_models_match must be exact or subset")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if len(self.retry_backoff_seconds) < self.max_retries + 1:
            raise ValueError("retry_backoff_seconds must cover retries and recovery cooldown")
        if any(value <= 0 for value in self.retry_backoff_seconds):
            raise ValueError("retry_backoff_seconds must contain positive delays")


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    total_cases: int
    completed_cases: int
    correct_cases: int
    system_failed_cases: int
    invalid_output_cases: int
    total_requests: int
    total_cost_usd: float

    @property
    def score(self) -> float:
        return self.correct_cases / self.total_cases if self.total_cases else 0.0
