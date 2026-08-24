from __future__ import annotations

import json
from pathlib import Path

from vohu_evals.benchmark import load_benchmark
from vohu_evals.ledger import SQLiteLedger
from vohu_evals.models import (
    Budget,
    Case,
    CaseScore,
    CaseStatus,
    InvocationResult,
    RunSpec,
    RunStage,
)
from vohu_evals.recovery import build_recovery_evidence, plan_recovery
from vohu_evals.reporting import build_evidence
from vohu_evals.runner import manifest_for

ROOT = Path(__file__).resolve().parents[1]


def _result(request_id: str, cost: float = 0.1) -> InvocationResult:
    return InvocationResult(
        output_text="B",
        raw_response={"choices": [{"message": {"content": "B"}}]},
        request_id=request_id,
        execution_id=f"execution-{request_id}",
        response_model="apigo/vohu",
        attempt_models=("qwen-fixture",),
        attempts=({"model": "qwen-fixture", "status": "success"},),
        usage={"total_tokens": 10},
        cost_usd=cost,
        latency_ms=10,
    )


def test_recovery_selects_only_unfinished_cases_and_combines_without_mutating_source(
    tmp_path: Path,
) -> None:
    benchmark = load_benchmark(ROOT, "gpqa")
    cases = [
        Case("kept", "question", "B"),
        Case("failed", "question", "B"),
        Case("pending", "question", "B"),
    ]
    source_spec = RunSpec(
        run_id="source-run",
        benchmark="gpqa",
        profile="vohu-quality-v1",
        stage=RunStage.PUBLICATION,
        trial_id=1,
        protocol_version="fixture",
        budget=Budget(10, 10, 60),
        allowed_models=frozenset({"qwen-fixture"}),
    )
    source = SQLiteLedger(tmp_path / "source.sqlite3")
    destination = SQLiteLedger(tmp_path / "recovery.sqlite3")
    try:
        source.initialize(source_spec, manifest_for(source_spec, benchmark), cases)
        source.complete(
            source_spec.run_id,
            "kept",
            _result("kept", 0.2),
            CaseScore(1, True, "accuracy"),
            0,
        )
        source.fail(source_spec.run_id, "failed", CaseStatus.SYSTEM_FAILED, "gateway 502", 2)

        plan = plan_recovery(
            source,
            source_run_id=source_spec.run_id,
            benchmark="gpqa",
            profile="vohu-quality-v1",
            cases=cases,
        )
        assert plan.selected_case_ids == ("failed", "pending")
        assert plan.preserved_completed_cases == 1

        recovery_cases = [case for case in cases if case.case_id in plan.selected_case_ids]
        recovery_spec = RunSpec(
            **{
                **source_spec.__dict__,
                "run_id": "recovery-run",
                "trial_id": 2,
                "budget": Budget(2, 6, 60),
            }
        )
        recovery_manifest = manifest_for(recovery_spec, benchmark)
        recovery_manifest["derivation"] = plan.manifest_derivation
        destination.initialize(recovery_spec, recovery_manifest, recovery_cases)
        destination.complete(
            recovery_spec.run_id,
            "failed",
            _result("retried", 0.1),
            CaseScore(1, True, "accuracy"),
            0,
        )
        destination.fail(
            recovery_spec.run_id,
            "pending",
            CaseStatus.SYSTEM_FAILED,
            "gateway 502",
            2,
        )

        subset_evidence = build_evidence(
            destination, recovery_spec.run_id, tmp_path / "subset-evidence", benchmark
        )
        subset_payload = json.loads(subset_evidence.read_text(encoding="utf-8"))
        assert subset_payload["publishable"] is False
        assert subset_payload["derivation"]["type"] == "recovery"

        evidence = build_recovery_evidence(
            source,
            destination,
            benchmark,
            source_run_id=source_spec.run_id,
            recovery_run_id=recovery_spec.run_id,
            selected_case_ids=plan.selected_case_ids,
            destination=tmp_path / "combined-summary.json",
        )
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        assert payload["publishable"] is False
        assert payload["metrics"]["total_cases"] == 3
        assert payload["metrics"]["completed_cases"] == 2
        assert payload["metrics"]["system_failed_cases"] == 1
        assert payload["metrics"]["correct_cases"] == 2
        assert payload["metrics"]["total_cost_usd"] == 0.3
        assert source.summary(source_spec.run_id).system_failed_cases == 1
        assert source.pending_case_ids(source_spec.run_id) == ("pending",)
    finally:
        source.close()
        destination.close()
