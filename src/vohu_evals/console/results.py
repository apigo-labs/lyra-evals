"""One public projection for the console and exports; never includes raw answers."""

from __future__ import annotations

import csv
import io
import math
from statistics import mean
from typing import Any


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def result_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for run in runs:
        manifest = run.get("manifest", {})
        synthetic = run["mode"] != "live" or manifest.get("synthetic", False)
        variants = {v["id"]: v for v in manifest.get("variants", [])}
        for job in run["jobs"]:
            variant = variants.get(job.get("variant_id"), {})
            latencies = job.get("latencies", [])
            complete = job["completed"] == job["total"] and job["status"] == "completed"
            requests = [q for e in job.get("episodes", []) for q in e.get("requests", [])]
            known_estimates = [
                q["estimated_cost_usd"] for q in requests if q.get("estimated_cost_usd") is not None
            ]
            cost = job.get("cost")
            settled = cost is not None and (synthetic or job.get("billing_status") == "settled")
            scored = job.get("scored", job["completed"] if complete else 0)
            rows.append(
                {
                    "run_id": run["id"],
                    "run_name": run["name"],
                    "job_id": job["id"],
                    "created_at": run["created_at"],
                    "benchmark": job["benchmark"],
                    "model": variant.get("connection", {}).get("model", job["target_name"]),
                    "harness": variant.get("harness", "synthetic" if synthetic else "unknown"),
                    "effort": variant.get("effort", "unknown"),
                    "effective_effort": variant.get("effective_effort"),
                    "variant_id": job.get("variant_id"),
                    "trial": job.get("trial"),
                    "execution_sha256": manifest.get("execution_sha256"),
                    "data_sha256": manifest.get("datasets", {})
                    .get(job["benchmark"], {})
                    .get("sha256"),
                    "max_output_tokens": variant.get("max_output_tokens"),
                    "deadline_seconds": variant.get("deadline_seconds"),
                    "episode_budget_usd": variant.get("episode_budget"),
                    "pricing_basis": variant.get("pricing_basis", manifest.get("pricing_basis")),
                    "comparison": "not_comparable"
                    if synthetic
                    else manifest.get("comparison", "reference_only"),
                    "synthetic": synthetic,
                    "status": job["status"],
                    "planned": job["total"],
                    "completed": job["completed"],
                    "scored": scored,
                    "correct": None if synthetic else job["correct"],
                    "accuracy": job["correct"] / job["total"]
                    if complete and not synthetic and job["total"]
                    else None,
                    "provisional_accuracy": job["correct"] / job["total"]
                    if not synthetic and job["total"]
                    else None,
                    "cost_usd": cost if settled else None,
                    "estimated_cost_usd": job.get("estimated_cost_usd"),
                    "known_estimated_cost_usd": sum(known_estimates) if known_estimates else None,
                    "requests_missing_usage": len(requests) - len(known_estimates),
                    "reserved_usd": job.get("reserved_usd"),
                    "billing_status": "not_applicable"
                    if synthetic
                    else "settled"
                    if settled
                    else "pending",
                    "cost_per_correct_usd": cost / job["correct"]
                    if complete and settled and not synthetic and job["correct"]
                    else None,
                    "latency_mean_ms": mean(latencies) if latencies else None,
                    "latency_p50_ms": percentile(latencies, 0.5),
                    "latency_p95_ms": percentile(latencies, 0.95),
                    "latency_samples": len(latencies),
                    "wall_time_ms": job.get("wall_time_ms"),
                    "metric": job.get(
                        "metric",
                        {
                            "gpqa": "accuracy",
                            "ifeval": "prompt_strict_accuracy",
                            "livecodebench": "pass@1",
                            "swebench": "resolved_rate",
                            "tau2": "success_rate",
                        }.get(job["benchmark"], "accuracy"),
                    ),
                }
            )
    return rows


def csv_export(rows: list[dict[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    fields = (
        list(rows[0])
        if rows
        else ["run_id", "model", "benchmark", "accuracy", "cost_usd", "latency_mean_ms"]
    )
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        # Spreadsheet formula injection applies even to quoted CSV cells.
        writer.writerow(
            {
                k: "'" + v
                if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                else v
                for k, v in row.items()
            }
        )
    return "\ufeff" + stream.getvalue()


def episode_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = {r["job_id"]: r for r in result_rows(runs)}
    rows = []
    for run in runs:
        for job in run["jobs"]:
            summary = summaries[job["id"]]
            for episode in job.get("episodes", []):
                requests = episode.get("requests", [])
                rows.append(
                    {
                        **{
                            k: summary[k]
                            for k in (
                                "run_id",
                                "run_name",
                                "job_id",
                                "benchmark",
                                "model",
                                "harness",
                                "effort",
                                "synthetic",
                                "comparison",
                            )
                        },
                        "case_id": episode["case_id"],
                        "status": episode["status"],
                        "correct": None if summary["synthetic"] else episode.get("correct"),
                        "latency_ms": episode.get("latency_ms"),
                        "cost_usd": episode.get("cost_usd")
                        if episode.get("billing_status") == "settled"
                        else None,
                        "estimated_cost_usd": sum(r["estimated_cost_usd"] for r in requests)
                        if requests
                        and all(r.get("estimated_cost_usd") is not None for r in requests)
                        else None,
                        "reserved_usd": episode.get("reserved_usd"),
                        "request_count": len(requests),
                        "request_ids": " ".join(
                            r["request_id"] for r in requests if r.get("request_id")
                        ),
                        "error": episode.get("error"),
                    }
                )
    return rows
