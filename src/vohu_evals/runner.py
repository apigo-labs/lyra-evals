from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, replace
from threading import Lock
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
        self._score_lock = Lock()

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

        pending_case_ids = self.ledger.pending_case_ids(spec.run_id)
        if spec.max_concurrency == 1:
            for case_id in pending_case_ids:
                self._execute_case(benchmark, spec, case_by_id[case_id], started)
        else:
            self._execute_concurrently(benchmark, spec, case_by_id, pending_case_ids, started)
        self.ledger.mark_completed_if_settled(spec.run_id)
        return self.ledger.summary(spec.run_id)

    def _execute_concurrently(
        self,
        benchmark: Benchmark,
        spec: RunSpec,
        case_by_id: dict[str, Case],
        pending_case_ids: tuple[str, ...],
        started: float,
    ) -> None:
        remaining = iter(pending_case_ids)
        futures: set[Future[None]] = set()
        with ThreadPoolExecutor(
            max_workers=spec.max_concurrency,
            thread_name_prefix="vohu-eval",
        ) as executor:
            while len(futures) < spec.max_concurrency:
                case_id = next(remaining, None)
                if case_id is None:
                    break
                futures.add(
                    executor.submit(
                        self._execute_case, benchmark, spec, case_by_id[case_id], started
                    )
                )
            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                try:
                    for future in done:
                        future.result()
                except Exception:
                    for future in futures:
                        future.cancel()
                    raise
                while len(futures) < spec.max_concurrency:
                    case_id = next(remaining, None)
                    if case_id is None:
                        break
                    futures.add(
                        executor.submit(
                            self._execute_case, benchmark, spec, case_by_id[case_id], started
                        )
                    )

    def _execute_case(
        self,
        benchmark: Benchmark,
        spec: RunSpec,
        case: Case,
        started: float,
    ) -> None:
        case_id = case.case_id
        request = benchmark.build_request(case)
        retry_count = self.ledger.reserved_attempt_count(spec.run_id, case_id)
        if retry_count > spec.max_retries:
            self.ledger.fail(
                spec.run_id,
                case_id,
                CaseStatus.SYSTEM_FAILED,
                "process stopped after all request attempts were reserved",
                spec.max_retries,
            )
            return
        while True:
            summary = self.ledger.summary(spec.run_id)
            self._check_budget(spec, summary, started)
            try:
                self._check_attempt_budget(spec, started)
                attempt_request = replace(
                    request,
                    timeout_seconds=self._attempt_timeout_seconds(
                        spec,
                        summary,
                        started,
                        pending_cases=len(self.ledger.pending_case_ids(spec.run_id)),
                    ),
                )
                attempt_no = self.ledger.reserve_attempt(
                    spec.run_id, case_id, spec.budget.max_requests
                )
                if attempt_no is None:
                    raise BudgetExceededError("run reached max_requests before next attempt")
                retry_count = attempt_no
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
                with self._score_lock:
                    parsed = benchmark.parse_response(result)
                    if not parsed:
                        self.ledger.fail(
                            spec.run_id,
                            case_id,
                            CaseStatus.INVALID_OUTPUT,
                            "empty parsed output",
                            retry_count,
                        )
                        return
                    score = benchmark.score_case(case, parsed, result)
                self.ledger.complete(spec.run_id, case_id, result, score, retry_count)
                return
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
                return
            except AuditError as exc:
                self.ledger.fail(
                    spec.run_id,
                    case_id,
                    CaseStatus.SYSTEM_FAILED,
                    str(exc),
                    retry_count,
                    result=result,
                )
                return
            except CompositionPolicyError as exc:
                self.ledger.fail(
                    spec.run_id,
                    case_id,
                    CaseStatus.SYSTEM_FAILED,
                    str(exc),
                    retry_count,
                )
                raise

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
    def _check_attempt_budget(spec: RunSpec, started: float) -> None:
        if time.monotonic() - started >= spec.budget.max_wall_time_seconds:
            raise BudgetExceededError("run reached max_wall_time before next attempt")

    def _attempt_timeout_seconds(
        self,
        spec: RunSpec,
        summary: RunSummary,
        started: float,
        *,
        pending_cases: int,
    ) -> float:
        remaining_wall = spec.budget.max_wall_time_seconds - (time.monotonic() - started)
        remaining_request_budget = spec.budget.max_requests - summary.total_requests
        remaining_case_slots = pending_cases * (spec.max_retries + 1)
        remaining_slots = max(1, min(remaining_request_budget, remaining_case_slots))
        return max(1.0, remaining_wall * spec.max_concurrency / remaining_slots)


def manifest_for(spec: RunSpec, benchmark: Benchmark) -> dict[str, Any]:
    value = asdict(spec)
    value["stage"] = spec.stage.value
    value["allowed_models"] = sorted(spec.allowed_models)
    value["expected_models"] = sorted(spec.expected_models)
    value["benchmark_manifest"] = benchmark.manifest
    return value
