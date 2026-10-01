"""Content-addressed (sha256) blob store with a SQLite index; put, get, get_slice."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from r2r.contracts import BlobKind, BlobRef, Role
from r2r.llm.tokens import estimate_tokens

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs (
    blob_id TEXT PRIMARY KEY, kind TEXT NOT NULL, summary TEXT NOT NULL,
    est_tokens INTEGER NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL
)
"""


class BlobNotFound(KeyError):
    pass


class BlobCorrupted(Exception):
    """A blob's bytes no longer match its sha256."""


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index = self.root / "index.sqlite"
        with closing(sqlite3.connect(self._index)) as db, db:
            db.execute(_SCHEMA)

    def _path(self, blob_id: str) -> Path:
        return self.root / blob_id[:2] / blob_id

    def put(self, data: bytes | str, kind: BlobKind, summary: str, created_by: Role) -> BlobRef:
        raw = data.encode() if isinstance(data, str) else data
        blob_id = hashlib.sha256(raw).hexdigest()
        if (existing := self._lookup(blob_id)) is not None:
            return existing
        try:
            tokens = estimate_tokens(raw.decode("utf-8"))
        except UnicodeDecodeError:
            tokens = 0
        ref = BlobRef(blob_id=blob_id, kind=kind, summary=summary, est_tokens=tokens)
        path = self._path(blob_id)
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, path)
        with closing(sqlite3.connect(self._index)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO blobs VALUES (?, ?, ?, ?, ?, ?)",
                (blob_id, kind, summary, tokens, created_by, datetime.now(UTC).isoformat()),
            )
        return ref

    def get(self, ref: BlobRef | str) -> bytes:
        blob_id = ref.blob_id if isinstance(ref, BlobRef) else ref
        try:
            raw = self._path(blob_id).read_bytes()
        except FileNotFoundError as e:
            raise BlobNotFound(blob_id) from e
        if hashlib.sha256(raw).hexdigest() != blob_id:
            raise BlobCorrupted(blob_id)
        return raw

    def get_text(self, ref: BlobRef | str) -> str:
        return self.get(ref).decode("utf-8")

    def get_slice(self, ref: BlobRef | str, max_tokens: int) -> str:
        """The text, cut to roughly `max_tokens` (len/4) with a marker when truncated."""
        text = self.get_text(ref)
        total = estimate_tokens(text)
        if total <= max_tokens:
            return text
        return (
            text[: max_tokens * 4] + f"\n[... truncated: showing ~{max_tokens} of ~{total} tokens]"
        )

    def ref(self, blob_id: str) -> BlobRef:
        if (found := self._lookup(blob_id)) is None:
            raise BlobNotFound(blob_id)
        return found

    def count(self) -> int:
        with closing(sqlite3.connect(self._index)) as db:
            return db.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]

    def _lookup(self, blob_id: str) -> BlobRef | None:
        with closing(sqlite3.connect(self._index)) as db:
            row = db.execute(
                "SELECT kind, summary, est_tokens FROM blobs WHERE blob_id = ?", (blob_id,)
            ).fetchone()
        if row is None:
            return None
        return BlobRef(blob_id=blob_id, kind=row[0], summary=row[1], est_tokens=row[2])
