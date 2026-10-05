"""Human gates: interrupt payloads and the channels that deliver them (terminal now, WhatsApp in
Phase 7). A channel shows a gate and returns a `HumanDecision`, or None to leave it pending."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from typing import Protocol

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

import r2r.contracts as contracts
from r2r.contracts import HumanDecision
from r2r.graph.render import render
from r2r.graph.routing import GateKind

NOTE_CHARS = 1000


class GateRequest(BaseModel):
    """What a gate node passes to `interrupt()`; JSON-serializable."""

    model_config = ConfigDict(extra="forbid")

    gate: GateKind
    title: str
    markdown: str
    contract_type: str | None = None  # set when the contract can be edited
    contract_json: str | None = None
    path_prompt: str | None = None  # D93: approve may carry a file path in the note


def gate_request(
    gate: GateKind, title: str, contract: BaseModel, editable: bool = True
) -> GateRequest:
    return GateRequest(
        gate=gate,
        title=title,
        markdown=render(contract),
        contract_type=type(contract).__name__ if editable else None,
        contract_json=contract.model_dump_json() if editable else None,
    )


class GateChannel(Protocol):
    def ask(self, req: GateRequest, run_id: str) -> HumanDecision | None: ...


def _system_editor(text: str) -> str:
    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile("w+", suffix=".yaml", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        subprocess.run([*editor.split(), path], check=False)
        with open(path) as f:
            return f.read()
    finally:
        os.unlink(path)


class TerminalChannel:
    def __init__(
        self,
        console: Console | None = None,
        input_fn: Callable[[str], str] = input,
        edit_fn: Callable[[str], str] = _system_editor,
    ) -> None:
        self.console = console or Console()
        self.input = input_fn
        self.edit = edit_fn

    def ask(self, req: GateRequest, run_id: str) -> HumanDecision | None:
        c = self.console
        c.print(
            Panel(Markdown(req.markdown), title=f"Gate · {req.title}", subtitle=f"run {run_id}")
        )
        options = (
            "[a]pprove"
            + (" / [e]dit" if req.contract_type else "")
            + " / [r]edirect / [s]top / [q]uit for now"
        )
        while True:
            key = self.input(f"{options}: ").strip().lower()[:1]
            if key == "a":
                path = self.input(req.path_prompt).strip()[:NOTE_CHARS] if req.path_prompt else ""
                return self._decision(run_id, "approve", note=path)
            if key == "q":
                c.print("Gate left pending. Continue later with `r2r resume " + run_id + "`.")
                return None
            if key == "s":
                note = self.input("Reason (optional): ").strip()[:NOTE_CHARS]
                return self._decision(run_id, "stop", note=note)
            if key == "r":
                note = self.input("Note for the long-term planner: ").strip()[:NOTE_CHARS]
                if note:
                    return self._decision(run_id, "redirect", note=note)
                c.print("A redirect needs a note.")
                continue
            if key == "e" and req.contract_type:
                edited = self._edit(req)
                if edited is not None:
                    note = self.input("Note (optional): ").strip()[:NOTE_CHARS]
                    return self._decision(run_id, "edit", note=note, edited=edited)
                continue
            c.print(f"Choose one of: {options}")

    def _edit(self, req: GateRequest) -> BaseModel | None:
        cls = getattr(contracts, req.contract_type or "")
        data = cls.model_validate_json(req.contract_json).model_dump(mode="json")
        text = self.edit(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        try:
            return cls.model_validate(yaml.safe_load(text))
        except (ValidationError, yaml.YAMLError, contracts.ContractTooLarge) as e:
            self.console.print(f"[red]Invalid edit, nothing changed:[/] {str(e)[:800]}")
            return None

    @staticmethod
    def _decision(
        run_id: str, action: str, note: str = "", edited: BaseModel | None = None
    ) -> HumanDecision:
        return HumanDecision(
            created_by="human", run_id=run_id, action=action, note=note, edited=edited
        )
