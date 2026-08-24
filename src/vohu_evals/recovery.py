from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vohu_evals.benchmark import Benchmark
from vohu_evals.config import canonical_hash
from vohu_evals.ledger import SQLiteLedger
from vohu_evals.models import Case, CaseScore, CaseStatus


@dataclass(frozen=True)
class RecoveryPlan:
    source_run_id: str
    selected_case_ids: tuple[str, ...]
    selection_sha256: str
    source_total_cases: int
    preserved_completed_cases: int

    @property
    def manifest_derivation(self) -> dict[str, Any]:
        return {
            "type": "recovery",
            "source_run_id": self.source_run_id,
            "selection_policy": "pending_or_system_failed",
            "selected_cases": len(self.selected_case_ids),
            "selection_sha256": self.selection_sha256,
        }


def plan_recovery(
    source: SQLiteLedger,
    *,
    source_run_id: str,
    benchmark: str,
    profile: str,
    cases: list[Case],
) -> RecoveryPlan:
    manifest = source.run_manifest(source_run_id)
    if manifest.get("benchmark") != benchmark:
        raise ValueError("source run benchmark does not match recovery benchmark")
    if manifest.get("profile") != profile:
        raise ValueError("source run profile does not match recovery profile")
    rows = source.case_rows(source_run_id)
    row_by_id = {str(row["case_id"]): row for row in rows}
    expected_ids = {case.case_id for case in cases}
    if set(row_by_id) != expected_ids:
        raise ValueError("source run case set does not match frozen benchmark")
    selected = tuple(
        case.case_id
        for case in cases
        if CaseStatus(str(row_by_id[case.case_id]["status"]))
        in {CaseStatus.PENDING, CaseStatus.SYSTEM_FAILED}
    )
    if not selected:
        raise ValueError("source run has no pending or system_failed cases to recover")
    preserved = sum(CaseStatus(str(row["status"])) is CaseStatus.COMPLETED for row in rows)
    return RecoveryPlan(
        source_run_id=source_run_id,
        selected_case_ids=selected,
        selection_sha256=canonical_hash({"case_ids": selected}),
        source_total_cases=len(rows),
        preserved_completed_cases=preserved,
    )


def build_recovery_evidence(
    source: SQLiteLedger,
    recovery: SQLiteLedger,
    benchmark: Benchmark,
    *,
    source_run_id: str,
    recovery_run_id: str,
    selected_case_ids: tuple[str, ...],
    destination: Path,
) -> Path:
    selected = set(selected_case_ids)
    source_rows = source.case_rows(source_run_id)
    recovery_rows = recovery.case_rows(recovery_run_id)
    recovery_by_id = {str(row["case_id"]): row for row in recovery_rows}
    if set(recovery_by_id) != selected:
        raise ValueError("recovery ledger case set does not match frozen selection")
    manifest = recovery.run_manifest(recovery_run_id)
    derivation = manifest.get("derivation")
    if not isinstance(derivation, dict) or derivation.get("source_run_id") != source_run_id:
        raise ValueError("recovery manifest source does not match")
    if derivation.get("selection_sha256") != canonical_hash({"case_ids": selected_case_ids}):
        raise ValueError("recovery manifest selection hash does not match")

    combined_rows = [
        recovery_by_id[str(row["case_id"])] if str(row["case_id"]) in selected else row
        for row in source_rows
    ]
    status_counts = {
        status.value: sum(str(row["status"]) == status.value for row in combined_rows)
        for status in CaseStatus
    }
    correct_cases = sum(bool(row["correct"]) for row in combined_rows)
    total_cost = (
        source.summary(source_run_id).total_cost_usd
        + recovery.summary(recovery_run_id).total_cost_usd
    )
    scores: list[CaseScore] = []
    for row in combined_rows:
        details = json.loads(row["score_details_json"] or "{}")
        metadata = json.loads(row["metadata_json"] or "{}")
        if row["status"] != CaseStatus.COMPLETED and metadata.get("official_evaluator"):
            instruction_count = len(metadata.get("instruction_id_list", []))
            details = {
                "strict_instruction_list": [False] * instruction_count,
                "loose_instruction_list": [False] * instruction_count,
                "strict_prompt": False,
                "loose_prompt": False,
            }
        scores.append(
            CaseScore(
                value=float(row["score"] or 0.0),
                correct=bool(row["correct"]),
                metric=str(row["metric"] or benchmark.manifest["metric"]),
                details=details,
            )
        )
    benchmark_metrics = benchmark.aggregate(scores)
    total_cases = len(combined_rows)
    completed_cases = status_counts[CaseStatus.COMPLETED]
    system_failed_cases = status_counts[CaseStatus.SYSTEM_FAILED]
    invalid_output_cases = status_counts[CaseStatus.INVALID_OUTPUT]
    pending_cases = status_counts[CaseStatus.PENDING]
    payload = {
        "schema_version": "1.0",
        "run_id": recovery_run_id,
        "publishable": (
            completed_cases == total_cases
            and system_failed_cases == 0
            and invalid_output_cases == 0
            and pending_cases == 0
        ),
        "publication_status": (
            "publication_ready"
            if completed_cases == total_cases
            and system_failed_cases == 0
            and invalid_output_cases == 0
            and pending_cases == 0
            else "internal_only"
        ),
        "derivation": derivation,
        "metrics": {
            "score_percent": round(correct_cases / total_cases * 100, 4),
            "total_cases": total_cases,
            "completed_cases": completed_cases,
            "pending_cases": pending_cases,
            "correct_cases": correct_cases,
            "system_failed_cases": system_failed_cases,
            "invalid_output_cases": invalid_output_cases,
            "total_requests": source.summary(source_run_id).total_requests
            + recovery.summary(recovery_run_id).total_requests,
            "recovery_requests": recovery.summary(recovery_run_id).total_requests,
            "total_cost_usd": round(total_cost, 6),
            "average_cost_usd": round(total_cost / total_cases, 9) if total_cases else 0.0,
            "benchmark": benchmark_metrics,
        },
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination
