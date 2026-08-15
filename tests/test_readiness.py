from __future__ import annotations

from pathlib import Path

from vohu_evals.readiness import benchmark_readiness, validate_official_references

ROOT = Path(__file__).resolve().parents[1]


def test_scaffold_benchmark_is_not_publication_ready(tmp_path: Path) -> None:
    manifest = {
        "name": "fixture",
        "implementation_status": "scaffold",
        "dataset": {"revision": "UNPINNED"},
        "official_evaluator": {"revision": "UNPINNED"},
    }
    codes = {issue.code for issue in benchmark_readiness(manifest, tmp_path)}
    assert codes == {"plugin_unverified", "evaluator_unpinned", "dataset_unfrozen"}


def test_reference_schema_rejects_missing_source(tmp_path: Path) -> None:
    references = tmp_path / "references"
    references.mkdir()
    (references / "bad.yaml").write_text(
        "schema_version: '1.0'\nmodel: qwen\nbenchmark: gpqa\nscore: 1\nmetric: accuracy\n",
        encoding="utf-8",
    )
    issues = validate_official_references(
        references, ROOT / "schemas" / "official-reference.schema.json"
    )
    assert len(issues) == 1
    assert issues[0].code == "reference_invalid"


def test_protocol_only_benchmark_is_not_publication_ready(tmp_path: Path) -> None:
    manifest = {
        "name": "draco",
        "implementation_status": "scaffold",
        "comparability": "protocol_experiment_only",
        "publication_blocker": "no official executable evaluator",
        "dataset": {"revision": "UNPINNED"},
        "official_evaluator": {"revision": "protocol-only-no-official-executable"},
    }

    codes = {issue.code for issue in benchmark_readiness(manifest, tmp_path)}

    assert "publication_blocked" in codes
    assert "not_officially_comparable" in codes
