from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from vohu_evals.benchmark import Benchmark
from vohu_evals.gateway import extract_citations, extract_output_text
from vohu_evals.judge import JudgePort
from vohu_evals.ledger import SQLiteLedger
from vohu_evals.models import (
    Budget,
    Case,
    CaseStatus,
    InvocationResult,
    RunSpec,
    RunStage,
    RunSummary,
)


@dataclass(frozen=True)
class RescoreResult:
    summary: RunSummary
    evaluator_requests: int


@dataclass(frozen=True)
class RescorePlan:
    source_run_id: str
    completed_cases: int
    preserved_failed_cases: int
    evaluator_requests: int


def _json(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


def _invocation_from_row(row: dict[str, Any]) -> InvocationResult:
    payload = _json(row["response_json"], {})
    return InvocationResult(
        output_text=extract_output_text(payload),
        raw_response=payload,
        request_id=str(row["request_id"] or ""),
        execution_id=str(row["execution_id"]) if row["execution_id"] else None,
        response_model=str(row["response_model"] or ""),
        attempt_models=tuple(_json(row["attempt_models_json"], [])),
        attempts=tuple(_json(row["attempts_json"], [])),
        usage=dict(_json(row["usage_json"], {})),
        cost_usd=float(row["cost_usd"] or 0),
        latency_ms=int(row["latency_ms"] or 0),
        citations=extract_citations(payload),
    )


def plan_rescore(source: SQLiteLedger, benchmark: Benchmark, source_run_id: str) -> RescorePlan:
    if source.run_status(source_run_id) != "completed":
        raise ValueError("source run must be completed before rescoring")
    source_manifest = source.run_manifest(source_run_id)
    if source_manifest.get("benchmark") != benchmark.name:
        raise ValueError("source run benchmark does not match scorer")
    completed_cases = 0
    preserved_failed_cases = 0
    evaluator_requests = 0
    for row in source.case_rows(source_run_id):
        status = CaseStatus(str(row["status"]))
        if status is CaseStatus.PENDING:
            raise ValueError("source run contains pending cases")
        if status is not CaseStatus.COMPLETED:
            preserved_failed_cases += 1
            continue
        completed_cases += 1
        result = _invocation_from_row(row)
        case = Case(
            case_id=str(row["case_id"]),
            prompt=str(row["prompt"]),
            expected=_json(row["expected_json"], None),
            metadata=dict(_json(row["metadata_json"], {})),
        )
        evaluator_requests += benchmark.evaluator_requests_for_response(
            case, benchmark.parse_response(result)
        )
    return RescorePlan(
        source_run_id=source_run_id,
        completed_cases=completed_cases,
        preserved_failed_cases=preserved_failed_cases,
        evaluator_requests=evaluator_requests,
    )


def rescore_run(
    source: SQLiteLedger,
    destination: SQLiteLedger,
    benchmark: Benchmark,
    *,
    source_run_id: str,
    derived_run_id: str,
    judge: JudgePort,
    max_judge_requests: int,
) -> RescoreResult:
    plan = plan_rescore(source, benchmark, source_run_id)
    if plan.evaluator_requests > max_judge_requests:
        raise ValueError("rescore would exceed max_judge_requests")
    source_manifest = source.run_manifest(source_run_id)
    rows = source.case_rows(source_run_id)
    cases = [
        Case(
            case_id=str(row["case_id"]),
            prompt=str(row["prompt"]),
            expected=_json(row["expected_json"], None),
            metadata=dict(_json(row["metadata_json"], {})),
        )
        for row in rows
    ]
    budget_data = source_manifest["budget"]
    spec = RunSpec(
        run_id=derived_run_id,
        benchmark=str(source_manifest["benchmark"]),
        profile=str(source_manifest["profile"]),
        stage=RunStage(str(source_manifest["stage"])),
        trial_id=int(source_manifest["trial_id"]),
        protocol_version=str(benchmark.manifest["protocol_version"]),
        budget=Budget(
            float(budget_data["max_usd"]),
            int(budget_data["max_requests"]),
            int(budget_data["max_wall_time_seconds"]),
        ),
        allowed_models=frozenset(source_manifest["allowed_models"]),
        gateway_protocol=str(source_manifest.get("gateway_protocol", "openai_chat_completions")),
        expected_models=frozenset(source_manifest.get("expected_models", [])),
        require_attempt_audit=bool(source_manifest.get("require_attempt_audit", True)),
    )
    derived_manifest = dict(source_manifest)
    derived_manifest.update(
        {
            "run_id": derived_run_id,
            "protocol_version": spec.protocol_version,
            "benchmark_manifest": benchmark.manifest,
            "derivation": {
                "type": "rescore",
                "source_run_id": source_run_id,
                "source_protocol_version": source_manifest["protocol_version"],
                "target_requests": 0,
            },
        }
    )
    destination.initialize(spec, derived_manifest, cases)
    benchmark.bind_judge(judge)
    case_by_id = {case.case_id: case for case in cases}
    row_by_id = {str(row["case_id"]): row for row in rows}
    evaluator_requests = 0
    for case_id in destination.pending_case_ids(derived_run_id):
        case = case_by_id[case_id]
        row = row_by_id[case_id]
        status = CaseStatus(str(row["status"]))
        if status is CaseStatus.COMPLETED:
            result = _invocation_from_row(row)
            parsed = benchmark.parse_response(result)
            needed = benchmark.evaluator_requests_for_response(case, parsed)
            if evaluator_requests + needed > max_judge_requests:
                raise ValueError("rescore would exceed max_judge_requests")
            score = benchmark.score_case(case, parsed, result)
            evaluator_requests += int(score.details.get("evaluator_requests", 0))
            destination.complete(
                derived_run_id, case_id, result, score, int(row["retry_count"] or 0)
            )
            continue
        if status is CaseStatus.PENDING:
            raise ValueError("source run contains pending cases")
        failure_result = _invocation_from_row(row) if row["response_json"] else None
        destination.fail(
            derived_run_id,
            case_id,
            status,
            str(row["error"] or "source run failure"),
            int(row["retry_count"] or 0),
            result=failure_result,
        )
    destination.mark_completed_if_settled(derived_run_id)
    total_evaluator_requests = sum(
        int(_json(row["score_details_json"], {}).get("evaluator_requests", 0))
        for row in destination.case_rows(derived_run_id)
    )
    return RescoreResult(destination.summary(derived_run_id), total_evaluator_requests)
