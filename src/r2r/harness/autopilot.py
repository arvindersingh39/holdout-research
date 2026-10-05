"""Phase 7d autopilot (D76): routine gate decisions in plain Python, never an LLM.

`decide` returns a decision for gates the rules cover, or None to ask the human. Coder, benchmark
and budget gates always go to the human: the code is stuck, the owner must run the benchmarks, or
money is involved. Every automatic decision says why in its note.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from r2r.contracts import HumanDecision, OversightFlag, VerificationReport
from r2r.settings import AutopilotConfig

ALWAYS_HUMAN = {"coder", "benchmark", "budget"}
VERIFIED_OK = {"verified", "none", "unchecked_only"}
NOTE_CHARS = 950


@dataclass(frozen=True)
class Facts:
    run_id: str
    milestone_id: str | None
    flags: list[OversightFlag] = field(default_factory=list)
    tests_passed: bool | None = None  # the stub check (D93)
    verification: VerificationReport | None = None  # the benchmark comparison
    first_roadmap: bool = False
    has_benchmarks: bool = False  # the milestone has values to reproduce


def decide(gate: str, f: Facts, cfg: AutopilotConfig) -> HumanDecision | None:
    serious = [x.text for x in f.flags if x.severity == "high"]
    if gate in ALWAYS_HUMAN:
        return None
    if gate == "roadmap":
        if serious or (f.first_roadmap and cfg.roadmap_first == "human"):
            return None
        return _make(f, "approve", "no serious overseer flags on the roadmap")
    if gate == "result":
        ver = f.verification.status if f.verification is not None else "none"
        if f.has_benchmarks and ver != "verified":
            return None  # D84/D93: values to reproduce that were not all reproduced
        if f.tests_passed and ver in VERIFIED_OK and not serious:
            what = f"benchmarks {ver}" if f.has_benchmarks else "no benchmarks"
            return _make(f, "approve", f"code check passes, {what}, no serious flags")
        return None
    if gate == "publication" and cfg.publication == "auto":
        return _make(f, "approve", "publication set to auto")
    return None


def _make(f: Facts, action: str, why: str) -> HumanDecision:
    return HumanDecision(
        created_by="harness",
        run_id=f.run_id,
        milestone_id=f.milestone_id,
        action=action,  # type: ignore[arg-type]
        note=f"autopilot: {why}"[:NOTE_CHARS],
    )
