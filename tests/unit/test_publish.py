"""Phase 8: collecting a run's results, and the LaTeX helpers (pure, no LLM, no sandbox)."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from samples import math_spec, paper_card, research_brief, result_digest, roadmap

from r2r.contracts import BaselineCard, PaperSection, Roadmap
from r2r.publish.collect import collect_from_states
from r2r.publish.latex import (
    BibEntry,
    bib_entries,
    bibliography,
    check_cites,
    escape,
    render_paper,
    unverified_numbers,
)
from r2r.settings import load_settings

REPO = Path(__file__).resolve().parents[2]


def _done(rm: Roadmap, *ids: str) -> Roadmap:
    ms = [m.model_copy(update={"status": "done"}) if m.id in ids else m for m in rm.milestones]
    return rm.model_copy(update={"milestones": ms})


def _digest(mid: str, runtime: float):
    return result_digest().model_copy(
        update={"milestone_id": mid, "metrics": {"runtime_s": runtime}}
    )


def test_config_and_contract() -> None:
    p = load_settings(REPO / "config", None).budgets.publish
    assert p.modes == ["paper", "package"] and p.fix_attempts == 1 and p.log_tail_lines == 40
    s = PaperSection(created_by="writer", run_id="r", name="method", latex="x", cites=["a"])
    assert s.name == "method"
    with pytest.raises(ValidationError):
        PaperSection(created_by="writer", run_id="r", name="appendix", latex="x")  # type: ignore[arg-type]


def test_collect_newest_per_milestone_from_history() -> None:
    rm = roadmap()  # M1, M2
    m1_old = {"roadmap": rm, "digest": _digest("M1", 9.0), "math_spec": math_spec()}
    m1_new = {"roadmap": rm, "digest": _digest("M1", 0.4), "math_spec": math_spec()}
    m2 = {
        "roadmap": _done(rm, "M1"),
        "digest": _digest("M2", 0.2),
        "math_spec": math_spec().model_copy(update={"milestone_id": "M2"}),
        "brief": research_brief(),
    }
    newest_first = [m2, m1_new, m1_old]
    rec = collect_from_states(newest_first)
    assert [m.milestone.id for m in rec.milestones] == ["M1", "M2"]
    assert rec.milestones[0].digest.metrics["runtime_s"] == 0.4
    assert rec.milestones[1].math_spec.milestone_id == "M2"
    assert rec.partial  # M2 is not marked done yet
    assert rec.brief is not None and rec.roadmap.goal == rm.goal


def test_escape() -> None:
    assert escape("50% of $x_1 & #2 {a}") == r"50\% of \$x\_1 \& \#2 \{a\}"


def test_bibliography_from_cards_and_verified_baselines() -> None:
    card = paper_card()
    base = BaselineCard(
        created_by="scout",
        run_id="r",
        milestone_id="M1",
        criterion_id="AC1",
        claim="ATM call",
        value=10.4506,
        source_url="https://www.quantstart.com/articles/bs",
        source_title="Black-Scholes & Python",
        quote="the call price is 10.4506",
        verified=True,
    )
    unverified = base.model_copy(update={"verified": False, "source_url": "https://x.org/y"})
    entries = bib_entries([card], [base, unverified])
    assert [e.key for e in entries] == ["glasserman2003", "web1"]
    tex = bibliography(entries)
    assert r"\bibitem{glasserman2003}" in tex and "Paul Glasserman" in tex
    assert (
        r"Black-Scholes \& Python" in tex and r"\url{https://www.quantstart.com/articles/bs}" in tex
    )


def test_check_cites() -> None:
    check_cites(r"As in \cite{a,b} and \citep{c}.", {"a", "b", "c"})
    with pytest.raises(ValueError, match="unknown citation keys: zz"):
        check_cites(r"See \cite{a, zz}.", {"a"})


@pytest.mark.parametrize(
    ("text", "unverified"),
    [
        (r"The magnetization is 0.9569 at $T=1.8$.", []),
        (r"The magnetization is 0.957 at $T=1.8$.", []),  # rounding of a source value
        (r"The magnetization is 0.9862 at $T=1.8$.", ["0.9862"]),
        (r"We used 4 standard errors and 3 sizes.", []),  # small integers are prose
        (r"See Section~\ref{sec:2} and \cite{web1}; eq.~(12).", []),
        (r"A speed-up of 37.5x.", ["37.5"]),
    ],
)
def test_unverified_numbers(text: str, unverified: list[str]) -> None:
    sources = ["m_OY = 0.95686", "T = 1.8", "runtime_s 0.04"]
    assert unverified_numbers(text, sources) == unverified


def test_render_paper() -> None:
    tex = render_paper(
        title="Ising & friends",
        authors=["r2r"],
        sections={"abstract": "Abs.", "introduction": "Intro.", "method": "M.", "conclusion": "C."},
        figures=[("figures/binder.png", "Binder cumulants.")],
        bib=bibliography([BibEntry("k", "A. B. Title. 2020.")]),
        partial=True,
    )
    assert r"\documentclass" in tex and r"\usepackage{authblk}" in tex
    assert r"\title{Ising \& friends}" in tex and r"\begin{abstract}" in tex and "Abs." in tex
    assert r"\section{Introduction}" in tex and r"\includegraphics" in tex
    assert "figures/binder.png" in tex and "partial" in tex.lower()
    assert r"\section{Related work}" not in tex  # missing sections are skipped


def test_collect_finds_math_spec_after_result_gate_reset() -> None:
    """Approving a result resets math_spec and baselines but keeps the digest."""
    rm = roadmap()
    during = {"roadmap": rm, "digest": _digest("M1", 0.4), "math_spec": math_spec()}
    after_reset = {"roadmap": _done(rm, "M1"), "digest": _digest("M1", 0.4), "math_spec": None}
    rec = collect_from_states([after_reset, during])
    assert rec.milestones[0].math_spec is not None
