"""Score saved answers without model calls; raw inputs and outputs remain local."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
from pathlib import Path

from vohu_evals.suites.adapters import (
    aggregate_scores,
    grade_livecodebench,
    grade_text,
    swe_command,
    swe_predictions,
    swe_score,
)
from vohu_evals.suites.packs import load_pack

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=["ifeval", "gpqa", "livecodebench", "swebench"])
    parser.add_argument(
        "--answers", type=Path, required=True, help="JSON object case_id -> final answer/code/patch"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--case-ids",
        nargs="+",
        help="Explicit subset; retained in output, never called full-set score",
    )
    parser.add_argument("--run-id", default="local-evaluation")
    parser.add_argument(
        "--execute-swe",
        action="store_true",
        help="Run the official Docker evaluator (can build large images)",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", args.run_id):
        raise ValueError("Invalid run ID")
    manifest, rows = load_pack(ROOT / ".local/suites", args.suite)
    answers = json.loads(args.answers.read_text())
    if args.case_ids:
        ids = set(args.case_ids)
        rows = [r for r in rows if r["case_id"] in ids]
        if len(rows) != len(ids):
            raise ValueError("Unknown subset IDs")
    if set(answers) != {row["case_id"] for row in rows} or not all(
        isinstance(v, str) for v in answers.values()
    ):
        raise ValueError(
            "Answers must cover the exact frozen selection; use empty text for failed tasks"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.suite == "swebench":
        dataset = args.output.parent / "swe-dataset.json"
        predictions = args.output.parent / "swe-predictions.json"
        dataset.write_text(json.dumps(rows))
        predictions.write_text(json.dumps(swe_predictions(rows, answers, "external-agent")))
        command = swe_command(
            ROOT / ".local/upstream/swebench",
            ROOT / ".local/benchmark-runtime/bin/python",
            dataset,
            predictions,
            args.run_id,
        )
        official_score = None
        if args.execute_swe:
            subprocess.run(command, cwd=args.output.parent, check=True)
            report_path = args.output.parent / f"external-agent.{args.run_id}.json"
            official_score = swe_score(
                json.loads(report_path.read_text()), [row["case_id"] for row in rows]
            )
        args.output.write_text(
            json.dumps(
                {
                    "manifest": manifest,
                    "selected_ids": [r["case_id"] for r in rows],
                    "official_command": command,
                    "executed": args.execute_swe,
                    "score": official_score,
                },
                indent=2,
            )
        )
        return
    results = []
    for row in rows:
        answer = answers[row["case_id"]]
        try:
            score = (
                asyncio.run(grade_livecodebench(row, answer))
                if args.suite == "livecodebench"
                else grade_text(args.suite, row, answer, ROOT)
            )
            results.append({"case_id": row["case_id"], "status": "scored", **score})
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            results.append(
                {
                    "case_id": row["case_id"],
                    "status": "system_failed",
                    "correct": False,
                    "error": type(exc).__name__,
                }
            )
    args.output.write_text(
        json.dumps(
            {
                "manifest": manifest,
                "subset": bool(args.case_ids),
                "count": len(rows),
                "summary": aggregate_scores(args.suite, rows, results),
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
