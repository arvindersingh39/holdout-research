"""Gates answered from Telegram (D91): the bot sends the gate with buttons and long-polls for the
owner's answer. Only the configured chat counts; a random nonce ties buttons to this gate. Only
structured decisions (a button, plus a note for redirect or stop) are accepted."""

from __future__ import annotations

import secrets
from collections.abc import Callable

from r2r.contracts import HumanDecision
from r2r.graph.gates import NOTE_CHARS, GateRequest
from r2r.log import get_logger
from r2r.telegram.client import TelegramClient, TelegramError
from r2r.whatsapp.messages import _plain

TEXT_CHARS = 3500  # Telegram allows 4,096 per message; the full gate goes as a document
BUTTONS = [
    [("Approve", "approve"), ("Redirect", "redirect")],
    [("Stop", "stop"), ("Quit for now", "quit")],
]
log = get_logger(__name__)

PATH_ROW = [("Other path", "path")]  # D93: approve with a results file the owner names
NOTE_PROMPT = {
    "redirect": "Reply with a one-line note for the long-term planner.",
    "stop": "Reply with the reason for stopping (or '-' for none).",
    "path": "Reply with the results file path (relative to the workspace, or absolute).",
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
        rows = [*BUTTONS, PATH_ROW] if req.path_prompt else BUTTONS
        buttons = [[(label, f"{nonce}:{action}") for label, action in row] for row in rows]
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
                        self._answer(cq["id"], "This gate is already closed.")
                        continue
                    self._answer(cq["id"])
                    if action == "approve":
                        self.client.clear_buttons(self.chat_id, message_id)
                        return _decision(run_id, "approve")
                    if action == "quit":
                        self.client.clear_buttons(self.chat_id, message_id)
                        self.client.send_message(
                            self.chat_id, f"Left pending. Continue with: r2r resume {run_id}"
                        )
                        return None
                    if action in NOTE_PROMPT and (action != "path" or req.path_prompt):
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
                action = "approve" if waiting_for == "path" else waiting_for
                return _decision(run_id, action, "" if note == "-" else note)

    def _answer(self, callback_id: str, text: str = "") -> None:
        """Stop the pressed button spinning. Only cosmetic: Telegram rejects answers to presses
        older than a few seconds (e.g. read on resume), which must not crash the gate."""
        try:
            self.client.answer_callback(callback_id, text)
        except TelegramError as e:
            log.warning("button press not acknowledged", extra={"error": str(e)[:200]})


def _decision(run_id: str, action: str, note: str = "") -> HumanDecision:
    return HumanDecision(created_by="human", run_id=run_id, action=action, note=note)
