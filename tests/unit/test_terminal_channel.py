"""The terminal gate channel with scripted keystrokes and a scripted editor."""

import io
from collections.abc import Iterator

import yaml
from rich.console import Console
from samples import roadmap

from r2r.graph.gates import GateRequest, TerminalChannel, gate_request


def _channel(
    keys: list[str], edits: list[str] | None = None
) -> tuple[TerminalChannel, io.StringIO]:
    out = io.StringIO()
    key_iter: Iterator[str] = iter(keys)
    edit_iter = iter(edits or [])
    ch = TerminalChannel(
        console=Console(file=out, width=100, force_terminal=False),
        input_fn=lambda prompt: next(key_iter),
        edit_fn=lambda text: next(edit_iter),
    )
    return ch, out


def _req() -> GateRequest:
    return gate_request("roadmap", "Approve the first roadmap", roadmap())


def test_approve() -> None:
    ch, out = _channel(["a"])
    d = ch.ask(_req(), "run-test")
    assert d.action == "approve" and d.created_by == "human" and d.run_id == "run-test"
    assert "Roadmap v1" in out.getvalue()


def test_redirect_asks_for_note() -> None:
    ch, _ = _channel(["r", "Split M1 into two milestones."])
    d = ch.ask(_req(), "run-test")
    assert d.action == "redirect" and d.note == "Split M1 into two milestones."


def test_stop_and_quit() -> None:
    ch, _ = _channel(["s", ""])
    assert ch.ask(_req(), "run-test").action == "stop"
    ch, _ = _channel(["q"])
    assert ch.ask(_req(), "run-test") is None


def test_unknown_key_reprompts() -> None:
    ch, out = _channel(["x", "a"])
    assert ch.ask(_req(), "run-test").action == "approve"
    assert "Choose" in out.getvalue()


def _edited(goal: str) -> str:
    data = roadmap().model_dump(mode="json")
    data["goal"] = goal
    return yaml.safe_dump(data, sort_keys=False)


def test_edit_validates_and_returns_contract() -> None:
    ch, _ = _channel(["e", ""], [_edited("Price options and Asians too.")])
    d = ch.ask(_req(), "run-test")
    assert d.action == "edit"
    assert d.edited is not None and d.edited.goal == "Price options and Asians too."


def test_invalid_edit_reprompts() -> None:
    bad = _edited("")  # empty goal violates min_length
    ch, out = _channel(["e", "a"], [bad])
    d = ch.ask(_req(), "run-test")
    assert d.action == "approve"
    assert "Invalid edit" in out.getvalue()


def test_edit_not_offered_without_contract() -> None:
    req = GateRequest(gate="result", title="Review M1", markdown="## result")
    ch, out = _channel(["e", "a"])
    assert ch.ask(req, "run-test").action == "approve"
    assert "[e]dit" not in out.getvalue()
