from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from vohu_evals.dataset import DatasetCache, DatasetIntegrityError
from vohu_evals.evaluator import EvaluatorResourceCache, EvaluatorResourceError


@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    message: str


def benchmark_readiness(manifest: dict[str, Any], cache_root: Path) -> list[ReadinessIssue]:
    issues: list[ReadinessIssue] = []
    if manifest.get("implementation_status") != "verified":
        issues.append(ReadinessIssue("plugin_unverified", "benchmark plugin is not verified"))
    if manifest.get("publication_blocker"):
        issues.append(ReadinessIssue("publication_blocked", str(manifest["publication_blocker"])))
    if manifest.get("comparability") in {
        "not-comparable-to-official-hle",
        "protocol_experiment_only",
    }:
        issues.append(
            ReadinessIssue(
                "not_officially_comparable",
                f"benchmark comparability is {manifest['comparability']}",
            )
        )
    evaluator = manifest.get("official_evaluator", {})
    if evaluator.get("revision") in {None, "", "UNPINNED"}:
        issues.append(
            ReadinessIssue("evaluator_unpinned", "official evaluator revision is not frozen")
        )
    try:
        snapshot = DatasetCache(cache_root).plan(manifest)
        for artifact in snapshot.artifacts:
            if not artifact.path.exists():
                issues.append(
                    ReadinessIssue(
                        "dataset_missing", f"dataset artifact is missing: {artifact.path}"
                    )
                )
            elif artifact.size_bytes == 0:
                issues.append(
                    ReadinessIssue("dataset_empty", f"dataset artifact is empty: {artifact.path}")
                )
    except DatasetIntegrityError as exc:
        issues.append(ReadinessIssue("dataset_unfrozen", str(exc)))
    project_root = cache_root.parents[1]
    vendored_files = evaluator.get("vendored_files", {})
    for relative, expected in vendored_files.items():
        path = project_root / "src" / "instruction_following_eval" / relative
        if not path.is_file():
            issues.append(ReadinessIssue("evaluator_source_missing", f"missing {path}"))
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            issues.append(ReadinessIssue("evaluator_source_mismatch", f"hash mismatch for {path}"))
    if evaluator.get("resources"):
        try:
            resource_cache = EvaluatorResourceCache(project_root)
            resource_snapshot = resource_cache.plan(manifest)
            if any(item.size_bytes == 0 for item in resource_snapshot.artifacts):
                issues.append(
                    ReadinessIssue(
                        "evaluator_resources_missing",
                        "official evaluator resources are not materialized",
                    )
                )
            nltk_data = resource_cache.nltk_data_path(manifest)
            for relative in (
                "tokenizers/punkt/english.pickle",
                "tokenizers/punkt_tab/english",
            ):
                if not (nltk_data / relative).exists():
                    issues.append(
                        ReadinessIssue(
                            "evaluator_resources_unpacked",
                            f"official evaluator resource is not unpacked: {relative}",
                        )
                    )
        except (DatasetIntegrityError, EvaluatorResourceError) as exc:
            issues.append(ReadinessIssue("evaluator_resources_unfrozen", str(exc)))
    return issues


def validate_official_references(references_dir: Path, schema_path: Path) -> list[ReadinessIssue]:
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    issues: list[ReadinessIssue] = []
    for path in sorted(references_dir.glob("*.yaml")):
        instance = yaml.safe_load(path.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(instance=instance, schema=schema)
        except jsonschema.ValidationError as exc:
            issues.append(ReadinessIssue("reference_invalid", f"{path}: {exc.message}"))
    return issues
