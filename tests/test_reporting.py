from __future__ import annotations

import json
from pathlib import Path

from vohu_evals.reporting import aggregate_evidence, generate_report_with_codex, verify_report


def test_report_verifier_accepts_sourced_number(tmp_path: Path) -> None:
    evidence = tmp_path / "summary.json"
    evidence.write_text(json.dumps({"metrics": {"score_percent": 87.4}}), encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text(
        "VOHU scored 87.4 percent. <!-- evidence:metrics.score_percent -->\n",
        encoding="utf-8",
    )
    assert verify_report(report, evidence) == []


def test_report_verifier_rejects_unsourced_claim(tmp_path: Path) -> None:
    evidence = tmp_path / "summary.json"
    evidence.write_text(json.dumps({"metrics": {"score_percent": 87.4}}), encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text("VOHU beats the reference at 90 percent.\n", encoding="utf-8")
    errors = verify_report(report, evidence)
    assert errors == ["line 1: numeric/comparative claim lacks evidence marker"]


def test_report_verifier_rejects_wrong_value(tmp_path: Path) -> None:
    evidence = tmp_path / "summary.json"
    evidence.write_text(json.dumps({"metrics": {"score_percent": 87.4}}), encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text(
        "VOHU scored 90 percent. <!-- evidence:metrics.score_percent -->\n", encoding="utf-8"
    )
    assert "evidence value 87.4 is not present" in verify_report(report, evidence)[0]


def test_report_verifier_supports_list_paths_and_lowercase_boolean(tmp_path: Path) -> None:
    evidence = tmp_path / "summary.json"
    evidence.write_text(
        json.dumps({"publishable": False, "trials": [{"metrics": {"score": 80}}]}),
        encoding="utf-8",
    )
    report = tmp_path / "report.md"
    report.write_text(
        "publishable=false。 <!-- evidence:publishable -->\n"
        "trial score 80。 <!-- evidence:trials.0.metrics.score -->\n",
        encoding="utf-8",
    )
    assert verify_report(report, evidence) == []


def test_aggregate_evidence_weights_trials_and_fails_publication_closed(
    tmp_path: Path,
) -> None:
    paths: list[Path] = []
    for index, (score, completed, failed, requests, strict) in enumerate(
        ((80, 19, 1, 20, 0.8), (90, 20, 0, 22, 0.9)), start=1
    ):
        path = tmp_path / f"trial-{index}.json"
        path.write_text(
            json.dumps(
                {
                    "run_id": f"trial-{index}",
                    "metrics": {
                        "score_percent": score,
                        "total_cases": 20,
                        "completed_cases": completed,
                        "system_failed_cases": failed,
                        "invalid_output_cases": 0,
                        "total_requests": requests,
                        "total_cost_usd": 0.1,
                        "benchmark": {
                            "prompt_level_strict": strict,
                            "instruction_level_strict": strict,
                            "prompt_level_loose": 1.0,
                            "instruction_level_loose": 1.0,
                            "prompts": 20,
                            "instructions": 30,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        paths.append(path)

    output = aggregate_evidence(paths, tmp_path / "aggregate")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["publishable"] is False
    assert payload["metrics"]["score_percent"] == 85
    assert payload["metrics"]["system_failure_rate_percent"] == 2.5
    assert payload["metrics"]["total_requests"] == 42
    assert payload["metrics"]["benchmark"]["prompt_level_strict"] == 0.85
    assert payload["metrics"]["benchmark"]["prompt_level_strict_percent"] == 85.0
    assert payload["trials"][0]["metrics"]["benchmark"]["prompt_level_strict_percent"] == 80.0


def test_aggregate_evidence_attaches_frozen_external_references_and_revision(
    tmp_path: Path,
) -> None:
    trial = tmp_path / "trial.json"
    trial.write_text(
        json.dumps(
            {
                "run_id": "trial-4",
                "metrics": {
                    "score_percent": 100,
                    "total_cases": 1,
                    "completed_cases": 1,
                    "system_failed_cases": 0,
                    "invalid_output_cases": 0,
                    "total_requests": 1,
                    "total_cost_usd": 0.1,
                    "benchmark": {
                        "prompt_level_strict": 1.0,
                        "instruction_level_strict": 1.0,
                        "prompt_level_loose": 1.0,
                        "instruction_level_loose": 1.0,
                        "prompts": 1,
                        "instructions": 1,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    references = tmp_path / "references.json"
    references.write_text(
        json.dumps({"comparability": "reference_only", "score": 69.0}),
        encoding="utf-8",
    )

    output = aggregate_evidence(
        [trial],
        tmp_path / "aggregate",
        external_references=references,
        system_revision="engine@abc+gateway@def",
        run_id="trials-4-6",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["run_id"] == "trials-4-6"
    assert payload["system_revision"] == "engine@abc+gateway@def"
    assert payload["external_references"]["score"] == 69.0


def test_codex_report_generator_writes_final_jsonl_agent_message(
    tmp_path: Path, monkeypatch
) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Read {evidence_dir}", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    output = tmp_path / "report.md"

    def fake_run(command, **kwargs):
        assert "--json" in command
        assert "--output-last-message" not in command
        assert str(evidence) in kwargs["input"]
        from subprocess import CompletedProcess

        return CompletedProcess(
            command,
            0,
            stdout='{"type":"item.completed","item":{"type":"agent_message","text":"# Report"}}\n',
            stderr="",
        )

    monkeypatch.setattr("vohu_evals.reporting.subprocess.run", fake_run)
    generate_report_with_codex(tmp_path, evidence, prompt, output)
    assert output.read_text(encoding="utf-8") == "# Report\n"
