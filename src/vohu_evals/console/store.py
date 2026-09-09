from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from vohu_evals.console.contracts import TargetInput


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        self.secrets = root / "secrets"
        self.secrets.mkdir(exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(root / "console.sqlite3")
        os.chmod(root / "console.sqlite3", 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS targets (id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)

    def targets(self) -> list[dict[str, Any]]:
        return [
            json.loads(r["metadata"])
            for r in self.db.execute("SELECT metadata FROM targets ORDER BY rowid DESC")
        ]

    def add_target(self, data: TargetInput) -> dict[str, Any]:
        identifier = uuid.uuid4().hex
        path = self.secrets / identifier
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(data.api_key)
            stream.flush()
            os.fsync(stream.fileno())
        public = {**data.model_dump(exclude={"api_key"}), "id": identifier, "has_key": True}
        try:
            self.db.execute("INSERT INTO targets VALUES (?, ?)", (identifier, json.dumps(public)))
            self.db.commit()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return public

    def set_price_cap(self, identifier: str, cap: dict):
        target = next((t for t in self.targets() if t["id"] == identifier), None)
        if target is None:
            raise ValueError("模型配置不存在")
        target["price_cap"] = cap
        self.db.execute(
            "UPDATE targets SET metadata=? WHERE id=?", (json.dumps(target), identifier)
        )
        self.db.commit()
        return target

    def delete_target(self, identifier: str) -> bool:
        if not any(t["id"] == identifier for t in self.targets()):
            return False
        self.db.execute("DELETE FROM targets WHERE id = ?", (identifier,))
        self.db.commit()
        (self.secrets / identifier).unlink(missing_ok=True)
        return True

    def save_run(self, run: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO runs VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (run["id"], json.dumps(run, ensure_ascii=False)),
        )
        self.db.commit()

    def runs(self) -> list[dict[str, Any]]:
        return [
            json.loads(r["payload"])
            for r in self.db.execute("SELECT payload FROM runs ORDER BY rowid DESC")
        ]

    def save_plan(self, plan: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO plans VALUES (?, ?)",
            (plan["id"], json.dumps(plan, ensure_ascii=False)),
        )
        self.db.commit()

    def plans(self) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.db.execute("SELECT payload FROM plans ORDER BY rowid DESC")
        ]
