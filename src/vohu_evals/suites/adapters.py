from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import pickle
import re
import subprocess
import uuid
import zlib
from pathlib import Path

from vohu_evals.console.acp import docker_command
from vohu_evals.suites.packs import gpqa_score

UPSTREAM_REVISIONS = {
    "livecodebench": "28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24",
    "tau2": "672227c6b6676edc20d57ea53b7000262aae77b9",
    "swebench": "726c5461e2ef52d83cf1ea2107870a8bb3328d57",
}


class DataOnlyUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise ValueError("Executable pickle forbidden in test data")


def lcb_sample(row: dict) -> dict:
    if "grader_sha256" in row:
        checksum = row["grader_sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("Invalid grader artifact digest")
        path = (
            Path(__file__).resolve().parents[3]
            / ".local/suites/livecodebench/artifacts"
            / f"{checksum}.json"
        )
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != checksum:
            raise ValueError("Grader artifact integrity mismatch")
        row = json.loads(raw)
    private = row["private_test_cases"]
    try:
        hidden = json.loads(private)
    except json.JSONDecodeError:
        raw = zlib.decompress(base64.b64decode(private))
        decoded = DataOnlyUnpickler(io.BytesIO(raw)).load()
        if not isinstance(decoded, str):
            raise ValueError("Expected encoded JSON test data") from None
        hidden = json.loads(decoded)
    tests = json.loads(row["public_test_cases"]) + hidden
    if not tests:
        raise ValueError("Empty tests cannot pass")
    metadata = json.loads(row.get("metadata", "{}"))
    return {
        "input_output": json.dumps(
            {
                "inputs": [t["input"] for t in tests],
                "outputs": [t["output"] for t in tests],
                "fn_name": metadata.get("func_name"),
            }
        )
    }


async def grade_livecodebench(
    row: dict, code: str, timeout: int = 120, image: str = "lyra-evals-lcb-grader:local"
) -> dict:
    name = f"lyra-lcb-{uuid.uuid4().hex[:12]}"
    process = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        "-i",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=32m",
        image,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(timeout):
            output, _ = await process.communicate(
                json.dumps({"sample": lcb_sample(row), "code": code}).encode()
            )
        if process.returncode != 0:
            raise RuntimeError("Official LCB grader failed")
        result = json.loads(output)
        if not isinstance(result.get("correct"), bool) or result.get("metric") != "pass@1":
            raise ValueError("Invalid grader result")
        return result
    finally:
        await asyncio.shield(docker_command("rm", "-f", name))
        if process.returncode is None:
            process.kill()
            await process.wait()


def grade_text(suite: str, row: dict, answer: str, root: Path) -> dict:
    if suite == "gpqa":
        return gpqa_score(answer, row["expected"])
    if suite != "ifeval":
        raise ValueError("Text grading only supports IFEval and GPQA")
    from vohu_evals.benchmark import load_benchmark
    from vohu_evals.models import Case

    case = Case(
        case_id=row["case_id"],
        prompt=row["prompt"],
        expected=None,
        metadata={
            "instruction_id_list": row["instruction_id_list"],
            "kwargs": row["kwargs"],
            "official_evaluator": True,
        },
    )
    # IFEval's scorer does not use a model invocation; never invent billing evidence.
    score = load_benchmark(root, "ifeval").score_case(case, answer, None)
    return {
        "metric": score.metric,
        "correct": score.correct,
        "value": score.value,
        "details": score.details,
    }


def swe_predictions(rows: list[dict], patches: dict[str, str], model: str) -> list[dict]:
    ids = {r["case_id"] for r in rows}
    if set(patches) != ids:
        raise ValueError("SWE predictions must cover exactly the frozen case IDs")
    return [
        {
            "instance_id": r["case_id"],
            "model_name_or_path": model,
            "model_patch": patches[r["case_id"]],
        }
        for r in rows
    ]


def swe_command(
    upstream: Path, python: Path, dataset: Path, predictions: Path, run_id: str
) -> list[str]:
    revision = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != UPSTREAM_REVISIONS["swebench"]:
        raise ValueError("SWE evaluator revision mismatch")
    if not dataset.is_file() or not predictions.is_file():
        raise ValueError("Frozen dataset and predictions are required")
    return [
        str(python.absolute()),
        str(Path(__file__).resolve().parents[3] / "scripts/swe_runtime.py"),
        "--cache",
        str(Path(__file__).resolve().parents[3] / ".local/swe-specs"),
        "--dataset_name",
        str(dataset.resolve()),
        "--predictions_path",
        str(predictions.resolve()),
        "--run_id",
        run_id,
        "--max_workers",
        "1",
        "--timeout",
        "1800",
    ]


class TauSession:
    """Controller-side bridge. Hidden task/state stays outside the Agent sandbox."""

    def __init__(self, env):
        self.env = env
        self.finished = False
        self.audit = []

    def reset(self, seed: int) -> dict:
        observation, info = self.env.reset(seed=seed)
        self.finished = False
        self.audit = []
        return {
            "observation": observation,
            "policy": info["policy"],
            "tools": [tool.openai_schema for tool in info["tools"]],
        }

    def step(self, action: str) -> dict:
        if self.finished:
            raise ValueError("Episode already finished")
        observation, reward, terminated, truncated, info = self.env.step(action)
        self.finished = terminated or truncated
        self.audit.append(info)  # Controller evidence only; never returned to the model.
        if terminated:
            report = info.get("simulation_run")
            try:
                report = json.loads(report) if isinstance(report, str) else report
            except ValueError as exc:
                raise RuntimeError("Invalid official tau2 simulation evidence") from exc
            if not isinstance(report, dict) or not report:
                raise RuntimeError("tau2 terminated without a simulation; infrastructure failure")
        return {
            "observation": observation,
            "terminated": terminated,
            "truncated": truncated,
            "reward": reward if terminated else None,
        }

    def close(self):
        self.env.close()


def swe_score(report: dict, selected_ids: list[str]) -> dict:
    selected = set(selected_ids)
    if not selected or len(selected) != len(selected_ids):
        raise ValueError("Expected nonempty unique SWE selection")
    if set(report["submitted_ids"]) != selected:
        raise ValueError("Official SWE report coverage mismatch")
    if report.get("error_ids"):
        raise RuntimeError("Official SWE grader reported infrastructure failures")
    resolved = set(report["resolved_ids"])
    if not resolved <= selected:
        raise ValueError("Unknown resolved IDs")
    return {
        "metric": "resolved_rate",
        "count": len(selected),
        "correct": len(resolved),
        "value": len(resolved) / len(selected),
        "results": [
            {"case_id": identifier, "correct": identifier in resolved}
            for identifier in selected_ids
        ],
    }


def aggregate_scores(suite: str, rows: list[dict], scores: list[dict]) -> dict:
    expected = {row["case_id"]: row for row in rows}
    if not rows or len(expected) != len(rows) or len(scores) != len(rows):
        raise ValueError("Score coverage mismatch")
    if {score["case_id"] for score in scores} != set(expected):
        raise ValueError("Score IDs mismatch")
    passed = sum(bool(score["correct"]) for score in scores)
    summary = {
        "count": len(rows),
        "correct": passed,
        "system_failed": sum(s.get("status") == "system_failed" for s in scores),
    }
    if suite == "ifeval":
        instruction_total = sum(len(row["instruction_id_list"]) for row in rows)
        if instruction_total == 0:
            raise ValueError("IFEval instruction denominator missing")
        for score in scores:
            if score.get("status") == "system_failed":
                continue
            size = len(expected[score["case_id"]]["instruction_id_list"])
            details = score.get("details", {})
            if any(
                len(details.get(key, [])) != size
                for key in ["strict_instruction_list", "loose_instruction_list"]
            ):
                raise ValueError("IFEval scorer instruction coverage mismatch")
        summary["metrics"] = {
            "prompt_level_strict": passed / len(rows),
            "prompt_level_loose": sum(
                bool(s.get("details", {}).get("loose_prompt")) for s in scores
            )
            / len(rows),
            "instruction_level_strict": sum(
                sum(s.get("details", {}).get("strict_instruction_list", [])) for s in scores
            )
            / instruction_total,
            "instruction_level_loose": sum(
                sum(s.get("details", {}).get("loose_instruction_list", [])) for s in scores
            )
            / instruction_total,
        }
    else:
        summary["metrics"] = {
            "pass@1" if suite == "livecodebench" else "accuracy": passed / len(rows)
        }
    return summary
