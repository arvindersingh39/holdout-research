"""D91: gates answered from Telegram (official Bot API, long polling). A fake Telegram server
(httpx.MockTransport) stands in for the network."""

import json
from collections.abc import Callable

import httpx
import pytest

from r2r.graph.gates import GateRequest
from r2r.telegram.channel import TelegramChannel
from r2r.telegram.client import TelegramClient, TelegramError

TOKEN = "123456:ABC-secret-token-value"
CHAT = "4242"
REQ = GateRequest(gate="result", title="Review the result of M9", markdown="## Result\n\n**met**")

Batch = Callable[["FakeTelegram"], list[dict]]


class FakeTelegram:
    """Records every API call; each getUpdates returns the next scripted batch."""

    def __init__(self, *batches: Batch, expired: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.batches = list(batches)
        self.next_id = 0
        self.expired = expired  # callback ids Telegram no longer accepts answers for

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith(f"/bot{TOKEN}/")
        method = request.url.path.rsplit("/", 1)[-1]
        ctype = request.headers.get("content-type", "")
        body = json.loads(request.content) if "json" in ctype else {"multipart": request.content}
        self.calls.append((method, body))
        if method == "answerCallbackQuery" and body.get("callback_query_id") in self.expired:
            return httpx.Response(
                400,
                json={
                    "ok": False,
                    "description": "Bad Request: query is too old and response timeout expired "
                    "or query ID is invalid",
                },
            )
        if method == "getUpdates":
            result = self.batches.pop(0)(self) if self.batches else []
        elif method in ("sendMessage", "sendDocument"):
            self.next_id += 1
            result = {"message_id": self.next_id}
        else:
            result = True
        return httpx.Response(200, json={"ok": True, "result": result})

    def sent(self, method: str) -> list[dict]:
        return [b for m, b in self.calls if m == method]

    def nonce(self) -> str:
        """The nonce on the newest gate message's buttons."""
        msg = [
            b for b in self.sent("sendMessage") if "inline_keyboard" in b.get("reply_markup", {})
        ]
        msg = msg[-1]
        data = msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        return data.split(":")[0]


_ids = iter(range(1, 10_000))


def press(action: str, chat: str = CHAT, nonce: str | None = None, cb: str = "cb") -> Batch:
    def make(t: FakeTelegram) -> list[dict]:
        data = f"{nonce or t.nonce()}:{action}"
        cq = {"id": cb, "data": data, "message": {"message_id": 1, "chat": {"id": int(chat)}}}
        return [{"update_id": next(_ids), "callback_query": cq}]

    return make


def say(text: str, chat: str = CHAT) -> Batch:
    return lambda t: [
        {"update_id": next(_ids), "message": {"chat": {"id": int(chat)}, "text": text}}
    ]


def channel(fake: FakeTelegram) -> TelegramChannel:
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    return TelegramChannel(TelegramClient(TOKEN, http=http), CHAT, poll_s=0)


def test_approve_button() -> None:
    fake = FakeTelegram(press("approve"))
    d = channel(fake).ask(REQ, "study-3")
    assert d is not None and d.action == "approve" and d.created_by == "human"
    msg = fake.sent("sendMessage")[0]
    assert (
        msg["chat_id"] == CHAT and "Review the result of M9" in msg["text"] and "met" in msg["text"]
    )
    labels = [b["text"] for row in msg["reply_markup"]["inline_keyboard"] for b in row]
    assert labels == ["Approve", "Redirect", "Stop", "Quit for now"]
    assert fake.sent("answerCallbackQuery")  # the button stops spinning


def test_redirect_asks_for_a_note() -> None:
    fake = FakeTelegram(press("redirect"), say("Use one criterion per test name."))
    d = channel(fake).ask(REQ, "study-3")
    assert d is not None and d.action == "redirect" and d.note == "Use one criterion per test name."
    prompts = [b for b in fake.sent("sendMessage") if "force_reply" in json.dumps(b)]
    assert prompts and "note" in prompts[0]["text"].lower()


def test_stop_with_note_and_quit() -> None:
    fake = FakeTelegram(press("stop"), say("Out of budget."))
    d = channel(fake).ask(REQ, "study-3")
    assert d is not None and d.action == "stop" and d.note == "Out of budget."
    assert channel(FakeTelegram(press("quit"))).ask(REQ, "study-3") is None


def test_other_chats_and_stale_buttons_are_ignored() -> None:
    fake = FakeTelegram(
        press("approve", chat="999"),  # a stranger
        press("approve", nonce="deadbeef"),  # a button from an older gate
        say("approve", chat="999"),
        press("redirect"),
        say("hi", chat="999"),  # a stranger's text is not the note
        say("The real note."),
    )
    d = channel(fake).ask(REQ, "study-3")
    assert d is not None and d.action == "redirect" and d.note == "The real note."


def test_an_expired_button_press_does_not_crash_the_gate() -> None:
    """A press left from an older gate is answered too late on resume; Telegram rejects the
    answer, but the press is only cosmetic, so the gate keeps waiting for a real decision."""
    fake = FakeTelegram(
        press("approve", nonce="deadbeef", cb="old"),
        press("approve", cb="late"),
        expired=("old", "late"),
    )
    d = channel(fake).ask(REQ, "study-4")
    assert d is not None and d.action == "approve"


def test_long_gate_is_cut_and_attached_whole() -> None:
    long = GateRequest(gate="result", title="Long", markdown="line\n" * 3000)
    fake = FakeTelegram(press("approve"))
    channel(fake).ask(long, "study-3")
    assert len(fake.sent("sendMessage")[0]["text"]) <= 4096
    (doc,) = fake.sent("sendDocument")
    assert b"line\nline" in doc["multipart"] and b"gate.md" in doc["multipart"]
    short = FakeTelegram(press("approve"))
    channel(short).ask(REQ, "study-3")
    assert not short.sent("sendDocument")


def test_errors_never_show_the_token() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = TelegramClient(TOKEN, http=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(TelegramError) as e:
        client.send_message(CHAT, "hello")
    assert TOKEN not in str(e.value) and "sendMessage" in str(e.value)

    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    client = TelegramClient(TOKEN, http=httpx.Client(transport=httpx.MockTransport(refused)))
    with pytest.raises(TelegramError, match="Unauthorized"):
        client.send_message(CHAT, "hello")


def test_chats_that_wrote_to_the_bot() -> None:
    fake = FakeTelegram(say("/start", chat="4242"))
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    assert TelegramClient(TOKEN, http=http).recent_chats() == ["4242"]


# --- CLI wiring -------------------------------------------------------------------------------


def _settings(token: str | None, chat: str | None):
    from pathlib import Path

    from r2r.settings import Secrets, load_settings

    st = load_settings(Path(__file__).resolve().parents[1] / "fixtures" / "config", None)
    secrets = Secrets(telegram_bot_token=token, telegram_chat_id=chat)
    return st.model_copy(update={"secrets": secrets})


def test_make_channel_telegram_or_terminal_fallback(capsys) -> None:
    import r2r.cli as cli
    from r2r.graph.gates import TerminalChannel

    assert isinstance(cli.make_channel(_settings(None, None), "telegram"), TerminalChannel)
    ch = cli.make_channel(_settings(TOKEN, CHAT), "telegram")
    assert isinstance(ch, TelegramChannel) and ch.chat_id == CHAT
    assert TOKEN not in capsys.readouterr().out


def test_cli_chat_id_prints_ids_only(monkeypatch) -> None:
    from typer.testing import CliRunner

    import r2r.cli as cli

    fake = FakeTelegram(say("/start", chat="777"))
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(cli, "_telegram_client", lambda: TelegramClient(TOKEN, http=http))
    out = CliRunner().invoke(cli.app, ["telegram", "chat-id"])
    assert out.exit_code == 0 and "TELEGRAM_CHAT_ID=777" in out.output and TOKEN not in out.output


def test_benchmark_gate_takes_a_results_path() -> None:
    """D93: approve reads the default results file; "Other path" asks for one."""
    req = GateRequest(
        gate="benchmark", title="M3: run the benchmarks", markdown="run it", path_prompt="File: "
    )
    fake = FakeTelegram(press("path"), say("out/m3.json"))
    d = channel(fake).ask(req, "study-3")
    assert d is not None and d.action == "approve" and d.note == "out/m3.json"
    labels = [
        b["text"]
        for row in fake.sent("sendMessage")[0]["reply_markup"]["inline_keyboard"]
        for b in row
    ]
    assert "Other path" in labels
    d = channel(FakeTelegram(press("approve"))).ask(req, "study-3")
    assert d is not None and d.action == "approve" and d.note == ""
    plain = FakeTelegram(press("approve"))
    channel(plain).ask(REQ, "study-3")
    assert "Other path" not in json.dumps(plain.sent("sendMessage")[0])
