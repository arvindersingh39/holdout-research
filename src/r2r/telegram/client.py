"""Thin client for Telegram's official Bot API (D91). The token is part of every URL, so errors
are re-raised without the URL or the token."""

from __future__ import annotations

from typing import Any

import httpx

API = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, http: httpx.Client | None = None) -> None:
        self._token = token
        self._http = http or httpx.Client(timeout=70.0)

    def _call(self, method: str, files: dict[str, Any] | None = None, **params: Any) -> Any:
        url = f"{API}/bot{self._token}/{method}"
        try:
            if files:
                resp = self._http.post(url, data=params, files=files)
            else:
                resp = self._http.post(url, json=params)
        except httpx.HTTPError as e:
            raise TelegramError(f"Telegram {method} failed: {type(e).__name__}") from None
        try:
            body = resp.json()
        except ValueError:
            raise TelegramError(f"Telegram {method}: HTTP {resp.status_code}") from None
        if not body.get("ok"):
            why = str(body.get("description", f"HTTP {resp.status_code}")).replace(
                self._token, "***"
            )
            raise TelegramError(f"Telegram {method}: {why}")
        return body["result"]

    def send_message(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
        force_reply: bool = False,
    ) -> int:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            params["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row]
                    for row in buttons
                ]
            }
        elif force_reply:
            params["reply_markup"] = {"force_reply": True}
        return int(self._call("sendMessage", **params)["message_id"])

    def send_document(self, chat_id: str, filename: str, content: bytes, caption: str = "") -> None:
        self._call(
            "sendDocument",
            files={"document": (filename, content, "text/markdown")},
            chat_id=chat_id,
            caption=caption,
        )

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            params["offset"] = offset
        return list(self._call("getUpdates", **params))

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def clear_buttons(self, chat_id: str, message_id: int) -> None:
        self._call(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup={"inline_keyboard": []},
        )

    def recent_chats(self) -> list[str]:
        """Chat ids that recently wrote to the bot (for `r2r telegram chat-id`)."""
        chats: list[str] = []
        for u in self.get_updates(None, timeout=0):
            chat = (u.get("message") or {}).get("chat", {}).get("id")
            if chat is not None and str(chat) not in chats:
                chats.append(str(chat))
        return chats
