from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import Any

from vohu_evals.audit import AuditError, AuditPort
from vohu_evals.benchmark import Benchmark
from vohu_evals.gateway import GatewayError, GatewayPort
from vohu_evals.judge import JudgePort
from vohu_evals.ledger import SQLiteLedger
from vohu_evals.models import Case, CaseStatus, RunSpec, RunSummary
from vohu_evals.policy import (
    CompositionPolicyError,
    validate_china_model_composition,
    validate_expected_composition,
)


class BudgetExceededError(RuntimeError):
    pass


class EvaluationRunner:
    """Deep module owning budget, retry, policy, persistence and scoring behaviour."""

    def __init__(
        self,
        gateway: GatewayPort,
        ledger: SQLiteLedger,
        *,
        audit: AuditPort | None = None,
        judge: JudgePort | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.gateway = gateway
        self.ledger = ledger
        self.audit = audit
        self.judge = judge
        self.sleeper = sleeper

    def execute(
        self,
        benchmark: Benchmark,
        spec: RunSpec,
        cases: list[Case],
        manifest: dict[str, Any],
    ) -> RunSummary:
        benchmark.validate(spec.stage)
        benchmark.bind_judge(self.judge)
        self.ledger.initialize(spec, manifest, cases)
        case_by_id = {case.case_id: case for case in cases}
        started = time.monotonic()

        for case_id in self.ledger.pending_case_ids(spec.run_id):
            summary = self.ledger.summary(spec.run_id)
            self._check_budget(spec, summary, started)
            case = case_by_id[case_id]
            request = benchmark.build_request(case)
            retry_count = 0
            while True:
                try:
                    self._check_attempt_budget(spec, summary, started, retry_count)
                    attempt_request = replace(
                        request,
                        timeout_seconds=self._attempt_timeout_seconds(
                            spec,
                            summary,
                            started,
                            pending_cases=len(self.ledger.pending_case_ids(spec.run_id)),
                            attempts_in_case=retry_count,
                        ),
                    )
                    result = self.gateway.invoke(attempt_request)
                    if self.audit is not None:
                        audit = self.audit.resolve(result.request_id, result.execution_id or "")
                        result = replace(
                            result,
                            attempt_models=audit.attempt_models,
                            attempts=audit.attempts,
                            usage=audit.usage,
                            cost_usd=audit.cost_usd,
                        )
                    validate_china_model_composition(
                        spec.allowed_models,
                        result.attempt_models,
                        require_audit=spec.require_attempt_audit,
                    )
                    validate_expected_composition(
                        spec.expected_models,
                        result.attempt_models,
                        match=spec.expected_models_match,
                    )
                    parsed = benchmark.parse_response(result)
                    if not parsed:
                        self.ledger.fail(
                            spec.run_id,
                            case_id,
                            CaseStatus.INVALID_OUTPUT,
                            "empty parsed output",
                            retry_count,
                        )
                        break
                    score = benchmark.score_case(case, parsed, result)
                    self.ledger.complete(spec.run_id, case_id, result, score, retry_count)
                    break
                except GatewayError as exc:
                    if exc.transient and retry_count < spec.max_retries:
                        self._wait_after_transient_failure(spec, started, retry_count)
                        retry_count += 1
                        continue
                    self.ledger.fail(
                        spec.run_id,
                        case_id,
                        CaseStatus.SYSTEM_FAILED,
                        str(exc),
                        retry_count,
                    )
                    if exc.transient and self.ledger.pending_case_ids(spec.run_id):
                        self._wait_after_transient_failure(spec, started, spec.max_retries)
                    break
                except AuditError as exc:
                    self.ledger.fail(
                        spec.run_id,
                        case_id,
                        CaseStatus.SYSTEM_FAILED,
                        str(exc),
                        retry_count,
                        result=result,
                    )
                    break
                except CompositionPolicyError as exc:
                    self.ledger.fail(
                        spec.run_id,
                        case_id,
                        CaseStatus.SYSTEM_FAILED,
                        str(exc),
                        retry_count,
                    )
                    raise
        self.ledger.mark_completed_if_settled(spec.run_id)
        return self.ledger.summary(spec.run_id)

    def _wait_after_transient_failure(
        self,
        spec: RunSpec,
        started: float,
        failure_index: int,
    ) -> None:
        delay = spec.retry_backoff_seconds[min(failure_index, len(spec.retry_backoff_seconds) - 1)]
        remaining_wall = spec.budget.max_wall_time_seconds - (time.monotonic() - started)
        if delay >= remaining_wall:
            raise BudgetExceededError("run lacks wall time for transient retry backoff")
        self.sleeper(delay)

    @staticmethod
    def _check_budget(spec: RunSpec, summary: RunSummary, started: float) -> None:
        if summary.total_cost_usd >= spec.budget.max_usd:
            raise BudgetExceededError("run reached max_usd")
        if summary.total_requests >= spec.budget.max_requests:
            raise BudgetExceededError("run reached max_requests")
        if time.monotonic() - started >= spec.budget.max_wall_time_seconds:
            raise BudgetExceededError("run reached max_wall_time")

    @staticmethod
    def _check_attempt_budget(
        spec: RunSpec, summary: RunSummary, started: float, attempts_in_case: int
    ) -> None:
        if summary.total_requests + attempts_in_case >= spec.budget.max_requests:
            raise BudgetExceededError("run reached max_requests before next attempt")
        if time.monotonic() - started >= spec.budget.max_wall_time_seconds:
            raise BudgetExceededError("run reached max_wall_time before next attempt")

    def _attempt_timeout_seconds(
        self,
        spec: RunSpec,
        summary: RunSummary,
        started: float,
        *,
        pending_cases: int,
        attempts_in_case: int,
    ) -> float:
        remaining_wall = spec.budget.max_wall_time_seconds - (time.monotonic() - started)
        remaining_request_budget = spec.budget.max_requests - (
            summary.total_requests + attempts_in_case
        )
        remaining_case_slots = pending_cases * (spec.max_retries + 1) - attempts_in_case
        remaining_slots = max(1, min(remaining_request_budget, remaining_case_slots))
        return max(1.0, remaining_wall / remaining_slots)


def manifest_for(spec: RunSpec, benchmark: Benchmark) -> dict[str, Any]:
    value = asdict(spec)
    value["stage"] = spec.stage.value
    value["allowed_models"] = sorted(spec.allowed_models)
    value["expected_models"] = sorted(spec.expected_models)
    value["benchmark_manifest"] = benchmark.manifest
    return value
