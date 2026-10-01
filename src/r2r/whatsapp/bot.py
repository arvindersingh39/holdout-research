"""The bot behind `r2r bot`: turns owner taps into stored decisions and starts `r2r resume`.

It never runs the pipeline itself and never relays free text to an agent (Meta's terms bar
general-purpose AI assistants); the only free text it accepts is a redirect note.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from r2r.contracts import HumanDecision
from r2r.log import get_logger
from r2r.whatsapp.channel import Notifier
from r2r.whatsapp.client import GraphClient, WhatsAppError
from r2r.whatsapp.messages import SHOW_ALL, Inbound, parse_button_id, parse_webhook
from r2r.whatsapp.store import GateStore

log = get_logger("r2r.whatsapp.bot")
NOTE_CHARS = 1000
HELP = (
    "Use the buttons on a gate: Approve, Redirect or Stop. Type 'show' to see waiting gates, "
    "or 'edit' to leave a gate for your desk."
)


class Bot:
    def __init__(
        self,
        store: GateStore,
        client: GraphClient,
        owner: str,
        gates: dict[str, Any],
        spawn_resume: Callable[[str], None],
        now: Callable[[], float] = time.time,
    ) -> None:
        self.store, self.client, self.owner, self.gates = store, client, owner, gates
        self.spawn_resume, self.now = spawn_resume, now
        self.notifier = Notifier(store, client, owner, gates, now)

    def handle(self, payload: dict[str, Any]) -> None:
        for msg in parse_webhook(payload):
            if msg.sender != self.owner:
                log.info("ignored message from a non-owner number")
                continue
            if not self.store.first_seen(msg.message_id, self.now()):
                continue
            self.store.touch_inbound(self.now())
            try:
                self._handle(msg)
            except WhatsAppError as e:
                log.warning("reply failed", extra={"error": str(e)[:200]})

    def _say(self, text: str) -> None:
        self.client.send_text(self.owner, text)

    def _handle(self, msg: Inbound) -> None:
        if msg.kind == "button_reply":
            return self._tap(msg.value)
        if msg.kind == "template_button":
            return self._show(msg.value)
        if msg.kind != "text":
            return self._say(HELP)
        text = msg.value.strip()
        waiting = self.store.awaiting_note()
        if waiting is not None:
            self._decide(waiting.gate_id, waiting.run_id, "redirect", text[:NOTE_CHARS])
            return self._say(f"Redirect noted for run {waiting.run_id}. Resuming.")
        if text.lower() == "edit":
            pending = self.store.pending()
            if not pending:
                return self._say("No gate is waiting.")
            g = pending[-1]
            return self._say(
                f"Left open. At your desk run: r2r resume {g.run_id} --channel terminal"
            )
        if text.lower() in ("show", "gates"):
            return self._show(SHOW_ALL)
        self._say(HELP)

    def _show(self, payload: str) -> None:
        pending = self.store.pending()
        if payload != SHOW_ALL:
            gate_id = payload.removeprefix("show:")
            pending = [g for g in pending if g.gate_id == gate_id]
        if not pending:
            return self._say("No gate is waiting.")
        for g in pending:
            self.notifier.show(g)

    def _tap(self, value: str) -> None:
        parsed = parse_button_id(value)
        g = self.store.get(parsed[0]) if parsed else None
        if not parsed or g is None or g.status != "pending" or g.nonce != parsed[2]:
            return self._say("That gate is no longer open.")
        action = parsed[1]
        if action == "redirect":
            self.store.set_awaiting_note(g.gate_id)
            return self._say("Send your note for the long-term planner as your next message.")
        self._decide(g.gate_id, g.run_id, action, "")
        verb = "Approved" if action == "approve" else "Stopped"
        self._say(f"{verb}: {g.request.title}. Run {g.run_id} continues on your laptop.")

    def _decide(self, gate_id: str, run_id: str, action: str, note: str) -> None:
        self.store.decide(
            gate_id, HumanDecision(created_by="human", run_id=run_id, action=action, note=note)
        )
        self.spawn_resume(run_id)

    def remind(self) -> None:
        """Re-ping gates still waiting after `remind_after_h`, at most `max_reminders` times."""
        due_after = float(self.gates.get("remind_after_h", 24)) * 3600
        limit = int(self.gates.get("max_reminders", 3))
        due = [
            g
            for g in self.store.pending()
            if g.last_ping_at is not None
            and self.now() - g.last_ping_at >= due_after
            and g.reminders < limit
        ]
        self.notifier.ping(due, reminder=True)
