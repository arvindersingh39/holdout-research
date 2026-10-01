"""WhatsAppChannel: the GateChannel used by `r2r run`/`resume` when gates go to WhatsApp (D48).

`ask()` returns a decision the bot stored for this gate, or sends the gate (interactive inside
the 24-hour window, the approved template outside it) and returns None, leaving it pending.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from r2r.contracts import HumanDecision
from r2r.graph.gates import GateRequest
from r2r.whatsapp.client import GraphClient
from r2r.whatsapp.messages import ACTIONS, SHOW_ALL, TITLES, button_id, summary
from r2r.whatsapp.store import Gate, GateStore

WINDOW_S = 24 * 3600


class Notifier:
    """Shared by the channel and the bot: how a gate is put in front of the owner."""

    def __init__(
        self,
        store: GateStore,
        client: GraphClient,
        owner: str,
        gates: dict[str, Any],
        now: Callable[[], float] = time.time,
    ) -> None:
        self.store, self.client, self.owner, self.gates, self.now = store, client, owner, gates, now

    def window_open(self) -> bool:
        last = self.store.last_inbound()
        return last is not None and self.now() - last < WINDOW_S

    def show(self, g: Gate) -> None:
        """Free-form gate: the full contract as a document (unless summary_only) plus buttons."""
        attach = self.gates.get("full_text", "attach") == "attach"
        if attach:
            media = self.client.upload_document(g.request.markdown.encode(), f"{g.gate_id}.txt")
            self.client.send_document(
                self.owner, media, f"r2r-{g.run_id}-{g.request.gate}.txt", caption=g.request.title
            )
        buttons = [(button_id(g.gate_id, a, g.nonce), TITLES[a]) for a in ACTIONS]
        self.client.send_buttons(
            self.owner,
            summary(g.request, g.run_id, attach),
            buttons,
            footer="Type 'edit' to edit at your desk",
        )
        self.store.mark_pinged(g.gate_id, self.now(), shown=True)

    def ping(self, gates: list[Gate], reminder: bool = False) -> None:
        if not gates:
            return
        if self.window_open():
            for g in gates:
                self.show(g)
                if reminder:
                    self.store.mark_pinged(g.gate_id, self.now(), reminder=True)
            return
        batch = self.gates.get("batch_pending_gates", True)
        groups = [gates] if batch else [[g] for g in gates]
        for group in groups:
            first = group[0]
            run = first.run_id if len(group) == 1 else f"{len(group)} gates"
            kind = first.request.gate if len(group) == 1 else "several"
            payload = SHOW_ALL if batch else f"show:{first.gate_id}"
            self.client.send_template(
                self.owner,
                self.gates.get("gate_template", "r2r_gate_waiting"),
                self.gates.get("template_lang", "en_US"),
                [run, kind],
                payload,
            )
            for g in group:
                self.store.mark_pinged(g.gate_id, self.now(), shown=False, reminder=reminder)


class WhatsAppChannel:
    def __init__(
        self,
        store: GateStore,
        client: GraphClient,
        owner: str,
        gates: dict[str, Any],
        now: Callable[[], float] = time.time,
    ) -> None:
        self.store, self.now = store, now
        self.notifier = Notifier(store, client, owner, gates, now)

    def ask(self, req: GateRequest, run_id: str) -> HumanDecision | None:
        g = self.store.open_gate(run_id, req, self.now())
        if g.status == "decided":
            return self.store.consume(g.gate_id)
        if g.last_ping_at is None:
            waiting = [
                p
                for p in self.store.pending()
                if p.gate_id != g.gate_id and p.last_ping_at is not None and not p.shown
            ]
            if (
                waiting
                and not self.notifier.window_open()
                and self.notifier.gates.get("batch_pending_gates", True)
            ):
                self.store.mark_pinged(g.gate_id, self.now(), shown=False)  # covered by that ping
            else:
                self.notifier.ping([g])
        return None
