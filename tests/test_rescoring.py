from __future__ import annotations

import json
from pathlib import Path

from vohu_evals.benchmark import load_benchmark
from vohu_evals.judge import JudgeResult
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
from vohu_evals.reporting import build_evidence, build_report_evidence
from vohu_evals.rescoring import rescore_run
from vohu_evals.runner import manifest_for

ROOT = Path(__file__).resolve().parents[1]


class CountingJudge:
    model = "judge-fixture"

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, prompt: str) -> JudgeResult:
        self.calls += 1
        return JudgeResult("correct: yes", f"judge-{self.calls}", self.model, {}, 1)


def _result(output: str, request_id: str, cost: float) -> InvocationResult:
    return InvocationResult(
        output_text=output,
        raw_response={"choices": [{"message": {"content": output}}]},
        request_id=request_id,
        execution_id=f"execution-{request_id}",
        response_model="apigo/vohu",
        attempt_models=("glm-5.2", "kimi-k3", "minimax-m3"),
        attempts=({"model": "glm-5.2", "status": "success"},),
        usage={"total_tokens": 10},
        cost_usd=cost,
        latency_ms=50,
    )


def test_rescore_derives_new_run_without_mutating_source_or_reinvoking_target(
    tmp_path: Path,
) -> None:
    benchmark = load_benchmark(ROOT, "browsecomp")
    cases = [
        Case("exact", "question", "beta", {"official_evaluator": True}),
        Case("alias", "question", "beta", {"official_evaluator": True}),
        Case("failed", "question", "beta", {"official_evaluator": True}),
    ]
    spec = RunSpec(
        run_id="source-run",
        benchmark="browsecomp",
        profile="vohu-research-v2",
        stage=RunStage.SMOKE,
        trial_id=1,
        protocol_version="broken-v1",
        budget=Budget(10, 20, 60),
        allowed_models=frozenset({"glm-5.2", "kimi-k3", "minimax-m3"}),
    )
    source = SQLiteLedger(tmp_path / "source.sqlite3")
    destination = SQLiteLedger(tmp_path / "derived.sqlite3")
    try:
        source.initialize(spec, manifest_for(spec, benchmark), cases)
        source.complete(
            spec.run_id,
            "exact",
            _result("Exact Answer: beta", "target-exact", 0.2),
            CaseScore(0, False, "accuracy", {"judge_verdict": "no"}),
            0,
        )
        source.complete(
            spec.run_id,
            "alias",
            _result("Exact Answer: beta alias", "target-alias", 0.3),
            CaseScore(0, False, "accuracy", {"judge_verdict": "no"}),
            0,
        )
        source.fail(spec.run_id, "failed", CaseStatus.SYSTEM_FAILED, "gateway 502", 2)
        source.mark_completed_if_settled(spec.run_id)
        judge = CountingJudge()

        result = rescore_run(
            source,
            destination,
            benchmark,
            source_run_id=spec.run_id,
            derived_run_id="derived-run",
            judge=judge,
            max_judge_requests=1,
        )

        assert result.summary.correct_cases == 2
        assert result.summary.system_failed_cases == 1
        assert result.summary.total_cost_usd == 0.5
        assert result.evaluator_requests == 1
        assert judge.calls == 1
        assert source.summary(spec.run_id).correct_cases == 0
        assert source.run_status(spec.run_id) == "completed"
        assert destination.run_status("derived-run") == "completed"
        assert destination.case_rows("derived-run")[0]["request_id"] == "target-alias"
        evidence = build_evidence(destination, "derived-run", tmp_path / "evidence", benchmark)
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        assert payload["publishable"] is False
        assert payload["derivation"]["source_run_id"] == "source-run"
        assert payload["metrics"]["target_requests"] == 0
        assert payload["metrics"]["inherited_target_requests"] == 5
        assert payload["metrics"]["benchmark"]["evaluator_requests"] == 1
        references = tmp_path / "references.json"
        references.write_text(json.dumps({"comparability": "reference_only"}), encoding="utf-8")
        report_evidence = build_report_evidence(
            destination,
            "derived-run",
            evidence,
            references,
            tmp_path / "evidence" / "report-summary.json",
        )
        report_payload = json.loads(report_evidence.read_text(encoding="utf-8"))
        assert report_payload["external_references"]["comparability"] == "reference_only"
        assert report_payload["reliability"]["system_failure_rate_percent"] == 33.3333
        assert (
            report_payload["protocol"]["scorer_protocol_version"]
            == benchmark.manifest["protocol_version"]
        )
    finally:
        source.close()
        destination.close()
