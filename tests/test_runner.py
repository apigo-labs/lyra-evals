from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from vohu_evals.audit import AuditError, ExecutionAudit, PlatformLogsAuditAdapter
from vohu_evals.benchmark import load_benchmark
from vohu_evals.gateway import APIGOGatewayAdapter, FixtureGatewayAdapter, GatewayError
from vohu_evals.ledger import SQLiteLedger
from vohu_evals.models import Budget, RunSpec, RunStage
from vohu_evals.policy import CompositionPolicyError
from vohu_evals.runner import BudgetExceededError, EvaluationRunner, manifest_for

ROOT = Path(__file__).resolve().parents[1]


def _spec(run_id: str = "fixture-run") -> RunSpec:
    return RunSpec(
        run_id=run_id,
        benchmark="gpqa",
        profile="vohu-quality-v1",
        stage=RunStage.FIXTURE,
        trial_id=1,
        protocol_version="0.1-fixture",
        budget=Budget(max_usd=1, max_requests=20, max_wall_time_seconds=60),
        allowed_models=frozenset({"qwen-fixture"}),
    )


def test_runner_executes_and_resumes_without_duplicate_calls(tmp_path: Path) -> None:
    benchmark = load_benchmark(ROOT, "gpqa")
    cases = benchmark.enumerate_cases(RunStage.FIXTURE)
    responses = {case.case_id: case.fixture_response for case in cases}
    gateway = FixtureGatewayAdapter(responses)  # type: ignore[arg-type]
    ledger = SQLiteLedger(tmp_path / "run.sqlite3")
    spec = _spec()
    try:
        summary = EvaluationRunner(gateway, ledger).execute(
            benchmark, spec, cases, manifest_for(spec, benchmark)
        )
        assert summary.score == 1.0
        assert summary.total_cases == 2
        assert gateway.calls == 2
        assert ledger.run_status(spec.run_id) == "completed"

        EvaluationRunner(gateway, ledger).execute(
            benchmark, spec, cases, manifest_for(spec, benchmark)
        )
        assert gateway.calls == 2
    finally:
        ledger.close()


def test_runner_keeps_run_running_when_budget_leaves_pending_cases(tmp_path: Path) -> None:
    benchmark = load_benchmark(ROOT, "gpqa")
    cases = benchmark.enumerate_cases(RunStage.FIXTURE)
    gateway = FixtureGatewayAdapter(
        {case.case_id: case.fixture_response for case in cases}  # type: ignore[dict-item]
    )
    ledger = SQLiteLedger(tmp_path / "run.sqlite3")
    base = _spec("budget-interrupted")
    spec = RunSpec(**{**base.__dict__, "budget": Budget(1, 1, 60)})
    try:
        with pytest.raises(BudgetExceededError):
            EvaluationRunner(gateway, ledger).execute(
                benchmark, spec, cases, manifest_for(spec, benchmark)
            )
        assert ledger.run_status(spec.run_id) == "running"
        assert len(ledger.pending_case_ids(spec.run_id)) == 1
    finally:
        ledger.close()


def test_runner_fails_closed_on_non_china_attempt(tmp_path: Path) -> None:
    benchmark = load_benchmark(ROOT, "gpqa")
    cases = benchmark.enumerate_cases(RunStage.FIXTURE)[:1]
    response = dict(cases[0].fixture_response or {})
    response["attempt_models"] = ["gpt-5.6-sol"]
    gateway = FixtureGatewayAdapter({cases[0].case_id: response})
    ledger = SQLiteLedger(tmp_path / "run.sqlite3")
    spec = _spec("invalid-composition")
    try:
        with pytest.raises(CompositionPolicyError):
            EvaluationRunner(gateway, ledger).execute(
                benchmark, spec, cases, manifest_for(spec, benchmark)
            )
        assert ledger.summary(spec.run_id).system_failed_cases == 1
    finally:
        ledger.close()


class OnceTransientGateway:
    def __init__(self, delegate: FixtureGatewayAdapter) -> None:
        self.delegate = delegate
        self.failed = False

    def invoke(self, request):
        if not self.failed:
            self.failed = True
            raise GatewayError("temporary", transient=True)
        return self.delegate.invoke(request)


class AlwaysTransientGateway:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, _request):
        self.calls += 1
        raise GatewayError("temporary", transient=True)


class TimeoutRecordingGateway:
    def __init__(self, delegate: FixtureGatewayAdapter) -> None:
        self.delegate = delegate
        self.timeouts: list[float | None] = []

    def invoke(self, request):
        self.timeouts.append(request.timeout_seconds)
        return self.delegate.invoke(request)


def test_runner_allocates_attempt_timeout_across_all_pending_retry_slots(tmp_path: Path) -> None:
    benchmark = load_benchmark(ROOT, "gpqa")
    cases = benchmark.enumerate_cases(RunStage.FIXTURE)
    delegate = FixtureGatewayAdapter(
        {case.case_id: case.fixture_response for case in cases}  # type: ignore[dict-item]
    )
    gateway = TimeoutRecordingGateway(delegate)
    ledger = SQLiteLedger(tmp_path / "run.sqlite3")
    spec = _spec("bounded-attempts")
    try:
        EvaluationRunner(gateway, ledger).execute(
            benchmark, spec, cases, manifest_for(spec, benchmark)
        )
        assert gateway.timeouts[0] is not None
        assert 9.0 <= gateway.timeouts[0] <= 10.0
    finally:
        ledger.close()


def test_runner_retries_only_transient_failure(tmp_path: Path) -> None:
    benchmark = load_benchmark(ROOT, "gpqa")
    cases = benchmark.enumerate_cases(RunStage.FIXTURE)[:1]
    fixture = FixtureGatewayAdapter({cases[0].case_id: cases[0].fixture_response})  # type: ignore[dict-item]
    ledger = SQLiteLedger(tmp_path / "run.sqlite3")
    spec = _spec("retry-run")
    try:
        sleeps: list[float] = []
        summary = EvaluationRunner(
            OnceTransientGateway(fixture), ledger, sleeper=sleeps.append
        ).execute(benchmark, spec, cases, manifest_for(spec, benchmark))
        assert summary.total_requests == 2
        assert summary.correct_cases == 1
        assert sleeps == [30.0]
    finally:
        ledger.close()


def test_runner_backs_off_retries_and_cools_down_before_next_case(tmp_path: Path) -> None:
    benchmark = load_benchmark(ROOT, "gpqa")
    cases = benchmark.enumerate_cases(RunStage.FIXTURE)
    gateway = AlwaysTransientGateway()
    sleeps: list[float] = []
    ledger = SQLiteLedger(tmp_path / "run.sqlite3")
    spec = _spec("transient-backoff")
    try:
        spec = RunSpec(**{**spec.__dict__, "retry_backoff_seconds": (1.0, 2.0, 3.0)})
        summary = EvaluationRunner(gateway, ledger, sleeper=sleeps.append).execute(
            benchmark, spec, cases, manifest_for(spec, benchmark)
        )
    finally:
        ledger.close()

    assert summary.system_failed_cases == 2
    assert gateway.calls == 6
    assert sleeps == [1.0, 2.0, 3.0, 1.0, 2.0]


class FirstAuditFails:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, _request_id: str, execution_id: str) -> ExecutionAudit:
        self.calls += 1
        if self.calls == 1:
            raise AuditError("attempt audit is incomplete")
        return ExecutionAudit(
            execution_id,
            ({"model": "qwen-fixture", "status": "success"},),
            {"total_tokens": 4},
            0.001,
        )


def test_runner_records_audit_failure_and_continues_fixed_denominator(tmp_path: Path) -> None:
    benchmark = load_benchmark(ROOT, "gpqa")
    cases = benchmark.enumerate_cases(RunStage.FIXTURE)
    gateway = FixtureGatewayAdapter(
        {case.case_id: case.fixture_response for case in cases}  # type: ignore[dict-item]
    )
    ledger = SQLiteLedger(tmp_path / "run.sqlite3")
    spec = _spec("audit-failure-continues")
    audit = FirstAuditFails()
    try:
        summary = EvaluationRunner(gateway, ledger, audit=audit).execute(
            benchmark, spec, cases, manifest_for(spec, benchmark)
        )
        rows = ledger.case_rows(spec.run_id)
    finally:
        ledger.close()

    assert summary.system_failed_cases == 1
    assert summary.completed_cases == 1
    assert gateway.calls == 2
    assert rows[0]["status"] == "system_failed"
    assert rows[1]["status"] == "completed"


def test_official_chat_to_platform_audit_to_ledger_contract(tmp_path: Path) -> None:
    benchmark = load_benchmark(ROOT, "gpqa")
    cases = benchmark.enumerate_cases(RunStage.FIXTURE)[:1]

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "id": "exec-1",
                "model": "apigo/vohu",
                "choices": [{"message": {"role": "assistant", "content": "B"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
            headers={"x-request-id": "request-1", "x-vohu-execution-id": "exec-1"},
            request=request,
        )

    def audit_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "cost_usd_exact": "0.0042",
                    "vohu_execution": {
                        "execution_id": "exec-1",
                        "attempt_count": 1,
                        "usage_reconciled": True,
                        "settlement_status": "settled",
                        "derived_usage": {
                            "prompt_tokens": 3,
                            "completion_tokens": 1,
                            "total_tokens": 4,
                        },
                        "attempts": [
                            {
                                "attempt_id": "attempt-1",
                                "role": "synthesizer",
                                "model": "qwen-fixture",
                                "provider": "alibaba",
                                "status": "success",
                                "usage": {"input_tokens": 3, "output_tokens": 1},
                            }
                        ],
                    },
                },
            },
            request=request,
        )

    gateway = APIGOGatewayAdapter(
        "https://gateway.example",
        "gateway-key",
        transport=httpx.MockTransport(gateway_handler),
    )
    audit = PlatformLogsAuditAdapter(
        "https://platform.example",
        "platform-jwt",
        "ws_1",
        transport=httpx.MockTransport(audit_handler),
    )
    ledger = SQLiteLedger(tmp_path / "run.sqlite3")
    spec = RunSpec(
        **{
            **_spec("official-contract").__dict__,
            "expected_models": frozenset({"qwen-fixture"}),
        }
    )
    assert manifest_for(spec, benchmark)["gateway_protocol"] == "openai_chat_completions"
    try:
        summary = EvaluationRunner(gateway, ledger, audit=audit).execute(
            benchmark, spec, cases, manifest_for(spec, benchmark)
        )
        row = ledger.case_rows(spec.run_id)[0]
    finally:
        ledger.close()

    assert summary.correct_cases == 1
    assert summary.total_cost_usd == 0.0042
    assert '"provider": "alibaba"' in row["attempts_json"]
