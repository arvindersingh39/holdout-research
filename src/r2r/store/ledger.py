"""SQLite ledger of every LLM call with spend queries per run and milestone."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt

_COLUMNS = (
    "ts",
    "run_id",
    "milestone_id",
    "role",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
    "latency_ms",
    "ok",
    "error",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    ts TEXT NOT NULL, run_id TEXT NOT NULL, milestone_id TEXT, role TEXT NOT NULL,
    model TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL, cache_write_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL, latency_ms INTEGER NOT NULL, ok INTEGER NOT NULL, error TEXT
);
CREATE INDEX IF NOT EXISTS llm_calls_run ON llm_calls (run_id, milestone_id);
"""


class CallRecord(BaseModel):
    """One row of `llm_calls`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_id: str
    milestone_id: str | None = None
    role: str
    model: str
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cache_read_tokens: NonNegativeInt = 0
    cache_write_tokens: NonNegativeInt = 0
    cost_usd: NonNegativeFloat = 0.0
    latency_ms: NonNegativeInt = 0
    ok: bool = True
    error: str | None = None


class RoleSpend(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(_SCHEMA)

    def record(self, rec: CallRecord) -> None:
        values = rec.model_dump()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                f"INSERT INTO llm_calls ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                [values[c] for c in _COLUMNS],
            )

    def annotate_latest(self, run_id: str, role: str, error: str) -> None:
        """Attach a note (e.g. why an output was rejected) to the newest row for this role."""
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE llm_calls SET error = ? WHERE rowid = (SELECT MAX(rowid) FROM llm_calls "
                "WHERE run_id = ? AND role = ?)",
                (error, run_id, role),
            )

    def spent(self, run_id: str, milestone_id: str | None = None) -> float:
        sql, args = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE run_id = ?", [run_id]
        if milestone_id is not None:
            sql += " AND milestone_id = ?"
            args.append(milestone_id)
        with closing(sqlite3.connect(self.path)) as db:
            return float(db.execute(sql, args).fetchone()[0])

    def by_role(self, run_id: str) -> dict[str, RoleSpend]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT role, COUNT(*), SUM(input_tokens), SUM(output_tokens), "
                "SUM(cache_read_tokens), SUM(cost_usd) FROM llm_calls WHERE run_id = ? "
                "GROUP BY role ORDER BY role",
                (run_id,),
            ).fetchall()
        return {
            r[0]: RoleSpend(
                calls=r[1],
                input_tokens=r[2],
                output_tokens=r[3],
                cache_read_tokens=r[4],
                cost_usd=r[5],
            )
            for r in rows
        }

    def by_milestone(self, run_id: str) -> dict[str | None, float]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT milestone_id, SUM(cost_usd) FROM llm_calls WHERE run_id = ? "
                "GROUP BY milestone_id ORDER BY milestone_id",
                (run_id,),
            ).fetchall()
        return {m: float(c) for m, c in rows}

    def by_model(self, run_id: str) -> dict[str, RoleSpend]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens), "
                "SUM(cache_read_tokens), SUM(cost_usd) FROM llm_calls WHERE run_id = ? "
                "GROUP BY model ORDER BY model",
                (run_id,),
            ).fetchall()
        return {
            r[0]: RoleSpend(
                calls=r[1],
                input_tokens=r[2],
                output_tokens=r[3],
                cache_read_tokens=r[4],
                cost_usd=r[5],
            )
            for r in rows
        }

    def rows(self, run_id: str) -> list[CallRecord]:
        with closing(sqlite3.connect(self.path)) as db:
            cur = db.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM llm_calls WHERE run_id = ? ORDER BY rowid",
                (run_id,),
            )
            return [CallRecord(**dict(zip(_COLUMNS, r, strict=True))) for r in cur]
