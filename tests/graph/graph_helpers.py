"""Graph test helpers: canned contracts, a scripted gate channel and run setup."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "unit"))

from samples import coordinator_verdict, milestone_spec, research_brief, roadmap

from r2r.contracts import (
    CoordinatorVerdict,
    HumanDecision,
    Issue,
)
from r2r.graph.deps import RunDeps
from r2r.graph.gates import GateRequest
from r2r.llm.fake import FakeLLM
from r2r.settings import load_settings

REPO = Path(__file__).resolve().parents[2]
PILOT = REPO / "examples" / "pilot_mc_pricer"
META = {"created_by", "run_id", "milestone_id"}

BRIEF = research_brief().model_dump_json(exclude=META | {"paper_cards"})
ROADMAP = roadmap()
SPEC = milestone_spec().model_dump_json(exclude=META | {"context_refs"})
PASS = CoordinatorVerdict.model_validate(
    {**coordinator_verdict().model_dump(), "verdict": "pass", "issues": []}
)
REVISE = coordinator_verdict()
REVISE_ROADMAP = CoordinatorVerdict.model_validate(
    {
        **REVISE.model_dump(),
        "issues": [
            Issue(target="roadmap", severity="high", text="R2: M1 is too big.").model_dump()
        ],
    }
)


class FakeChannel:
    """Returns scripted decisions (None = quit for now) and records every gate shown."""

    def __init__(self, *decisions: HumanDecision | None) -> None:
        self.decisions = list(decisions)
        self.requests: list[GateRequest] = []

    def ask(self, req: GateRequest, run_id: str) -> HumanDecision | None:
        self.requests.append(req)
        if not self.decisions:
            raise AssertionError(f"no scripted decision for gate {req.gate!r}")
        return self.decisions.pop(0)

    @property
    def gates(self) -> list[str]:
        return [r.gate for r in self.requests]


def decide(action: str, **kw) -> HumanDecision:
    return HumanDecision(created_by="human", run_id="g", action=action, **kw)


def make_deps(tmp_path: Path, fake: FakeLLM, run_id: str = "g", create: bool = True) -> RunDeps:
    settings = load_settings(REPO / "config", env_file=None)
    if create:
        return RunDeps.create(
            run_id,
            PILOT,
            settings,
            fake,
            runs_base=tmp_path,
            prompts_dir=REPO / "prompts",
            retry_wait_max_s=0,
        )
    return RunDeps.load(
        run_id, settings, fake, runs_base=tmp_path, prompts_dir=REPO / "prompts", retry_wait_max_s=0
    )
