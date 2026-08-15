from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from vohu_evals.models import Case, CaseScore, CaseStatus, InvocationResult, RunSpec, RunSummary


class SQLiteLedger:
    """Authoritative, resumable local run ledger."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY,
              manifest_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'running'
            );
            CREATE TABLE IF NOT EXISTS cases (
              run_id TEXT NOT NULL,
              case_id TEXT NOT NULL,
              prompt TEXT NOT NULL,
              expected_json TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL,
              score REAL,
              correct INTEGER,
              metric TEXT,
              score_details_json TEXT,
              response_json TEXT,
              request_id TEXT,
              execution_id TEXT,
              response_model TEXT,
              attempt_models_json TEXT,
              attempts_json TEXT,
              usage_json TEXT,
              cost_usd REAL NOT NULL DEFAULT 0,
              latency_ms INTEGER,
              error TEXT,
              retry_count INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (run_id, case_id)
            );
            """
        )
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(cases)").fetchall()
        }
        migrations = {
            "metadata_json": (
                "ALTER TABLE cases ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
            ),
            "score_details_json": "ALTER TABLE cases ADD COLUMN score_details_json TEXT",
            "attempts_json": "ALTER TABLE cases ADD COLUMN attempts_json TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                self.connection.execute(statement)
        self.connection.commit()

    def initialize(self, spec: RunSpec, manifest: dict[str, Any], cases: list[Case]) -> None:
        manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        existing = self.connection.execute(
            "SELECT manifest_json FROM runs WHERE run_id = ?", (spec.run_id,)
        ).fetchone()
        if existing and existing["manifest_json"] != manifest_json:
            raise ValueError("run manifest is immutable")
        self.connection.execute(
            "INSERT OR IGNORE INTO runs(run_id, manifest_json) VALUES (?, ?)",
            (spec.run_id, manifest_json),
        )
        for case in cases:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO cases(
                  run_id, case_id, prompt, expected_json, metadata_json, status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.run_id,
                    case.case_id,
                    case.prompt,
                    json.dumps(case.expected, ensure_ascii=False, sort_keys=True),
                    json.dumps(case.metadata, ensure_ascii=False, sort_keys=True),
                    CaseStatus.PENDING,
                ),
            )
        self.connection.commit()

    def pending_case_ids(self, run_id: str) -> tuple[str, ...]:
        rows = self.connection.execute(
            "SELECT case_id FROM cases WHERE run_id = ? AND status = ? ORDER BY case_id",
            (run_id, CaseStatus.PENDING),
        ).fetchall()
        return tuple(row["case_id"] for row in rows)

    def mark_completed_if_settled(self, run_id: str) -> bool:
        """Close a run only when every frozen case has reached a terminal state."""
        cursor = self.connection.execute(
            """
            UPDATE runs SET status = 'completed'
            WHERE run_id = ?
              AND NOT EXISTS (
                SELECT 1 FROM cases WHERE run_id = ? AND status = ?
              )
            """,
            (run_id, run_id, CaseStatus.PENDING),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def run_status(self, run_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return str(row["status"]) if row is not None else None

    def run_manifest(self, run_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT manifest_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown run: {run_id}")
        return dict(json.loads(row["manifest_json"]))

    def complete(
        self,
        run_id: str,
        case_id: str,
        result: InvocationResult,
        score: CaseScore,
        retry_count: int,
    ) -> None:
        self.connection.execute(
            """
            UPDATE cases SET status = ?, score = ?, correct = ?, metric = ?,
              score_details_json = ?, response_json = ?, request_id = ?, execution_id = ?,
              response_model = ?, attempt_models_json = ?, attempts_json = ?, usage_json = ?,
              cost_usd = ?, latency_ms = ?, retry_count = ?
            WHERE run_id = ? AND case_id = ?
            """,
            (
                CaseStatus.COMPLETED,
                score.value,
                int(score.correct),
                score.metric,
                json.dumps(score.details, ensure_ascii=False, sort_keys=True),
                json.dumps(result.raw_response, ensure_ascii=False, sort_keys=True),
                result.request_id,
                result.execution_id,
                result.response_model,
                json.dumps(result.attempt_models),
                json.dumps(result.attempts, ensure_ascii=False, sort_keys=True),
                json.dumps(result.usage, sort_keys=True),
                result.cost_usd,
                result.latency_ms,
                retry_count,
                run_id,
                case_id,
            ),
        )
        self.connection.commit()

    def fail(
        self,
        run_id: str,
        case_id: str,
        status: CaseStatus,
        error: str,
        retry_count: int,
        *,
        result: InvocationResult | None = None,
    ) -> None:
        if result is None:
            self.connection.execute(
                """
                UPDATE cases SET status = ?, error = ?, retry_count = ?
                WHERE run_id = ? AND case_id = ?
                """,
                (status, error[:1024], retry_count, run_id, case_id),
            )
        else:
            self.connection.execute(
                """
                UPDATE cases SET status = ?, error = ?, retry_count = ?, response_json = ?,
                  request_id = ?, execution_id = ?, response_model = ?, usage_json = ?,
                  cost_usd = ?, latency_ms = ?
                WHERE run_id = ? AND case_id = ?
                """,
                (
                    status,
                    error[:1024],
                    retry_count,
                    json.dumps(result.raw_response, ensure_ascii=False, sort_keys=True),
                    result.request_id,
                    result.execution_id,
                    result.response_model,
                    json.dumps(result.usage, sort_keys=True),
                    result.cost_usd,
                    result.latency_ms,
                    run_id,
                    case_id,
                ),
            )
        self.connection.commit()

    def summary(self, run_id: str) -> RunSummary:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS total,
              SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
              SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) AS correct,
              SUM(CASE WHEN status = 'system_failed' THEN 1 ELSE 0 END) AS system_failed,
              SUM(CASE WHEN status = 'invalid_output' THEN 1 ELSE 0 END) AS invalid_output,
              SUM(CASE WHEN status = 'pending' THEN 0 ELSE retry_count + 1 END) AS requests,
              SUM(cost_usd) AS cost
            FROM cases WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        return RunSummary(
            run_id=run_id,
            total_cases=int(row["total"] or 0),
            completed_cases=int(row["completed"] or 0),
            correct_cases=int(row["correct"] or 0),
            system_failed_cases=int(row["system_failed"] or 0),
            invalid_output_cases=int(row["invalid_output"] or 0),
            total_requests=int(row["requests"] or 0),
            total_cost_usd=float(row["cost"] or 0.0),
        )

    def case_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM cases WHERE run_id = ? ORDER BY case_id", (run_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.connection.close()
