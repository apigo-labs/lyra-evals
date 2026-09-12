import base64
import csv
import json
import pickle
import zlib

import pytest

from vohu_evals.suites.adapters import TauSession, lcb_sample, swe_predictions
from vohu_evals.suites.packs import agent_input, freeze_pack, gpqa_rows, gpqa_score, load_pack


def test_pack_integrity_and_hidden_fields(tmp_path):
    row = {
        "case_id": "1",
        "problem_statement": "fix",
        "repo": "test/repo",
        "base_commit": "abc",
        "patch": "SECRET",
        "test_patch": "HIDDEN",
    }
    manifest = freeze_pack(tmp_path, "swebench", [row], {"revision": "test"})
    _, rows = load_pack(tmp_path, "swebench")
    assert agent_input("swebench", rows[0]) == {
        "case_id": "1",
        "prompt": "fix",
        "repo": "test/repo",
        "base_commit": "abc",
    }
    (tmp_path / "swebench" / (manifest["sha256"] + ".json")).write_text("[]")
    with pytest.raises(ValueError):
        load_pack(tmp_path, "swebench")


def test_gpqa_shuffle_and_strict_answer(tmp_path):
    path = tmp_path / "data.csv"
    row = {
        "Question": "Synthetic?",
        "Correct Answer": "right",
        "Incorrect Answer 1": "wrong1",
        "Incorrect Answer 2": "wrong2",
        "Incorrect Answer 3": "wrong3",
    }
    with path.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    first = gpqa_rows(path, seed=12)
    assert first == gpqa_rows(path, seed=12)
    answer = first[0]["expected"]
    assert f"{answer}. right" in first[0]["prompt"]
    assert gpqa_score("Reasoning\nAnswer: " + answer, answer)["correct"]
    assert gpqa_score("Answer: " + answer.lower(), answer)["correct"]
    assert not gpqa_score("This prose ends with " + answer, answer)["correct"]
    assert "expected" not in agent_input("gpqa", first[0])


def test_lcb_encoded_tests_and_public_projection():
    tests = [{"input": "1", "output": "2", "testtype": "stdin"}]
    encoded = base64.b64encode(zlib.compress(pickle.dumps(json.dumps(tests)))).decode()
    row = {
        "case_id": "1",
        "question_content": "Increment",
        "private_test_cases": encoded,
        "public_test_cases": "[]",
        "metadata": "{}",
    }
    assert json.loads(lcb_sample(row)["input_output"])["outputs"] == ["2"]
    assert "private_test_cases" not in agent_input("livecodebench", row)
    with pytest.raises(ValueError):
        lcb_sample(
            {
                **row,
                "private_test_cases": base64.b64encode(zlib.compress(pickle.dumps(eval))).decode(),
            }
        )


def test_swe_exact_coverage():
    rows = [{"case_id": "one"}, {"case_id": "two"}]
    with pytest.raises(ValueError):
        swe_predictions(rows, {"one": "patch"}, "model")
    assert swe_predictions(rows, {"one": "patch", "two": ""}, "model")[1]["model_patch"] == ""


def test_tau_bridge_never_exposes_hidden_state():
    class Tool:
        @property
        def openai_schema(self):
            return {"type": "function", "function": {"name": "test"}}

    class Env:
        def reset(self, seed):
            return "hello", {"policy": "policy", "tools": [Tool()], "task": "SECRET"}

        def step(self, action):
            return "done", 1, True, False, {"simulation_run": json.dumps({"id": "PRIVATE"})}

        def close(self):
            pass

    session = TauSession(Env())
    assert "SECRET" not in str(session.reset(0))
    result = session.step("hello")
    assert result["reward"] == 1 and "PRIVATE" not in str(result)
    with pytest.raises(ValueError):
        session.step("again")
    session.close()


def test_swe_report_denominator_and_unknown_results():
    from vohu_evals.suites.adapters import swe_score

    report = {"submitted_ids": ["a", "b"], "resolved_ids": ["a"]}
    assert swe_score(report, ["a", "b"])["value"] == 0.5
    with pytest.raises(ValueError):
        swe_score(report, ["a"])


def test_plan_freezes_dataset_selection_without_answers(tmp_path):
    from vohu_evals.console.planner import PlanInput, build_plan

    rows = [{"case_id": str(i), "prompt": "question", "expected": "SECRET"} for i in range(5)]
    freeze_pack(tmp_path, "gpqa", rows, {"revision": "test"})
    target = {
        "id": "test",
        "model": "gpt-test",
        "protocol": "openai_responses",
        "endpoint": "https://gateway.example/v1",
    }
    spec = PlanInput(
        name="Test",
        benchmarks=["gpqa"],
        sample_size=3,
        variants=[{"target_id": "test", "harness": "codex", "efforts": ["high"]}],
    )
    plan = build_plan(spec, [target], tmp_path)
    assert len(plan["manifest"]["datasets"]["gpqa"]["case_ids"]) == 3
    assert "SECRET" not in str(plan)
    spec.sample_size = 6
    with pytest.raises(ValueError):
        build_plan(spec, [target], tmp_path)


def test_swe_preserves_virtualenv_interpreter(tmp_path, monkeypatch):
    from vohu_evals.suites.adapters import UPSTREAM_REVISIONS, swe_command

    executable = tmp_path / "runtime/bin/python"
    executable.parent.mkdir(parents=True)
    base = tmp_path / "base-python"
    base.write_text("")
    executable.symlink_to(base)
    dataset = tmp_path / "data.json"
    predictions = tmp_path / "predictions.json"
    dataset.write_text("[]")
    predictions.write_text("[]")
    monkeypatch.setattr(
        "vohu_evals.suites.adapters.subprocess.check_output",
        lambda *a, **k: UPSTREAM_REVISIONS["swebench"],
    )
    command = swe_command(tmp_path, executable, dataset, predictions, "test")
    assert command[0] == str(executable.absolute())
    assert command[0] != str(base)


def test_console_data_status_is_not_execution_readiness(tmp_path):
    from vohu_evals.suites.registry import suite_status

    items = [{"id": "gpqa", "cases": 198}]
    assert not suite_status(tmp_path, items)[0]["data_ready"]
    manifest = freeze_pack(
        tmp_path,
        "gpqa",
        [{"case_id": "test", "prompt": "x", "expected": "A"}],
        {"revision": "synthetic"},
    )
    ready = suite_status(tmp_path, items)[0]
    assert ready["data_ready"] and not ready["live_ready"] and ready["cases"] == 1
    (tmp_path / "gpqa" / (manifest["sha256"] + ".json")).write_text("[]")
    assert not suite_status(tmp_path, items)[0]["data_ready"]


def test_ifeval_failures_remain_in_instruction_denominator():
    from vohu_evals.suites.adapters import aggregate_scores

    rows = [
        {"case_id": "a", "instruction_id_list": ["one", "two"]},
        {"case_id": "b", "instruction_id_list": ["three"]},
    ]
    scores = [
        {
            "case_id": "a",
            "status": "scored",
            "correct": False,
            "details": {
                "strict_instruction_list": [True, False],
                "loose_instruction_list": [True, True],
                "loose_prompt": True,
            },
        },
        {"case_id": "b", "status": "system_failed", "correct": False},
    ]
    summary = aggregate_scores("ifeval", rows, scores)
    assert summary["metrics"]["instruction_level_strict"] == 1 / 3
    assert summary["metrics"]["prompt_level_loose"] == 0.5


def test_swe_infrastructure_errors_are_not_wrong_answers():
    from vohu_evals.suites.adapters import swe_score

    with pytest.raises(RuntimeError):
        swe_score({"submitted_ids": ["a"], "resolved_ids": [], "error_ids": ["a"]}, ["a"])


def test_swe_missing_environment_stops_before_inference(tmp_path):
    import asyncio

    from vohu_evals.suites.swe_live import preflight

    with pytest.raises(ValueError, match="SWE"):
        asyncio.run(preflight([{"case_id": "a"}], tmp_path))


def test_swe_patch_includes_new_and_modified_files(tmp_path):
    import asyncio
    import subprocess

    from vohu_evals.suites.swe_live import collect_patch

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "existing.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "existing.py").write_text("x = 2\n")
    (tmp_path / "new.py").write_text("new = True\n")
    patch = asyncio.run(collect_patch(tmp_path))
    assert "+x = 2" in patch and "+new = True" in patch


def test_swe_lite_is_separate_and_frozen_in_plan(tmp_path):
    from vohu_evals.console.planner import PlanInput, build_plan
    from vohu_evals.suites.packs import dataset_root

    freeze_pack(tmp_path, "swebench", [{"case_id": "verified"}], {"subset": "verified"})
    freeze_pack(
        dataset_root(tmp_path, "swebench", "lite"),
        "swebench",
        [{"case_id": "lite"}],
        {"subset": "lite"},
    )
    target = {
        "id": "test",
        "model": "gpt-test",
        "protocol": "openai_responses",
        "endpoint": "https://api.apigo.ai/v1",
    }
    spec = PlanInput(
        name="Lite",
        benchmarks=["swebench"],
        swe_subset="lite",
        sample_size=1,
        variants=[{"target_id": "test", "harness": "codex", "efforts": ["high"]}],
    )
    plan = build_plan(spec, [target], tmp_path)
    assert plan["manifest"]["datasets"]["swebench"]["case_ids"] == ["lite"]
    assert plan["manifest"]["datasets"]["swebench"]["source"]["subset"] == "lite"
    assert load_pack(tmp_path, "swebench")[1][0]["case_id"] == "verified"
    spec.swe_subset = "verified"
    assert build_plan(spec, [target], tmp_path)["id"] != plan["id"]
    with pytest.raises(ValueError):
        dataset_root(tmp_path, "swebench", "../other")
