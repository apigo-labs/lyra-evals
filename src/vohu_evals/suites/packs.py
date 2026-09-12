from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import tempfile
from pathlib import Path

SUITES = {"ifeval", "gpqa", "livecodebench", "tau2", "swebench"}


def dataset_root(root: Path, suite: str, swe_subset: str = "verified") -> Path:
    if swe_subset not in {"lite", "verified"}:
        raise ValueError("Unknown SWE subset")
    return root / "variants/lite" if suite == "swebench" and swe_subset == "lite" else root


def atomic_write(path: Path, payload: bytes) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".prepare-")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def freeze_pack(root: Path, suite: str, rows: list[dict], source: dict) -> dict:
    if suite not in SUITES or not rows:
        raise ValueError("Unknown or empty suite")
    ids = [r["case_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case IDs")
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode()
    checksum = hashlib.sha256(payload).hexdigest()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    directory = root / suite
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{checksum}.json"
    atomic_write(path, payload)
    manifest = {"suite": suite, "count": len(rows), "sha256": checksum, "source": source}
    atomic_write(
        directory / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    )
    return manifest


def load_pack(root: Path, suite: str) -> tuple[dict, list[dict]]:
    if suite not in SUITES:
        raise ValueError("Unknown suite")
    manifest = json.loads((root / suite / "manifest.json").read_text())
    if manifest.get("suite") != suite:
        raise ValueError("Pack suite mismatch")
    checksum = manifest["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise ValueError("Invalid pack digest")
    raw = (root / suite / f"{checksum}.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError("Pack integrity mismatch")
    rows = json.loads(raw)
    if len(rows) != manifest["count"] or len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("Pack count or IDs mismatch")
    return manifest, rows


# GPQA is a closed-book knowledge/reasoning benchmark. The ACP agent surfaces tool
# schemas (shell, browsing, etc.) by default in some harnesses; without an explicit
# instruction the model repeatedly attempts to call them, burns the deadline/episode
# budget across several request round-trips, and never emits a final answer. This
# derived closed-book protocol note (本地提示为派生闭卷协议) tells the model up front
# that no tool exists for this task and it must answer in this single response.
GPQA_CLOSED_BOOK_PROTOCOL = (
    "This is a closed-book, single-turn question with no tools, browsing, code "
    "execution, or file access available or permitted. Do not attempt to call any "
    "tool or function; answer entirely from your own knowledge in this one response. "
    "Finish with your choice on its own final line, in plain "
    "text with no markdown emphasis, in exactly this format: Answer: A, B, C, or D."
)


def gpqa_rows(path: Path, seed: int = 0) -> list[dict]:
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for index, raw in enumerate(csv.DictReader(stream)):
            question = raw["Question"].strip()
            choices = [raw["Correct Answer"], *(raw[f"Incorrect Answer {i}"] for i in range(1, 4))]
            order = list(range(4))
            random.Random(f"{seed}:{index}:{question}").shuffle(order)
            expected = "ABCD"[order.index(0)]
            prompt = (
                question
                + "\n\n"
                + "\n".join(
                    f"{letter}. {choices[j]}" for letter, j in zip("ABCD", order, strict=True)
                )
                + "\n\n"
                + GPQA_CLOSED_BOOK_PROTOCOL
            )
            rows.append(
                {
                    "case_id": str(index),
                    "prompt": prompt,
                    "expected": expected,
                    "choice_order": order,
                }
            )
    return rows


def gpqa_score(answer: str, expected: str) -> dict:
    # Accept a bare label or an explicit final line; never guess from the last prose
    # character. Markdown emphasis markers observed from real ACP transcripts (e.g.
    # "**Answer: A**") are stripped first so they don't break the end-of-string
    # anchor; this does not loosen the match itself, which still requires the
    # explicit "answer:" keyword or a bare letter alone on its own line, with the
    # last explicit statement winning over any earlier one.
    cleaned = re.sub(r"[*_`]", "", answer).strip()
    matches = re.findall(r"(?im)^\s*(?:answer\s*:\s*)?([ABCD])[.)]?\s*$", cleaned)
    explicit = re.search(r"(?i)\banswer\s*:\s*([ABCD])[.)]?\s*$", cleaned)
    selected = explicit[1].upper() if explicit else matches[-1].upper() if matches else None
    return {
        "metric": "accuracy",
        "correct": selected == expected,
        "value": float(selected == expected),
        "parsed_answer": selected,
    }


def agent_input(suite: str, row: dict) -> dict:
    """Allowlist projection: hidden tests, gold patches, task state never enter Agent context."""
    if suite in {"ifeval", "gpqa"}:
        return {"case_id": row["case_id"], "prompt": row["prompt"]}
    if suite == "livecodebench":
        return {
            "case_id": row["case_id"],
            "prompt": row["question_content"],
            "starter_code": row.get("starter_code", ""),
            "public_test_cases": row.get("public_test_cases", "[]"),
        }
    if suite == "swebench":
        return {
            "case_id": row["case_id"],
            "prompt": row["problem_statement"],
            "repo": row["repo"],
            "base_commit": row["base_commit"],
        }
    if suite == "tau2":
        return {"case_id": row["case_id"], "domain": row["domain"], "task_id": row["task_id"]}
    raise ValueError("Unknown suite")
