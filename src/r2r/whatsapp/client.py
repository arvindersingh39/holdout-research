"""Graph API sender for WhatsApp messages (httpx). The access token never reaches logs or errors."""

from __future__ import annotations

from typing import Any

import httpx

from r2r.log import get_logger, redact

log = get_logger("r2r.whatsapp")
GRAPH_VERSION = "v25.0"


class WhatsAppError(RuntimeError):
    pass


class GraphClient:
    def __init__(
        self,
        token: str,
        phone_number_id: str,
        http: httpx.Client | None = None,
        version: str = GRAPH_VERSION,
    ) -> None:
        self._token = token
        self._base = f"https://graph.facebook.com/{version}/{phone_number_id}"
        self._http = http or httpx.Client(timeout=30)

    def _check(self, resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("error", {}).get("message", "")
            except ValueError:
                msg = resp.text[:300]
            safe = redact(f"WhatsApp API {resp.status_code}: {msg}", [self._token])
            log.warning("whatsapp send failed", extra={"status": resp.status_code})
            raise WhatsAppError(safe)
        return resp.json()

    def _send(self, to: str, kind: str, body: dict[str, Any]) -> str:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": kind,
            kind: body,
        }
        data = self._check(
            self._http.post(
                f"{self._base}/messages",
                json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        )
        return str((data.get("messages") or [{}])[0].get("id", ""))

    def send_text(self, to: str, text: str) -> str:
        return self._send(to, "text", {"body": text[:4096]})

    def send_buttons(
        self, to: str, body: str, buttons: list[tuple[str, str]], footer: str = ""
    ) -> str:
        content: dict[str, Any] = {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": i[:256], "title": t[:20]}}
                    for i, t in buttons[:3]
                ]
            },
        }
        if footer:
            content["footer"] = {"text": footer[:60]}
        return self._send(to, "interactive", content)

    def send_template(self, to: str, name: str, lang: str, params: list[str], payload: str) -> str:
        return self._send(
            to,
            "template",
            {
                "name": name,
                "language": {"code": lang},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": p[:60]} for p in params],
                    },
                    {
                        "type": "button",
                        "sub_type": "quick_reply",
                        "index": "0",
                        "parameters": [{"type": "payload", "payload": payload[:128]}],
                    },
                ],
            },
        )

    def upload_document(self, content: bytes, filename: str, mime: str = "text/plain") -> str:
        data = self._check(
            self._http.post(
                f"{self._base}/media",
                headers={"Authorization": f"Bearer {self._token}"},
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (filename, content, mime)},
            )
        )
        return str(data["id"])

    def send_document(self, to: str, media_id: str, filename: str, caption: str = "") -> str:
        return self._send(
            to, "document", {"id": media_id, "filename": filename, "caption": caption[:1024]}
        )
