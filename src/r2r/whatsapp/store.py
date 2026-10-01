"""Local SQLite state for WhatsApp gates: nonces, decisions, de-duplication, 24-hour window."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from r2r.contracts import HumanDecision
from r2r.graph.gates import GateRequest

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gates (
    gate_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, request_json TEXT NOT NULL,
    nonce TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
    last_ping_at REAL, shown INTEGER NOT NULL DEFAULT 0, reminders INTEGER NOT NULL DEFAULT 0,
    awaiting_note INTEGER NOT NULL DEFAULT 0, decision_json TEXT
);
CREATE TABLE IF NOT EXISTS processed (message_id TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Gate:
    gate_id: str
    run_id: str
    request: GateRequest
    nonce: str
    status: str  # pending | decided | consumed
    last_ping_at: float | None
    shown: bool
    reminders: int


class GateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(_SCHEMA)

    def _db(self) -> sqlite3.Connection:
        return closing(sqlite3.connect(self.path))  # type: ignore[return-value]

    @staticmethod
    def _row(r: tuple) -> Gate:
        return Gate(
            r[0], r[1], GateRequest.model_validate_json(r[2]), r[3], r[4], r[5], bool(r[6]), r[7]
        )

    _COLS = "gate_id, run_id, request_json, nonce, status, last_ping_at, shown, reminders"

    def open_gate(self, run_id: str, req: GateRequest, now: float) -> Gate:
        gate_id = hashlib.sha256(f"{run_id}|{req.model_dump_json()}".encode()).hexdigest()[:10]
        existing = self.get(gate_id)
        if existing is not None and existing.status != "consumed":
            return existing
        with self._db() as db, db:
            db.execute("DELETE FROM gates WHERE gate_id = ?", (gate_id,))
            db.execute(
                "INSERT INTO gates (gate_id, run_id, request_json, nonce, status, created_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (gate_id, run_id, req.model_dump_json(), secrets.token_hex(4), now),
            )
        return self.get(gate_id)  # type: ignore[return-value]

    def get(self, gate_id: str) -> Gate | None:
        with self._db() as db:
            r = db.execute(
                f"SELECT {self._COLS} FROM gates WHERE gate_id = ?", (gate_id,)
            ).fetchone()
        return self._row(r) if r else None

    def pending(self) -> list[Gate]:
        with self._db() as db:
            rows = db.execute(
                f"SELECT {self._COLS} FROM gates WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
        return [self._row(r) for r in rows]

    def mark_pinged(
        self, gate_id: str, now: float, shown: bool | None = None, reminder: bool = False
    ) -> None:
        with self._db() as db, db:
            db.execute(
                "UPDATE gates SET last_ping_at = ?, reminders = reminders + ? WHERE gate_id = ?",
                (now, int(reminder), gate_id),
            )
            if shown is not None:
                db.execute("UPDATE gates SET shown = ? WHERE gate_id = ?", (int(shown), gate_id))

    def decide(self, gate_id: str, decision: HumanDecision) -> None:
        with self._db() as db, db:
            db.execute(
                "UPDATE gates SET status = 'decided', awaiting_note = 0, decision_json = ? "
                "WHERE gate_id = ? AND status = 'pending'",
                (decision.model_dump_json(), gate_id),
            )

    def consume(self, gate_id: str) -> HumanDecision | None:
        with self._db() as db, db:
            r = db.execute(
                "SELECT decision_json FROM gates WHERE gate_id = ? AND status = 'decided'",
                (gate_id,),
            ).fetchone()
            if not r:
                return None
            db.execute("UPDATE gates SET status = 'consumed' WHERE gate_id = ?", (gate_id,))
        return HumanDecision.model_validate_json(r[0])

    def set_awaiting_note(self, gate_id: str) -> None:
        with self._db() as db, db:
            db.execute("UPDATE gates SET awaiting_note = 0")
            db.execute("UPDATE gates SET awaiting_note = 1 WHERE gate_id = ?", (gate_id,))

    def awaiting_note(self) -> Gate | None:
        with self._db() as db:
            r = db.execute(
                f"SELECT {self._COLS} FROM gates WHERE awaiting_note = 1 AND status = 'pending'"
            ).fetchone()
        return self._row(r) if r else None

    def first_seen(self, message_id: str, now: float) -> bool:
        """True the first time a message id is seen (Meta may deliver an event twice)."""
        with self._db() as db, db:
            cur = db.execute("INSERT OR IGNORE INTO processed VALUES (?, ?)", (message_id, now))
            return cur.rowcount == 1

    def touch_inbound(self, now: float) -> None:
        with self._db() as db, db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_inbound_at', ?)", (str(now),))

    def last_inbound(self) -> float | None:
        with self._db() as db:
            r = db.execute("SELECT value FROM meta WHERE key = 'last_inbound_at'").fetchone()
        return float(r[0]) if r else None
