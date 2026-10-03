"""Owner request (2026-10-03): gate text must be short and easy for a graduate student to read.
Gates show the overseer's flags (most serious first, a one-line count, long text shortened, no
evidence codes) and only the names of briefed agents; `r2r show <run> oversight` keeps it all."""

from r2r.contracts import OversightFlag
from r2r.graph.nodes import render_oversight

LONG = "The comparison command prints nothing, so the owner cannot read the table. " * 8


def _state() -> dict:
    return {
        "run_id": "study-3",
        "flags": [
            OversightFlag(severity="info", text="Note: M2 depends on M1.", evidence=["E1"]),
            OversightFlag(severity="high", text=LONG, evidence=["E2", "E3"]),
            OversightFlag(severity="warn", text="AC4 has no test.", evidence=["E4"]),
        ],
        "briefings": {"math_designer": "Detailed advice " * 40, "coder": "More advice."},
    }


def test_gate_view_is_short_and_sorted() -> None:
    md = render_oversight(_state(), flags_shown=6, flag_chars=120)
    lines = md.splitlines()
    assert lines[0].startswith("**Overseer:** 1 serious, 1 warning, 1 note")
    flags = [ln for ln in lines if ln.startswith("- ")]
    assert [f.split(":")[0] for f in flags] == ["- ⚑ serious", "- ⚑ warning", "- note"]
    assert all(len(f) <= 140 for f in flags) and flags[0].endswith("…")
    assert "[E" not in md  # evidence codes are for the agents, not the owner
    assert "Detailed advice" not in md and "More advice" not in md
    assert "Briefed: math designer, coder" in md and "uv run r2r show study-3 oversight" in md


def test_full_view_keeps_everything() -> None:
    md = render_oversight(_state(), flags_shown=1000, flag_chars=120, full=True)
    assert LONG.strip() in md and "[E2, E3]" in md
    assert "Detailed advice" in md and "More advice." in md


def test_nothing_to_show() -> None:
    assert render_oversight({"run_id": "r"}, flags_shown=6, flag_chars=120) == ""
