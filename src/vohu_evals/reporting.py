from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from vohu_evals.benchmark import Benchmark
from vohu_evals.ledger import SQLiteLedger
from vohu_evals.models import CaseScore, CaseStatus

EVIDENCE_MARKER = re.compile(r"<!--\s*evidence:([a-zA-Z0-9_.-]+)\s*-->")
COMPARATIVE_WORDS = re.compile(
    r"\b(beats?|outperforms?|leading|higher than)\b|领先|超过|高于", re.I
)


def build_evidence(
    ledger: SQLiteLedger,
    run_id: str,
    destination: Path,
    benchmark: Benchmark | None = None,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    summary = ledger.summary(run_id)
    manifest = ledger.run_manifest(run_id)
    derivation = manifest.get("derivation")
    is_rescore = isinstance(derivation, dict) and derivation.get("type") == "rescore"
    publishable = (
        manifest.get("stage") == "publication"
        and summary.completed_cases == summary.total_cases
        and summary.system_failed_cases == 0
        and summary.invalid_output_cases == 0
    )
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "publishable": publishable,
        "publication_status": "publication_ready" if publishable else "internal_only",
        "metrics": {
            "score_percent": round(summary.score * 100, 4),
            "total_cases": summary.total_cases,
            "completed_cases": summary.completed_cases,
            "system_failed_cases": summary.system_failed_cases,
            "invalid_output_cases": summary.invalid_output_cases,
            "total_requests": summary.total_requests,
            "total_cost_usd": round(summary.total_cost_usd, 6),
            "target_requests": 0 if is_rescore else summary.total_requests,
        },
    }
    if is_rescore:
        payload["derivation"] = derivation
        payload["metrics"]["inherited_target_requests"] = summary.total_requests
        payload["metrics"]["inherited_target_cost_usd"] = round(summary.total_cost_usd, 6)
    if benchmark is not None:
        scores: list[CaseScore] = []
        for row in ledger.case_rows(run_id):
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
        payload["metrics"]["benchmark"] = benchmark_metrics
        if "score" in benchmark_metrics:
            payload["metrics"]["score_percent"] = round(float(benchmark_metrics["score"]) * 100, 4)
    path = destination / "summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_report_evidence(
    ledger: SQLiteLedger,
    run_id: str,
    evidence_path: Path,
    external_references_path: Path,
    destination: Path,
) -> Path:
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    manifest = ledger.run_manifest(run_id)
    total_cases = int(payload["metrics"]["total_cases"])
    failed_cases = int(payload["metrics"]["system_failed_cases"])
    payload["protocol"] = {
        "benchmark": manifest["benchmark"],
        "profile": manifest["profile"],
        "stage": manifest["stage"],
        "trial_id": manifest["trial_id"],
        "gateway_protocol": manifest["gateway_protocol"],
        "scorer_protocol_version": manifest["protocol_version"],
        "expected_models": manifest.get("expected_models", []),
        "comparability": manifest["benchmark_manifest"].get("comparability", "not_comparable"),
    }
    payload["reliability"] = {
        "system_failure_rate_percent": round(
            failed_cases / total_cases * 100 if total_cases else 0.0, 4
        )
    }
    payload["external_references"] = json.loads(
        external_references_path.read_text(encoding="utf-8")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination


def aggregate_evidence(
    evidence_paths: list[Path],
    destination: Path,
    *,
    external_references: Path | None = None,
    system_revision: str | None = None,
    run_id: str = "ifeval-vohu-quality-v1-smoke-trials-1-3",
) -> Path:
    """Builds a deterministic multi-trial summary from frozen run evidence."""
    if not evidence_paths:
        raise ValueError("at least one evidence file is required")
    trials = [json.loads(path.read_text(encoding="utf-8")) for path in evidence_paths]
    for trial in trials:
        benchmark = trial.get("metrics", {}).get("benchmark")
        if not isinstance(benchmark, dict):
            continue
        for field in (
            "prompt_level_strict",
            "instruction_level_strict",
            "prompt_level_loose",
            "instruction_level_loose",
        ):
            if field in benchmark:
                benchmark[f"{field}_percent"] = round(float(benchmark[field]) * 100, 4)
    metrics = [trial["metrics"] for trial in trials]
    total_cases = sum(int(item["total_cases"]) for item in metrics)
    completed_cases = sum(int(item["completed_cases"]) for item in metrics)
    system_failed_cases = sum(int(item["system_failed_cases"]) for item in metrics)
    invalid_output_cases = sum(int(item["invalid_output_cases"]) for item in metrics)
    total_requests = sum(int(item["total_requests"]) for item in metrics)
    total_cost_usd = sum(float(item["total_cost_usd"]) for item in metrics)
    correct_cases = sum(
        float(item["score_percent"]) * int(item["total_cases"]) / 100 for item in metrics
    )

    benchmark_metrics = [item.get("benchmark") for item in metrics]
    if not all(isinstance(item, dict) for item in benchmark_metrics):
        raise ValueError("every trial must contain benchmark metrics")
    prompts = sum(int(item["prompts"]) for item in benchmark_metrics)
    instructions = sum(int(item["instructions"]) for item in benchmark_metrics)

    def weighted(field: str, denominator: str) -> float:
        total = sum(float(item[field]) * int(item[denominator]) for item in benchmark_metrics)
        count = prompts if denominator == "prompts" else instructions
        return total / count if count else 0.0

    prompt_strict = weighted("prompt_level_strict", "prompts")
    instruction_strict = weighted("instruction_level_strict", "instructions")
    prompt_loose = weighted("prompt_level_loose", "prompts")
    instruction_loose = weighted("instruction_level_loose", "instructions")
    destination.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "publishable": (
            completed_cases == total_cases
            and system_failed_cases == 0
            and invalid_output_cases == 0
        ),
        "metrics": {
            "trials": len(trials),
            "score_percent": round(correct_cases / total_cases * 100, 4),
            "total_cases": total_cases,
            "completed_cases": completed_cases,
            "system_failed_cases": system_failed_cases,
            "system_failure_rate_percent": round(system_failed_cases / total_cases * 100, 4),
            "invalid_output_cases": invalid_output_cases,
            "total_requests": total_requests,
            "total_cost_usd": round(total_cost_usd, 6),
            "benchmark": {
                "prompt_level_strict": prompt_strict,
                "instruction_level_strict": instruction_strict,
                "prompt_level_loose": prompt_loose,
                "instruction_level_loose": instruction_loose,
                "prompt_level_strict_percent": round(prompt_strict * 100, 4),
                "instruction_level_strict_percent": round(instruction_strict * 100, 4),
                "prompt_level_loose_percent": round(prompt_loose * 100, 4),
                "instruction_level_loose_percent": round(instruction_loose * 100, 4),
                "prompts": prompts,
                "instructions": instructions,
            },
        },
        "trials": trials,
    }
    if system_revision:
        payload["system_revision"] = system_revision
    if external_references is not None:
        payload["external_references"] = json.loads(external_references.read_text(encoding="utf-8"))
    path = destination / "summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else key
            result.update(_flatten(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{prefix}.{index}" if prefix else str(index)
            result.update(_flatten(item, child))
    else:
        result[prefix] = value
    return result


def verify_report(report_path: Path, evidence_path: Path) -> list[str]:
    report = report_path.read_text(encoding="utf-8")
    evidence = _flatten(json.loads(evidence_path.read_text(encoding="utf-8")))
    errors: list[str] = []
    for line_number, line in enumerate(report.splitlines(), start=1):
        markers = EVIDENCE_MARKER.findall(line)
        has_claim = bool(re.search(r"\d", line) or COMPARATIVE_WORDS.search(line))
        if has_claim and not markers:
            errors.append(f"line {line_number}: numeric/comparative claim lacks evidence marker")
        for key in markers:
            if key not in evidence:
                errors.append(f"line {line_number}: unknown evidence key {key}")
                continue
            expected = evidence[key]
            missing_boolean = (
                isinstance(expected, bool) and str(expected).lower() not in line.lower()
            )
            missing_number = (
                isinstance(expected, (int, float))
                and not isinstance(expected, bool)
                and str(expected) not in line
            )
            if missing_boolean or missing_number:
                errors.append(f"line {line_number}: evidence value {expected} is not present")
    return errors


def generate_report_with_codex(
    project_root: Path, evidence_dir: Path, prompt_path: Path, output_path: Path
) -> None:
    prompt = prompt_path.read_text(encoding="utf-8").format(evidence_dir=evidence_dir)
    command = [
        "codex",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--model",
        "gpt-5.6-sol",
        "--sandbox",
        "read-only",
        "--cd",
        str(project_root),
        "--json",
        "-",
    ]
    completed = subprocess.run(command, input=prompt, text=True, check=True, capture_output=True)
    final_message = ""
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            final_message = str(item.get("text", "")).strip()
    if not final_message:
        raise RuntimeError("Codex returned no final agent message")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_message + "\n", encoding="utf-8")
