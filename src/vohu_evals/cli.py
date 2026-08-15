from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

from vohu_evals.audit import PlatformLogsAuditAdapter
from vohu_evals.benchmark import DatasetNotReadyError, load_benchmark
from vohu_evals.config import canonical_hash, load_yaml, validate_campaign
from vohu_evals.dataset import DatasetCache
from vohu_evals.evaluator import EvaluatorResourceCache
from vohu_evals.gateway import APIGOGatewayAdapter, FixtureGatewayAdapter
from vohu_evals.judge import APIGOJudgeAdapter
from vohu_evals.ledger import SQLiteLedger
from vohu_evals.models import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    BenchmarkRequest,
    Budget,
    RunSpec,
    RunStage,
)
from vohu_evals.platform_auth import PlatformCredentialManager, jwt_is_fresh, load_local_env
from vohu_evals.policy import (
    CompositionPolicyError,
    validate_china_model_composition,
    validate_expected_composition,
    validate_official_composition_snapshot,
)
from vohu_evals.readiness import benchmark_readiness, validate_official_references
from vohu_evals.reporting import build_evidence, build_report_evidence, verify_report
from vohu_evals.rescoring import plan_rescore, rescore_run
from vohu_evals.runner import EvaluationRunner, manifest_for

REQUIRED_EXECUTION_ENV = (
    "VOHU_EVALS_GATEWAY_BASE_URL",
    "VOHU_EVALS_API_KEY",
    "VOHU_EVALS_PLATFORM_BASE_URL",
    "VOHU_EVALS_PLATFORM_TOKEN",
    "VOHU_EVALS_WORKSPACE_ID",
)

PLATFORM_LOGIN_ENV = ("VOHU_USER_EMAIL", "VOHU_USER_PASSWORD")
TARGETS_DIR_ENV = "VOHU_EVALS_TARGETS_DIR"
BENCHMARKS = ("gpqa", "hle", "ifeval", "browsecomp", "draco", "frames")
PROFILES = (
    "vohu-quality-v1",
    "vohu-budget-v1",
    "vohu-fast-v1",
    "vohu-research-v1",
    "vohu-research-v2",
    "vohu-research-v3",
    "vohu-research-v4",
    "vohu-research-v5",
    "vohu-research-v6",
    "vohu-research-v7",
    "vohu-research-v8",
    "vohu-research-v9",
    "vohu-research-v10",
    "vohu-research-v11",
    "vohu-research-v12",
    "vohu-research-v14",
    "vohu-research-v15",
    "vohu-research-v16",
    "vohu-research-v17",
    "vohu-research-v18",
    "vohu-research-v19",
    "vohu-research-v20",
    "vohu-research-v21",
)


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _targets_dir(root: Path) -> Path:
    configured = os.environ.get(TARGETS_DIR_ENV)
    return Path(configured).expanduser() if configured else root / "targets"


def _fixture_run(name: str, output: Path | None) -> int:
    root = _root()
    benchmark = load_benchmark(root, name)
    cases = benchmark.enumerate_cases(RunStage.FIXTURE)
    responses = {
        case.case_id: case.fixture_response for case in cases if case.fixture_response is not None
    }
    allowlist = load_yaml(_targets_dir(root) / "china-models-v1.yaml")
    allowed = frozenset(item["model_id"] for item in allowlist["models"])
    manifest_seed = {"benchmark": name, "profile": "vohu-quality-v1", "stage": "fixture"}
    run_id = f"{name}-vohu-quality-fixture-{canonical_hash(manifest_seed)[:8]}"
    spec = RunSpec(
        run_id=run_id,
        benchmark=name,
        profile="vohu-quality-v1",
        stage=RunStage.FIXTURE,
        trial_id=1,
        protocol_version=str(benchmark.manifest["protocol_version"]),
        budget=Budget(max_usd=1, max_requests=100, max_wall_time_seconds=60),
        allowed_models=allowed,
    )
    if output is None:
        output = Path(tempfile.mkdtemp(prefix="vohu-evals-fixture-"))
    ledger = SQLiteLedger(output / "run.sqlite3")
    try:
        summary = EvaluationRunner(FixtureGatewayAdapter(responses), ledger).execute(
            benchmark, spec, cases, manifest_for(spec, benchmark)
        )
        evidence = build_evidence(ledger, run_id, output / "evidence", benchmark)
        print(json.dumps({**summary.__dict__, "score": summary.score, "evidence": str(evidence)}))
    finally:
        ledger.close()
    return 0


def _validate() -> int:
    root = _root()
    failures: list[str] = []
    for name in BENCHMARKS:
        try:
            benchmark = load_benchmark(root, name)
            benchmark.validate(RunStage.FIXTURE)
            benchmark.enumerate_cases(RunStage.FIXTURE)
        except (ValueError, DatasetNotReadyError) as exc:
            failures.append(f"{name}: {exc}")
    for path in sorted((root / "configs" / "campaigns").glob("*.yaml")):
        try:
            validate_campaign(load_yaml(path))
        except ValueError as exc:
            failures.append(f"{path}: {exc}")
    failures.extend(_target_validation_failures(_targets_dir(root)))
    if failures:
        print("\n".join(failures))
        return 1
    print("configuration and fixture validation passed")
    return 0


def _target_validation_failures(targets_dir: Path) -> list[str]:
    if not targets_dir.is_dir():
        return []
    try:
        allowlist = load_yaml(targets_dir / "china-models-v1.yaml")
        allowed = frozenset(item["model_id"] for item in allowlist["models"])
        for path in sorted(targets_dir.glob("official-compositions-*.yaml")):
            snapshot = load_yaml(path)
            validate_official_composition_snapshot(allowed, snapshot)
    except (CompositionPolicyError, ValueError, OSError) as exc:
        return [f"targets: {exc}"]
    return []


def _profile_policy(
    root: Path, profile_name: str
) -> tuple[str, str, str, frozenset[str], frozenset[str]]:
    targets_dir = _targets_dir(root)
    profile = load_yaml(root / "configs" / "profiles" / f"{profile_name}.yaml")
    gateway_model = str(profile.get("gateway_model", "")).strip()
    if not gateway_model:
        raise ValueError(f"profile gateway model is missing: {profile_name}")
    gateway_protocol = str(profile.get("gateway_protocol", "")).strip()
    if gateway_protocol != "openai_chat_completions":
        raise ValueError(f"profile must freeze Chat Completions: {profile_name}")
    source = str(profile.get("composition_source", ""))
    if source == "workspace_custom":
        identity = str(profile.get("custom_model", "")).strip()
        expected_version = int(profile["expected_version"])
        if gateway_model != f"{identity}@v{expected_version}":
            raise ValueError(f"custom profile gateway model/version mismatch: {profile_name}")
        candidates = profile.get("candidate_models", [])
        synthesizer = str(profile.get("synthesizer_model", "")).strip()
        if not isinstance(candidates, list) or not synthesizer:
            raise ValueError(f"custom profile composition is invalid: {profile_name}")
        expected_models = frozenset([*candidates, synthesizer])
    else:
        snapshot_name = str(profile["composition_snapshot"])
        if Path(snapshot_name).name != snapshot_name:
            raise ValueError(f"profile composition snapshot is invalid: {profile_name}")
        snapshot = load_yaml(targets_dir / f"{snapshot_name}.yaml")
        if snapshot.get("snapshot_id") != snapshot_name:
            raise ValueError(f"profile composition snapshot identity mismatch: {profile_name}")
        identity = str(profile["official_mode"])
        mode_snapshot = snapshot["modes"].get(identity)
        if not isinstance(mode_snapshot, dict):
            raise ValueError(
                f"profile official mode is absent from composition snapshot: {identity}"
            )
        if int(mode_snapshot["version"]) != int(profile["expected_version"]):
            raise ValueError(f"profile version does not match composition snapshot: {profile_name}")
        if "researcher_models" in mode_snapshot:
            if gateway_model != identity:
                raise ValueError(f"accuracy profile gateway model mismatch: {profile_name}")
            for profile_key, snapshot_key in (
                ("expected_snapshot_hash", "snapshot_hash"),
                ("expected_schema_version", "schema_version"),
                ("expected_evidence_mode", "evidence_mode"),
            ):
                if profile.get(profile_key) != mode_snapshot.get(snapshot_key):
                    raise ValueError(
                        f"profile {profile_key} does not match composition snapshot: {profile_name}"
                    )
            expected_models = frozenset(
                [
                    *mode_snapshot.get("researcher_models", []),
                    mode_snapshot["verifier_model"],
                    mode_snapshot["synthesizer_model"],
                ]
            )
        else:
            expected_models = frozenset(
                [*mode_snapshot.get("candidate_models", []), mode_snapshot["synthesizer_model"]]
            )
    allowlist = load_yaml(targets_dir / "china-models-v1.yaml")
    allowed_models = frozenset(
        item["model_id"] for item in allowlist["models"] if not item.get("test_only", False)
    )
    if not expected_models or not expected_models.issubset(allowed_models):
        raise ValueError(
            f"profile composition is outside the China-model allowlist: {profile_name}"
        )
    return identity, gateway_model, gateway_protocol, expected_models, allowed_models


def _preflight(
    config_path: Path,
    profile_name: str,
    *,
    execute: bool,
    confirmed_budget: float | None,
) -> int:
    root = _root()
    config = load_yaml(config_path)
    validate_campaign(config)
    mode, gateway_model, gateway_protocol, expected_models, allowed = _profile_policy(
        root, profile_name
    )
    campaign_protocol = str(config.get("protocol", "")).strip()
    if campaign_protocol != gateway_protocol:
        raise ValueError("campaign protocol does not match the frozen profile protocol")
    campaign_gateway_model = str(config.get("gateway_model", "")).strip()
    if campaign_gateway_model != gateway_model:
        raise ValueError("campaign gateway model does not match the frozen profile model")
    plan = {
        "campaign": config["campaign"],
        "profile": profile_name,
        "official_mode": mode,
        "expected_models": sorted(expected_models),
        "protocol": gateway_protocol,
        "gateway_model": gateway_model,
        "requires_web_search": bool(config.get("requires_web_search_preflight")),
        "requires_citations": bool(config.get("requires_citations_preflight")),
        "preflight_max_usd": float(config.get("preflight_max_usd", 1.0)),
        "network_call": execute,
    }
    if not execute:
        print(json.dumps(plan, indent=2))
        return 0
    if confirmed_budget != plan["preflight_max_usd"]:
        raise ValueError("--confirm-budget-usd must exactly match campaign preflight_max_usd")
    base_url = os.environ.get("VOHU_EVALS_GATEWAY_BASE_URL")
    api_key = os.environ.get("VOHU_EVALS_API_KEY")
    audit = _platform_audit_from_env()
    if not base_url or not api_key:
        raise ValueError("VOHU_EVALS_GATEWAY_BASE_URL and VOHU_EVALS_API_KEY are required")
    result = APIGOGatewayAdapter(
        base_url, api_key, model=gateway_model, protocol=gateway_protocol
    ).invoke(
        BenchmarkRequest(
            case_id="preflight",
            prompt=(
                "Find the official APIGO website, state its title, and cite the source."
                if plan["requires_web_search"]
                else "Return exactly VOHU_PREFLIGHT_OK."
            ),
            web_search=plan["requires_web_search"],
        )
    )
    execution_audit = audit.resolve(result.request_id, result.execution_id or "")
    result = replace(
        result,
        attempt_models=execution_audit.attempt_models,
        attempts=execution_audit.attempts,
        usage=execution_audit.usage,
        cost_usd=execution_audit.cost_usd,
    )
    validate_china_model_composition(allowed, result.attempt_models, require_audit=True)
    validate_expected_composition(expected_models, result.attempt_models)
    if not result.usage:
        raise ValueError("preflight response has no observable usage")
    if plan["requires_citations"] and not result.citations:
        raise ValueError("Web Search preflight returned no citations")
    print(
        json.dumps(
            {
                **plan,
                "request_id": result.request_id,
                "execution_id": result.execution_id,
                "attempt_models": result.attempt_models,
                "citation_count": len(result.citations),
                "usage_observed": True,
            },
            indent=2,
        )
    )
    return 0


def _platform_audit_from_env() -> PlatformLogsAuditAdapter:
    base_url = os.environ.get("VOHU_EVALS_PLATFORM_BASE_URL")
    token = os.environ.get("VOHU_EVALS_PLATFORM_TOKEN")
    workspace_id = os.environ.get("VOHU_EVALS_WORKSPACE_ID")
    if not base_url or not workspace_id:
        raise ValueError(
            "VOHU_EVALS_PLATFORM_BASE_URL and VOHU_EVALS_WORKSPACE_ID are required "
            "for out-of-band attempt audit"
        )
    credentials = PlatformCredentialManager(
        base_url,
        token,
        os.environ.get("VOHU_USER_EMAIL"),
        os.environ.get("VOHU_USER_PASSWORD"),
        _root() / ".env",
    )
    fresh_token = credentials.ensure_fresh()
    return PlatformLogsAuditAdapter(
        base_url,
        fresh_token,
        workspace_id,
        refresh_token=lambda: credentials.ensure_fresh(force=True),
    )


def _doctor(benchmark_name: str, profile_name: str = "vohu-quality-v1") -> int:
    root = _root()
    benchmark = load_benchmark(root, benchmark_name)
    environment = {
        name: bool(os.environ.get(name)) for name in (*REQUIRED_EXECUTION_ENV, *PLATFORM_LOGIN_ENV)
    }
    token_fresh = jwt_is_fresh(os.environ.get("VOHU_EVALS_PLATFORM_TOKEN"))
    platform_auth_ready = token_fresh or all(environment[name] for name in PLATFORM_LOGIN_ENV)
    readiness = benchmark_readiness(benchmark.manifest, root / ".cache" / "datasets")
    configuration_issues: list[str] = []
    try:
        mode, gateway_model, gateway_protocol, expected_models, _ = _profile_policy(
            root, profile_name
        )
    except (CompositionPolicyError, KeyError, OSError, TypeError, ValueError) as exc:
        mode, gateway_model, gateway_protocol, expected_models = "", "", "", frozenset()
        configuration_issues.append(str(exc))
    payload = {
        "benchmark": benchmark_name,
        "profile": profile_name,
        "official_mode": mode,
        "gateway_model": gateway_model,
        "gateway_protocol": gateway_protocol,
        "expected_models": sorted(expected_models),
        "environment": environment,
        "platform_token_fresh": token_fresh,
        "readiness_issues": [issue.__dict__ for issue in readiness],
        "configuration_issues": configuration_issues,
        "can_execute": (
            all(
                environment[name]
                for name in REQUIRED_EXECUTION_ENV
                if name != "VOHU_EVALS_PLATFORM_TOKEN"
            )
            and platform_auth_ready
            and not readiness
            and not configuration_issues
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["can_execute"] else 1


def _run_plan(
    benchmark_name: str,
    profile_name: str,
    stage: RunStage,
    trial_id: int,
    budget: Budget,
    *,
    execute: bool,
    confirmed_budget: float | None,
) -> int:
    root = _root()
    benchmark = load_benchmark(root, benchmark_name)
    if stage is RunStage.PUBLICATION:
        issues = benchmark_readiness(benchmark.manifest, root / ".cache" / "datasets")
        if issues:
            raise ValueError(
                "publication readiness failed: "
                + "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
            )
    cases = benchmark.enumerate_cases(stage)
    evaluator_requests = sum(benchmark.evaluator_requests_per_case(case) for case in cases)
    planned_gateway_requests = len(cases) + evaluator_requests
    mode, gateway_model, gateway_protocol, expected_models, allowed_models = _profile_policy(
        root, profile_name
    )
    seed = {
        "benchmark": benchmark_name,
        "profile": profile_name,
        "stage": stage.value,
        "trial_id": trial_id,
        "protocol_version": benchmark.manifest["protocol_version"],
        "dataset_revision": benchmark.manifest["dataset"]["revision"],
        "gateway_protocol": gateway_protocol,
        "target_retry_policy": {
            "max_retries": DEFAULT_MAX_RETRIES,
            "backoff_seconds": DEFAULT_RETRY_BACKOFF_SECONDS,
        },
        "budget": budget.__dict__,
    }
    run_id = f"{benchmark_name}-{profile_name}-{stage.value}-{canonical_hash(seed)[:12]}"
    plan = {
        "run_id": run_id,
        "benchmark": benchmark_name,
        "profile": profile_name,
        "official_mode": mode,
        "expected_models": sorted(expected_models),
        "gateway_model": gateway_model,
        "gateway_protocol": gateway_protocol,
        "stage": stage.value,
        "trial_id": trial_id,
        "cases": len(cases),
        "target_requests": len(cases),
        "evaluator_requests": evaluator_requests,
        "planned_gateway_requests": planned_gateway_requests,
        "target_retry_policy": {
            "max_retries": DEFAULT_MAX_RETRIES,
            "backoff_seconds": DEFAULT_RETRY_BACKOFF_SECONDS,
        },
        "budget": budget.__dict__,
        "network_call": execute,
    }
    if not execute:
        print(json.dumps(plan, indent=2))
        return 0
    if confirmed_budget != budget.max_usd:
        raise ValueError("--confirm-budget-usd must exactly match --max-usd")
    if budget.max_requests < planned_gateway_requests:
        raise ValueError(
            "--max-requests must cover target plus frozen evaluator requests: "
            f"{planned_gateway_requests}"
        )
    base_url = os.environ.get("VOHU_EVALS_GATEWAY_BASE_URL")
    api_key = os.environ.get("VOHU_EVALS_API_KEY")
    if not base_url or not api_key:
        raise ValueError("VOHU_EVALS_GATEWAY_BASE_URL and VOHU_EVALS_API_KEY are required")
    spec = RunSpec(
        run_id=run_id,
        benchmark=benchmark_name,
        profile=profile_name,
        stage=stage,
        trial_id=trial_id,
        protocol_version=str(benchmark.manifest["protocol_version"]),
        budget=budget,
        allowed_models=allowed_models,
        gateway_protocol=gateway_protocol,
        expected_models=expected_models,
    )
    output = root / "runs" / run_id
    ledger = SQLiteLedger(output / "run.sqlite3")
    try:
        summary = EvaluationRunner(
            APIGOGatewayAdapter(base_url, api_key, model=gateway_model, protocol=gateway_protocol),
            ledger,
            audit=_platform_audit_from_env(),
            judge=(
                APIGOJudgeAdapter(
                    base_url,
                    api_key,
                    str(benchmark.manifest["official_evaluator"]["judge_model"]),
                )
                if benchmark.manifest.get("official_evaluator", {}).get("judge_model")
                else None
            ),
        ).execute(benchmark, spec, cases, manifest_for(spec, benchmark))
        evidence = build_evidence(ledger, run_id, output / "evidence", benchmark)
    finally:
        ledger.close()
    print(json.dumps({**plan, **summary.__dict__, "evidence": str(evidence)}, indent=2))
    return 0


def _rescore_browsecomp(
    source_run_id: str,
    *,
    max_judge_requests: int,
    execute: bool,
    confirmed_judge_requests: int | None,
) -> int:
    root = _root()
    benchmark = load_benchmark(root, "browsecomp")
    source_path = root / "runs" / source_run_id / "run.sqlite3"
    if not source_path.is_file():
        raise ValueError(f"source run ledger does not exist: {source_run_id}")
    source = SQLiteLedger(source_path)
    try:
        rescore_plan = plan_rescore(source, benchmark, source_run_id)
        seed = {
            "source_run_id": source_run_id,
            "protocol_version": benchmark.manifest["protocol_version"],
        }
        derived_run_id = f"{source_run_id}-rescore-{canonical_hash(seed)[:12]}"
        plan = {
            "source_run_id": source_run_id,
            "derived_run_id": derived_run_id,
            "protocol_version": benchmark.manifest["protocol_version"],
            "completed_cases": rescore_plan.completed_cases,
            "preserved_failed_cases": rescore_plan.preserved_failed_cases,
            "target_requests": 0,
            "evaluator_requests": rescore_plan.evaluator_requests,
            "max_judge_requests": max_judge_requests,
            "network_call": execute,
        }
        if max_judge_requests < rescore_plan.evaluator_requests:
            raise ValueError("--max-judge-requests is below the planned evaluator requests")
        if not execute:
            print(json.dumps(plan, indent=2))
            return 0
        if confirmed_judge_requests != rescore_plan.evaluator_requests:
            raise ValueError(
                "--confirm-judge-requests must exactly match planned evaluator requests"
            )
        base_url = os.environ.get("VOHU_EVALS_GATEWAY_BASE_URL")
        api_key = os.environ.get("VOHU_EVALS_API_KEY")
        if not base_url or not api_key:
            raise ValueError("VOHU_EVALS_GATEWAY_BASE_URL and VOHU_EVALS_API_KEY are required")
        output = root / "runs" / derived_run_id
        destination = SQLiteLedger(output / "run.sqlite3")
        try:
            result = rescore_run(
                source,
                destination,
                benchmark,
                source_run_id=source_run_id,
                derived_run_id=derived_run_id,
                judge=APIGOJudgeAdapter(
                    base_url,
                    api_key,
                    str(benchmark.manifest["official_evaluator"]["judge_model"]),
                ),
                max_judge_requests=max_judge_requests,
            )
            evidence = build_evidence(destination, derived_run_id, output / "evidence", benchmark)
            report_evidence = build_report_evidence(
                destination,
                derived_run_id,
                evidence,
                root / "benchmarks" / "ifeval" / "references" / "external-official.json",
                output / "evidence" / "report-summary.json",
            )
        finally:
            destination.close()
        print(
            json.dumps(
                {
                    **plan,
                    **result.summary.__dict__,
                    "actual_evaluator_requests": result.evaluator_requests,
                    "evidence": str(evidence),
                    "report_evidence": str(report_evidence),
                },
                indent=2,
            )
        )
        return 0
    finally:
        source.close()


def main() -> None:
    load_local_env(_root() / ".env")
    parser = argparse.ArgumentParser(prog="vohu-eval")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    fixture = subparsers.add_parser("fixture-run")
    fixture.add_argument("benchmark", choices=BENCHMARKS)
    fixture.add_argument("--output", type=Path)
    campaign = subparsers.add_parser("campaign-plan")
    campaign.add_argument("config", type=Path)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("config", type=Path)
    preflight.add_argument(
        "--profile",
        default="vohu-quality-v1",
        choices=PROFILES,
    )
    preflight.add_argument("--execute", action="store_true")
    preflight.add_argument("--confirm-budget-usd", type=float)
    report = subparsers.add_parser("verify-report")
    report.add_argument("report", type=Path)
    report.add_argument("evidence", type=Path)
    dataset = subparsers.add_parser("dataset")
    dataset.add_argument("benchmark", choices=BENCHMARKS)
    dataset.add_argument("--execute", action="store_true")
    readiness = subparsers.add_parser("readiness")
    readiness.add_argument("benchmark", choices=BENCHMARKS)
    references = subparsers.add_parser("references-validate")
    references.add_argument("benchmark", choices=BENCHMARKS)
    evaluator = subparsers.add_parser("evaluator")
    evaluator.add_argument("benchmark", choices=("ifeval",))
    evaluator.add_argument("--execute", action="store_true")
    run = subparsers.add_parser("run")
    run.add_argument("benchmark", choices=BENCHMARKS)
    run.add_argument(
        "--profile",
        required=True,
        choices=PROFILES,
    )
    run.add_argument("--stage", required=True, choices=("smoke", "calibration", "publication"))
    run.add_argument("--trial-id", type=int, default=1)
    run.add_argument("--max-usd", type=float, required=True)
    run.add_argument("--max-requests", type=int, required=True)
    run.add_argument("--max-wall-time-seconds", type=int, required=True)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--confirm-budget-usd", type=float)
    rescore = subparsers.add_parser("rescore-browsecomp")
    rescore.add_argument("--source-run", required=True)
    rescore.add_argument("--max-judge-requests", type=int, required=True)
    rescore.add_argument("--execute", action="store_true")
    rescore.add_argument("--confirm-judge-requests", type=int)
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("benchmark", nargs="?", default="ifeval", choices=BENCHMARKS)
    doctor.add_argument(
        "--profile",
        default="vohu-quality-v1",
        choices=PROFILES,
    )
    args = parser.parse_args()

    if args.command == "validate":
        raise SystemExit(_validate())
    if args.command == "fixture-run":
        raise SystemExit(_fixture_run(args.benchmark, args.output))
    if args.command == "campaign-plan":
        config = load_yaml(args.config)
        validate_campaign(config)
        print(
            json.dumps({"campaign": config["campaign"], "hash": canonical_hash(config)}, indent=2)
        )
        return
    if args.command == "preflight":
        raise SystemExit(
            _preflight(
                args.config,
                args.profile,
                execute=args.execute,
                confirmed_budget=args.confirm_budget_usd,
            )
        )
    if args.command == "verify-report":
        errors = verify_report(args.report, args.evidence)
        if errors:
            print("\n".join(errors))
            raise SystemExit(1)
        print("report verification passed")
        return
    if args.command == "dataset":
        benchmark = load_benchmark(_root(), args.benchmark)
        cache = DatasetCache(_root() / ".cache" / "datasets")
        snapshot = (
            cache.materialize(benchmark.manifest)
            if args.execute
            else cache.plan(benchmark.manifest)
        )
        print(
            json.dumps(
                {
                    "name": snapshot.name,
                    "revision": snapshot.revision,
                    "execute": args.execute,
                    "snapshot_sha256": snapshot.snapshot_sha256,
                    "record_count": snapshot.record_count,
                    "ids_sha256": snapshot.ids_sha256,
                    "artifacts": [
                        {
                            "path": str(item.path),
                            "sha256": item.sha256,
                            "size_bytes": item.size_bytes,
                        }
                        for item in snapshot.artifacts
                    ],
                },
                indent=2,
            )
        )
        return
    if args.command == "readiness":
        benchmark = load_benchmark(_root(), args.benchmark)
        issues = benchmark_readiness(benchmark.manifest, _root() / ".cache" / "datasets")
        print(json.dumps([issue.__dict__ for issue in issues], indent=2))
        raise SystemExit(1 if issues else 0)
    if args.command == "references-validate":
        issues = validate_official_references(
            _root() / "benchmarks" / args.benchmark / "references",
            _root() / "schemas" / "official-reference.schema.json",
        )
        print(json.dumps([issue.__dict__ for issue in issues], indent=2))
        raise SystemExit(1 if issues else 0)
    if args.command == "evaluator":
        benchmark = load_benchmark(_root(), args.benchmark)
        cache = EvaluatorResourceCache(_root())
        snapshot = (
            cache.materialize(benchmark.manifest)
            if args.execute
            else cache.plan(benchmark.manifest)
        )
        print(
            json.dumps(
                {
                    "benchmark": args.benchmark,
                    "execute": args.execute,
                    "revision": snapshot.revision,
                    "snapshot_sha256": snapshot.snapshot_sha256,
                    "nltk_data": str(cache.nltk_data_path(benchmark.manifest)),
                },
                indent=2,
            )
        )
        return
    if args.command == "run":
        raise SystemExit(
            _run_plan(
                args.benchmark,
                args.profile,
                RunStage(args.stage),
                args.trial_id,
                Budget(
                    max_usd=args.max_usd,
                    max_requests=args.max_requests,
                    max_wall_time_seconds=args.max_wall_time_seconds,
                ),
                execute=args.execute,
                confirmed_budget=args.confirm_budget_usd,
            )
        )
    if args.command == "doctor":
        raise SystemExit(_doctor(args.benchmark, args.profile))
    if args.command == "rescore-browsecomp":
        raise SystemExit(
            _rescore_browsecomp(
                args.source_run,
                max_judge_requests=args.max_judge_requests,
                execute=args.execute,
                confirmed_judge_requests=args.confirm_judge_requests,
            )
        )


if __name__ == "__main__":
    main()
