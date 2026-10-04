"""Phase 7d autopilot (D76): routine gate decisions in plain Python, never an LLM.

`decide` returns a decision for gates the rules cover, or None to ask the human. Coder, dispute,
tamper, escalation and budget gates always go to the human: they mean something is stuck or
suspicious. Every automatic decision says why in its note.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from r2r.contracts import HumanDecision, OversightFlag, VerificationReport
from r2r.settings import AutopilotConfig

ALWAYS_HUMAN = {"escalation", "coder", "dispute", "tamper", "budget"}
VERIFIED_OK = {"verified", "none", "unchecked_only"}
NOTE_CHARS = 950


@dataclass(frozen=True)
class Facts:
    run_id: str
    milestone_id: str | None
    flags: list[OversightFlag] = field(default_factory=list)
    redgreen_ok: bool = True
    tests_passed: bool | None = None
    verification: VerificationReport | None = None
    auto_redirects: int = 0  # automatic redirects already made for this milestone
    first_roadmap: bool = False
    design_feedback: str = ""
    cites_data: bool = False  # the milestone's criteria refer to the owner's recorded data


def decide(gate: str, f: Facts, cfg: AutopilotConfig) -> HumanDecision | None:
    serious = [x.text for x in f.flags if x.severity == "high"]
    if gate in ALWAYS_HUMAN:
        return None
    if gate == "roadmap":
        if serious or (f.first_roadmap and cfg.roadmap_first == "human"):
            return None
        return _make(f, "approve", "no serious overseer flags on the roadmap")
    if gate == "tests":
        if not serious:
            return _make(f, "approve", "tests have no serious overseer flags")
        return _redirect(f, cfg, serious)
    if gate == "design":  # reached only when the design failed twice
        return _redirect(f, cfg, [x for x in (f.design_feedback, *serious) if x])
    if gate == "result":
        ver = f.verification.status if f.verification is not None else "none"
        if f.cites_data and ver in ("none", "unchecked_only"):
            return None  # D84: nothing was compared with the owner's values; the owner decides
        if f.tests_passed and ver in VERIFIED_OK and not serious:
            return _make(f, "approve", f"tests pass, verification {ver}, no serious flags")
        return None
    if gate == "publication" and cfg.publication == "auto":
        return _make(f, "approve", "publication set to auto")
    return None


def _redirect(f: Facts, cfg: AutopilotConfig, reasons: list[str]) -> HumanDecision | None:
    if f.auto_redirects >= cfg.max_auto_redirects:
        return None  # the same problem keeps coming back: a human should look
    return _make(f, "redirect", "fix these, then resubmit: " + " | ".join(reasons))


def _make(f: Facts, action: str, why: str) -> HumanDecision:
    return HumanDecision(
        created_by="harness",
        run_id=f.run_id,
        milestone_id=f.milestone_id,
        action=action,  # type: ignore[arg-type]
        note=f"autopilot: {why}"[:NOTE_CHARS],
    )
