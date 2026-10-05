"""Gates answered from Telegram (D91): the bot sends the gate with buttons and long-polls for the
owner's answer. Only the configured chat counts; a random nonce ties buttons to this gate. Only
structured decisions (a button, plus a note for redirect or stop) are accepted."""

from __future__ import annotations

import secrets
from collections.abc import Callable

from r2r.contracts import HumanDecision
from r2r.graph.gates import NOTE_CHARS, GateRequest
from r2r.telegram.client import TelegramClient
from r2r.whatsapp.messages import _plain

TEXT_CHARS = 3500  # Telegram allows 4,096 per message; the full gate goes as a document
BUTTONS = [
    [("Approve", "approve"), ("Redirect", "redirect")],
    [("Stop", "stop"), ("Quit for now", "quit")],
]
NOTE_PROMPT = {
    "redirect": "Reply with a one-line note for the long-term planner.",
    "stop": "Reply with the reason for stopping (or '-' for none).",
}


class TelegramChannel:
    def __init__(
        self,
        client: TelegramClient,
        chat_id: str,
        poll_s: int = 50,
        nonce: Callable[[], str] = lambda: secrets.token_hex(4),
    ) -> None:
        self.client, self.chat_id, self.poll_s, self.nonce = client, str(chat_id), poll_s, nonce
        self.offset: int | None = None

    def ask(self, req: GateRequest, run_id: str) -> HumanDecision | None:
        nonce = self.nonce()
        body = _plain(req.markdown)
        cut = len(body) > TEXT_CHARS
        text = f"r2r · run {run_id}\n{req.title}\n\n" + (
            body[:TEXT_CHARS].rsplit("\n", 1)[0] + "\n…\n(full text attached)" if cut else body
        )
        buttons = [[(label, f"{nonce}:{action}") for label, action in row] for row in BUTTONS]
        message_id = self.client.send_message(self.chat_id, text, buttons=buttons)
        if cut:
            self.client.send_document(self.chat_id, "gate.md", req.markdown.encode(), req.title)
        # "redirect" or "stop" once pressed: the owner's next text message is the note
        waiting_for: str | None = None
        while True:
            for u in self.client.get_updates(self.offset, timeout=self.poll_s):
                self.offset = u["update_id"] + 1
                cq = u.get("callback_query")
                if cq is not None:
                    if str(cq.get("message", {}).get("chat", {}).get("id")) != self.chat_id:
                        continue
                    stamp, _, action = str(cq.get("data", "")).partition(":")
                    if stamp != nonce:
                        self.client.answer_callback(cq["id"], "This gate is already closed.")
                        continue
                    self.client.answer_callback(cq["id"])
                    if action == "approve":
                        self.client.clear_buttons(self.chat_id, message_id)
                        return _decision(run_id, "approve")
                    if action == "quit":
                        self.client.clear_buttons(self.chat_id, message_id)
                        self.client.send_message(
                            self.chat_id, f"Left pending. Continue with: r2r resume {run_id}"
                        )
                        return None
                    if action in NOTE_PROMPT:
                        waiting_for = action
                        self.client.send_message(
                            self.chat_id, NOTE_PROMPT[action], force_reply=True
                        )
                    continue
                msg = u.get("message")
                if msg is None or str(msg.get("chat", {}).get("id")) != self.chat_id:
                    continue
                note = str(msg.get("text", "")).strip()[:NOTE_CHARS]
                if waiting_for is None or not note:
                    continue
                self.client.clear_buttons(self.chat_id, message_id)
                return _decision(run_id, waiting_for, "" if note == "-" else note)


def _decision(run_id: str, action: str, note: str = "") -> HumanDecision:
    return HumanDecision(created_by="human", run_id=run_id, action=action, note=note)
