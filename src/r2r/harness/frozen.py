"""Hashing and verification of tests/frozen/ and checks/ (Phase 4).

`freeze()` records a sha256 per file plus one digest over the sorted paths and contents, in
runs/<id>/frozen.json, outside the workspace. `verify()` runs before every test run.
"""

from __future__ import annotations

import hashlib
import json
import stat

from r2r.harness.workspace import FROZEN_DIRS, Workspace


class NotFrozen(RuntimeError):
    """Tests were run before `freeze()` without `require_frozen=False`."""


class FrozenTamper(RuntimeError):
    """Frozen tests or checks changed since `freeze()`: routes to a human gate."""

    def __init__(self, changed: list[str], added: list[str], removed: list[str]) -> None:
        self.changed, self.added, self.removed = changed, added, removed
        parts = [
            f"{k}: {', '.join(v)}"
            for k, v in (("changed", changed), ("added", added), ("removed", removed))
            if v
        ]
        super().__init__("frozen files modified (" + "; ".join(parts) + ")")


def manifest(ws: Workspace) -> dict[str, str]:
    files: dict[str, str] = {}
    for d in FROZEN_DIRS:
        for p in sorted((ws.root / d).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                rel = p.relative_to(ws.root).as_posix()
                files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return files


def digest(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(f"{path}\0{files[path]}\n".encode())
    return h.hexdigest()


def freeze(ws: Workspace) -> str:
    files = manifest(ws)
    d = digest(files)
    ws.frozen_manifest.write_text(json.dumps({"digest": d, "files": files}, indent=2))
    for rel in files:
        p = ws.root / rel
        p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    return d


def is_frozen(ws: Workspace) -> bool:
    return ws.frozen_manifest.is_file()


def verify(ws: Workspace) -> None:
    if not is_frozen(ws):
        raise NotFrozen("tests are not frozen yet")
    expected: dict[str, str] = json.loads(ws.frozen_manifest.read_text())["files"]
    actual = manifest(ws)
    changed = sorted(p for p in expected.keys() & actual.keys() if expected[p] != actual[p])
    added = sorted(actual.keys() - expected.keys())
    removed = sorted(expected.keys() - actual.keys())
    if changed or added or removed:
        raise FrozenTamper(changed, added, removed)
