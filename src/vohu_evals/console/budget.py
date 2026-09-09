"""Durable reservations in integer micro-USD; no network or provider assumptions."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path


def micros(value: str) -> int:
    amount = Decimal(value)
    scaled = amount * 1000000
    if not amount.is_finite() or amount < 0 or scaled != scaled.to_integral_value():
        raise ValueError("费用必须是非负且最多六位小数的 USD")
    if scaled > 10**15:
        raise ValueError("费用超过支持范围")
    return int(scaled)


class BudgetLedger:
    """Each worker owns a connection; BEGIN IMMEDIATE serializes competing reservations."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS budgets (id TEXT PRIMARY KEY, cap INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS reservations (
                request_id TEXT PRIMARY KEY, budget_id TEXT NOT NULL,
                reserved INTEGER NOT NULL, settled INTEGER, event_id TEXT UNIQUE
            );
        """)

    def define(self, identifier: str, cap: str) -> None:
        value = micros(cap)
        if value <= 0:
            raise ValueError("预算必须大于零")
        self.db.execute("INSERT OR IGNORE INTO budgets VALUES (?, ?)", (identifier, value))
        if (
            self.db.execute("SELECT cap FROM budgets WHERE id=?", (identifier,)).fetchone()[0]
            != value
        ):
            raise ValueError("预算冻结后不可修改")

    def reserve(self, budget_id: str, request_id: str, upper_bound: str) -> None:
        amount = micros(upper_bound)
        if amount <= 0:
            raise ValueError("请求需要已验证的正费用上界")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute(
                "SELECT budget_id,reserved FROM reservations WHERE request_id=?", (request_id,)
            ).fetchone()
            if old is not None:
                if old != (budget_id, amount):
                    raise ValueError("请求 ID 已用于不同预留")
            else:
                budget = self.db.execute(
                    "SELECT cap FROM budgets WHERE id=?", (budget_id,)
                ).fetchone()
                if budget is None:
                    raise ValueError("预算不存在")
                committed = self.db.execute(
                    "SELECT COALESCE(SUM(COALESCE(settled,reserved)),0) "
                    "FROM reservations WHERE budget_id=?",
                    (budget_id,),
                ).fetchone()[0]
                if committed + amount > budget[0]:
                    raise ValueError("预算不足，停止派发")
                self.db.execute(
                    "INSERT INTO reservations VALUES (?,?,?,NULL,NULL)",
                    (request_id, budget_id, amount),
                )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def settle(self, request_id: str, event_id: str, actual: str) -> None:
        amount = micros(actual)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT settled,event_id FROM reservations WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise ValueError("无法结算没有预留的请求")
            if row[0] is not None:
                if row != (amount, event_id):
                    raise ValueError("结算冲突，需要人工对账")
            else:
                # Record overruns truthfully; subsequent reservations fail against actual usage.
                self.db.execute(
                    "UPDATE reservations SET settled=?,event_id=? WHERE request_id=?",
                    (amount, event_id, request_id),
                )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def summary(self, budget_id: str) -> dict:
        row = self.db.execute("SELECT cap FROM budgets WHERE id=?", (budget_id,)).fetchone()
        if row is None:
            raise ValueError("预算不存在")
        reserved, settled, pending = self.db.execute(
            "SELECT COALESCE(SUM(CASE WHEN settled IS NULL THEN reserved ELSE 0 END),0),"
            "COALESCE(SUM(settled),0),COUNT(CASE WHEN settled IS NULL THEN 1 END) "
            "FROM reservations WHERE budget_id=?",
            (budget_id,),
        ).fetchone()
        return {
            "cap": str(Decimal(row[0]) / 1000000),
            "reserved": str(Decimal(reserved) / 1000000),
            "settled": str(Decimal(settled) / 1000000),
            "pending": pending,
            "over_budget": reserved + settled > row[0],
        }

    def close(self):
        self.db.close()
