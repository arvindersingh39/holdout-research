"""Compact Markdown renderings used by gates and `r2r show`."""

from samples import coordinator_verdict, milestone_spec, research_brief, result_digest, roadmap

from r2r.graph.render import render


def test_render_brief() -> None:
    md = render(research_brief())
    assert md.startswith("## Research brief")
    assert research_brief().objective in md
    assert "SE slope within 0.1 of -0.5" in md


def test_render_roadmap() -> None:
    md = render(roadmap())
    assert "Roadmap v1" in md
    assert "M1" in md and "M2" in md
    assert "depends on M1" in md
    assert "AC1" in md
    assert "▶" in md  # current milestone marker


def test_render_spec_and_verdict() -> None:
    md = render(milestone_spec())
    assert "Milestone spec · M1" in md
    assert "bs_call(" in md
    vmd = render(coordinator_verdict())
    assert "REVISE" in vmd and "Units missing for sigma." in vmd


def test_render_digest() -> None:
    md = render(result_digest())
    assert "met" in md and "AC1" in md and "runtime_s" in md


def test_render_is_compact() -> None:
    for c in (research_brief(), roadmap(), milestone_spec(), coordinator_verdict()):
        assert len(render(c)) < 4000


def test_roadmap_renders_as_nested_list_in_rich() -> None:
    import io

    from rich.console import Console
    from rich.markdown import Markdown

    out = io.StringIO()
    Console(file=out, width=100).print(Markdown(render(roadmap())))
    lines = [ln.rstrip() for ln in out.getvalue().splitlines()]
    m1 = next(i for i, ln in enumerate(lines) if "M1 · milestone 1" in ln)
    m2 = next(i for i, ln in enumerate(lines) if "M2 · milestone 2" in ln)
    assert m2 > m1  # milestones on separate lines, not one paragraph
    assert any("AC1: criterion 1" in ln for ln in lines[m1 + 1 : m2])
