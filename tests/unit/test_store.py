"""Run directories, the content-addressed blob store and the cost ledger."""

import hashlib
from pathlib import Path

import pytest

from r2r.store.blobs import BlobCorrupted, BlobNotFound, BlobStore
from r2r.store.ledger import CallRecord, Ledger
from r2r.store.runs import ensure_runs_root, run_paths

# --- runs ---------------------------------------------------------------------------


def test_runs_root_is_symlink_to_nosync(tmp_path: Path) -> None:
    root = ensure_runs_root(tmp_path)
    link = tmp_path / "runs"
    assert link.is_symlink()
    assert link.resolve() == (tmp_path / "runs.nosync").resolve()
    assert root == link
    assert ensure_runs_root(tmp_path) == link  # idempotent


def test_existing_runs_dir_is_kept(tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir()
    assert ensure_runs_root(tmp_path) == tmp_path / "runs"
    assert not (tmp_path / "runs").is_symlink()


def test_run_paths_layout(tmp_path: Path) -> None:
    p = run_paths("run-1", base=tmp_path, create=True)
    assert p.root == tmp_path / "runs" / "run-1"
    for d in (p.workspace, p.blobs, p.vectors):
        assert d.is_dir()
    assert p.ledger.name == "ledger.sqlite"
    assert p.checkpoints.name == "checkpoints.sqlite"


def test_run_id_must_be_safe(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run_paths("../escape", base=tmp_path)


# --- blobs --------------------------------------------------------------------------


@pytest.fixture
def blobs(tmp_path: Path) -> BlobStore:
    return BlobStore(tmp_path / "blobs")


def test_blob_round_trip(blobs: BlobStore) -> None:
    ref = blobs.put("hello world", kind="paper_text", summary="greeting", created_by="cli")
    assert ref.blob_id == hashlib.sha256(b"hello world").hexdigest()
    assert ref.kind == "paper_text"
    assert ref.est_tokens == 3
    assert blobs.get(ref) == b"hello world"
    assert blobs.get_text(ref) == "hello world"


def test_identical_content_stored_once(blobs: BlobStore, tmp_path: Path) -> None:
    a = blobs.put(b"same", kind="code", summary="first", created_by="coder")
    b = blobs.put(b"same", kind="code", summary="second", created_by="coder")
    assert a == b  # first summary wins
    files = [f for f in (tmp_path / "blobs").rglob("*") if f.is_file() and f.suffix != ".sqlite"]
    assert len(files) == 1
    assert blobs.count() == 1


def test_binary_blob_has_zero_tokens(blobs: BlobStore) -> None:
    ref = blobs.put(b"\x89PNG\x00\xff", kind="figure", summary="plot", created_by="writer")
    assert ref.est_tokens == 0


def test_get_slice_truncates(blobs: BlobStore) -> None:
    text = "word " * 1000  # ~1250 tokens by len/4
    ref = blobs.put(text, kind="paper_text", summary="long", created_by="retriever")
    assert blobs.get_slice(ref, max_tokens=5000) == text
    part = blobs.get_slice(ref, max_tokens=100)
    assert len(part) < 500
    assert part.startswith("word word")
    assert "truncated" in part


def test_missing_and_corrupt_blobs(blobs: BlobStore, tmp_path: Path) -> None:
    ref = blobs.put("data", kind="log", summary="log", created_by="harness")
    missing = ref.model_copy(update={"blob_id": "f" * 64})
    with pytest.raises(BlobNotFound):
        blobs.get(missing)
    path = next(f for f in (tmp_path / "blobs").rglob(ref.blob_id))
    path.write_bytes(b"tampered")
    with pytest.raises(BlobCorrupted):
        blobs.get(ref)


def test_index_survives_reopen(blobs: BlobStore, tmp_path: Path) -> None:
    ref = blobs.put("persist", kind="other", summary="p", created_by="cli")
    again = BlobStore(tmp_path / "blobs")
    assert again.ref(ref.blob_id) == ref


# --- ledger -------------------------------------------------------------------------


def _rec(**kw) -> CallRecord:
    base = dict(
        run_id="r1",
        milestone_id="M1",
        role="coder",
        model="anthropic/x",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
        latency_ms=900,
        ok=True,
    )
    return CallRecord(**{**base, **kw})


def test_ledger_sums(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "ledger.sqlite")
    led.record(_rec())
    led.record(_rec(cost_usd=0.02))
    led.record(_rec(milestone_id="M2", cost_usd=0.5))
    led.record(_rec(run_id="other", cost_usd=9.0))
    led.record(_rec(milestone_id=None, role="long_planner", cost_usd=0.1))
    assert led.spent("r1") == pytest.approx(0.63)
    assert led.spent("r1", "M1") == pytest.approx(0.03)
    assert led.spent("r1", "M2") == pytest.approx(0.5)
    assert led.spent("nobody") == 0.0


def test_ledger_by_role_and_rows(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "ledger.sqlite")
    led.record(_rec(cache_read_tokens=80))
    led.record(_rec(ok=False, error="RateLimitError", cost_usd=0.0))
    led.record(_rec(role="writer", cost_usd=0.2))
    roles = led.by_role("r1")
    assert roles["coder"].calls == 2
    assert roles["coder"].cost_usd == pytest.approx(0.01)
    assert roles["coder"].cache_read_tokens == 80
    assert roles["writer"].cost_usd == pytest.approx(0.2)
    rows = led.rows("r1")
    assert len(rows) == 3
    assert rows[1].ok is False and rows[1].error == "RateLimitError"
    assert rows[0].ts  # timestamp filled in


def test_ledger_persists(tmp_path: Path) -> None:
    Ledger(tmp_path / "l.sqlite").record(_rec())
    assert Ledger(tmp_path / "l.sqlite").spent("r1") == pytest.approx(0.01)
