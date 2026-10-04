"""Leak 2: the short planner is told the field limits, so tightening never overflows them."""

from pathlib import Path

PROMPT = (Path(__file__).resolve().parents[2] / "prompts" / "short_planner.md").read_text()


def test_short_planner_prompt_states_field_limits() -> None:
    for limit in ("300", "800", "400", "600"):
        assert limit in PROMPT
    assert "more precise, not longer" in PROMPT
