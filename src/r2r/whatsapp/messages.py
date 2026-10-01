"""Pure helpers: button ids, gate summaries, webhook parsing, signature checks."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any, Literal

from r2r.graph.gates import GateRequest

BODY_CHARS = 1024
ACTIONS = ("approve", "redirect", "stop")
TITLES = {"approve": "Approve", "redirect": "Redirect", "stop": "Stop"}
_BUTTON = re.compile(r"^g:([0-9a-f]{10}):(approve|redirect|stop):([0-9a-f]{8})$")
SHOW_ALL = "show:all"


def button_id(gate_id: str, action: str, nonce: str) -> str:
    return f"g:{gate_id}:{action}:{nonce}"


def parse_button_id(value: str) -> tuple[str, str, str] | None:
    m = _BUTTON.match(value or "")
    return (m.group(1), m.group(2), m.group(3)) if m else None


def _plain(markdown: str) -> str:
    text = re.sub(r"[*_`#>]+", "", markdown)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def summary(req: GateRequest, run_id: str, attached: bool) -> str:
    head = f"r2r · run {run_id}\n{req.title}\n\n"
    tail = "\n\nFull text attached." if attached else ""
    room = BODY_CHARS - len(head) - len(tail) - 3
    body = _plain(req.markdown)
    if len(body) > room:
        body = body[:room].rsplit("\n", 1)[0] + "\n…"
    return (head + body + tail)[:BODY_CHARS]


def signature_ok(raw: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@dataclass(frozen=True)
class Inbound:
    sender: str
    message_id: str
    kind: Literal["text", "button_reply", "template_button", "other"]
    value: str  # text body, button id or template payload


def parse_webhook(payload: dict[str, Any]) -> list[Inbound]:
    out: list[Inbound] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            for m in (change.get("value") or {}).get("messages", []) or []:
                kind, value = "other", ""
                if m.get("type") == "text":
                    kind, value = "text", (m.get("text") or {}).get("body", "")
                elif m.get("type") == "interactive":
                    reply = (m.get("interactive") or {}).get("button_reply") or {}
                    kind, value = "button_reply", reply.get("id", "")
                elif m.get("type") == "button":
                    kind, value = "template_button", (m.get("button") or {}).get("payload", "")
                out.append(Inbound(str(m.get("from", "")), str(m.get("id", "")), kind, value))
    return out
