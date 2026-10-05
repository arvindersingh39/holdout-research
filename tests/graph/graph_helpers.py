"""Graph test helpers: canned contracts, a scripted gate channel and run setup."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "unit"))

from samples import coordinator_verdict, milestone_spec, research_brief, roadmap

from r2r.contracts import (
    CoordinatorVerdict,
    FileSpec,
    HumanDecision,
    Issue,
    MathDesign,
    MathSpec,
)
from r2r.graph.deps import RunDeps
from r2r.graph.gates import GateRequest
from r2r.harness.sandbox import LocalProcessBackend
from r2r.llm.client import ToolCall
from r2r.llm.fake import FakeLLM, tool_call
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


def make_deps(
    tmp_path: Path,
    fake: FakeLLM,
    run_id: str = "g",
    create: bool = True,
    sandbox_factory=LocalProcessBackend,
    settings=None,
    overseer: bool = False,
    overseer_max_calls: int | None = None,
    publish_modes: tuple[str, ...] = (),
    proposal: Path = PILOT,
    autopilot: bool = False,
    max_fix_rounds: int | None = None,
) -> RunDeps:
    """The overseer (Phase 7c) is off unless a test turns it on: it adds LLM calls to script."""
    settings = settings or load_settings(REPO / "tests" / "fixtures" / "config", env_file=None)
    b = settings.budgets
    ov = b.overseer.model_copy(
        update={
            "enabled": overseer,
            "max_calls_milestone": overseer_max_calls or b.overseer.max_calls_milestone,
        }
    )
    pub = b.publish.model_copy(update={"modes": list(publish_modes)})  # off unless a test asks
    ap = b.autopilot.model_copy(
        update={
            "enabled": autopilot,
            "max_fix_rounds": b.autopilot.max_fix_rounds
            if max_fix_rounds is None
            else max_fix_rounds,
        }
    )
    settings = settings.model_copy(
        update={"budgets": b.model_copy(update={"overseer": ov, "publish": pub, "autopilot": ap})}
    )
    if create:
        return RunDeps.create(
            run_id,
            proposal,
            settings,
            fake,
            runs_base=tmp_path,
            prompts_dir=REPO / "prompts",
            retry_wait_max_s=0,
            sandbox_factory=sandbox_factory,
        )
    return RunDeps.load(
        run_id,
        settings,
        fake,
        runs_base=tmp_path,
        prompts_dir=REPO / "prompts",
        retry_wait_max_s=0,
        sandbox_factory=sandbox_factory,
    )


# --- #4 designs and #5 coder scripts for a tiny task: square(x), one package per milestone ------

STUB = "def square(x: float) -> float:\n    raise NotImplementedError\n"
IMPL = "def square(x: float) -> float:\n    return x * x\n"


def design(n: int, extra: tuple[FileSpec, ...] = ()) -> MathDesign:
    """D93: the formulation and stubs only."""
    pkg = f"sq{n}"
    meta = {"created_by": "math_designer", "run_id": "g", "milestone_id": f"M{n}"}
    spec = MathSpec(
        **meta,
        formulation="f(x) = x^2",
        algorithm="multiply",
        signatures=["square(x: float) -> float"],
    )
    files = [
        FileSpec(path=f"src/{pkg}/__init__.py", purpose="stub", content=""),
        FileSpec(path=f"src/{pkg}/core.py", purpose="stub", content=STUB),
        *extra,
    ]
    return MathDesign(**meta, spec=spec, files=files)


def coder_pass(n: int) -> list[list[ToolCall]]:
    """One coder session that implements square(x) and runs the stub check."""
    return [
        [tool_call("create_file", path=f"src/sq{n}/core.py", content=IMPL)],
        [tool_call("check_code")],
    ]


def reproduce(n: int, values: dict[str, float]) -> list[list[ToolCall]]:
    """The coder writes the milestone's reproduce script (D93) reporting `values`."""
    entries = [{"ref": r, "generated": v} for r, v in values.items()]
    body = (
        "import json, pathlib\n\n\ndef main():\n"
        f"    out = pathlib.Path('results/benchmarks/m{n}.json')\n"
        "    out.parent.mkdir(parents=True, exist_ok=True)\n"
        f"    out.write_text(json.dumps({{'entries': {entries!r}}}))\n\n\n"
        "if __name__ == '__main__':\n    main()\n"
    )
    return [[tool_call("create_file", path=f"bench/reproduce_m{n}.py", content=body)]]


def script_milestone(fake: FakeLLM, n: int, verdict: CoordinatorVerdict = PASS) -> None:
    fake.script("short_planner", SPEC)
    fake.script("coordinator", verdict)
    fake.script("math_designer", design(n))
    fake.script("coder", *coder_pass(n))
