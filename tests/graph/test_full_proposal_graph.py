"""study-2: planners saw only retrieved excerpts of a short proposal and kept claiming its equations
were "missing". A proposal under `context_pack.full_proposal_tokens` now goes in full to the long
planner, short planner and math designer (as data, inside <document> tags)."""

import json
from pathlib import Path

from graph_helpers import PILOT, ROADMAP, FakeChannel, decide, make_deps, script_milestone

from r2r.graph.driver import drive, open_graph
from r2r.llm.fake import FakeLLM

LAST_LINE = (PILOT / "proposal.md").read_text().strip().splitlines()[-1].strip("- ").strip()
ROLES = ("long_planner", "short_planner", "math_designer")


def _payload(fake: FakeLLM, role: str) -> dict:
    content = next(c for c in fake.calls if c.role == role).messages[1]["content"]
    return json.loads(content.split("<data>")[1].split("</data>")[0])


def _run(tmp_path: Path, fake: FakeLLM, cap: int | None = None):
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    deps = make_deps(tmp_path, fake)
    if cap is not None:
        b = deps.settings.budgets
        cp = b.context_pack.model_copy(update={"full_proposal_tokens": cap})
        deps.settings = deps.settings.model_copy(
            update={"budgets": b.model_copy(update={"context_pack": cp})}
        )
    with open_graph(deps) as graph:
        drive(
            graph,
            deps.run_id,
            FakeChannel(decide("approve"), decide("approve"), decide("stop")),
            start=True,
        )


def test_short_proposal_reaches_planners_and_designer_in_full(
    tmp_path: Path, fake: FakeLLM
) -> None:
    _run(tmp_path, fake)
    for role in ROLES:
        text = _payload(fake, role)["proposal"]
        assert text.startswith("<document") and LAST_LINE in text, role


def test_long_proposal_falls_back_to_excerpts(tmp_path: Path, fake: FakeLLM) -> None:
    _run(tmp_path, fake, cap=10)
    for role in ROLES:
        assert _payload(fake, role)["proposal"] == "", role
