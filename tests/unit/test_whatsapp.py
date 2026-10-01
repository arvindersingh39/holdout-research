"""WhatsApp gate channel and bot against a fake Graph API and signed fake webhooks (D48)."""

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from samples import roadmap

from r2r.graph.gates import GateRequest, gate_request
from r2r.whatsapp.bot import Bot
from r2r.whatsapp.channel import WhatsAppChannel
from r2r.whatsapp.client import GraphClient, WhatsAppError
from r2r.whatsapp.messages import button_id, parse_button_id
from r2r.whatsapp.store import GateStore
from r2r.whatsapp.webhook import create_app

OWNER = "447700900123"
SECRET = "app-secret-123"
VERIFY = "verify-token-xyz"
TOKEN = "EAAG-very-secret-token"
GATES = {
    "remind_after_h": 24,
    "max_reminders": 3,
    "full_text": "attach",
    "batch_pending_gates": True,
    "gate_template": "r2r_gate_waiting",
    "template_lang": "en_US",
}


class FakeGraph:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.uploads = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.url.path.endswith("/media"):
            self.uploads += 1
            return httpx.Response(200, json={"id": f"media-{self.uploads}"})
        body = json.loads(request.content)
        self.sent.append(body)
        return httpx.Response(200, json={"messages": [{"id": f"wamid.out{len(self.sent)}"}]})

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


@dataclass
class Env:
    graph: FakeGraph
    store: GateStore
    bot: Bot
    channel: WhatsAppChannel
    app: TestClient
    spawned: list[str]
    clock: Clock


@pytest.fixture
def env(tmp_path: Path) -> Env:
    graph = FakeGraph()
    http = httpx.Client(transport=httpx.MockTransport(graph.handler))
    client = GraphClient(TOKEN, "1234567890", http=http)
    clock = Clock()
    store = GateStore(tmp_path / "whatsapp.sqlite")
    spawned: list[str] = []
    bot = Bot(store, client, owner=OWNER, gates=GATES, spawn_resume=spawned.append, now=clock)
    channel = WhatsAppChannel(store, client, owner=OWNER, gates=GATES, now=clock)
    app = TestClient(create_app(bot, app_secret=SECRET, verify_token=VERIFY))
    return Env(graph, store, bot, channel, app, spawned, clock)


def _post(app: TestClient, payload: dict, secret: str = SECRET):
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return app.post(
        "/webhook",
        content=raw,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )


_ids = iter(range(1, 10_000))


def _msg(kind: str, value, sender: str = OWNER, mid: str | None = None) -> dict:
    m = {
        "from": sender,
        "id": mid or f"wamid.in{next(_ids)}",
        "timestamp": "1800000000",
        "type": kind,
    }
    if kind == "text":
        m["text"] = {"body": value}
    elif kind == "interactive":
        m["interactive"] = {"type": "button_reply", "button_reply": {"id": value, "title": "x"}}
    elif kind == "button":
        m["button"] = {"payload": value, "text": "Show gate"}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"wa_id": sender}],
                            "messages": [m],
                        },
                    }
                ],
            }
        ],
    }


def _req() -> GateRequest:
    return gate_request("roadmap", "Approve the first roadmap", roadmap())


def _open_window(app: TestClient) -> None:
    _post(app, _msg("text", "hi"))


def _buttons(graph: FakeGraph) -> list[dict]:
    last = [m for m in graph.sent if m["type"] == "interactive"][-1]
    return [b["reply"] for b in last["interactive"]["action"]["buttons"]]


# --- webhook hygiene -------------------------------------------------------------------------


def test_verification_handshake(env) -> None:
    _graph, _store, _bot, _channel, app, _spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    ok = app.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"},
    )
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = app.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
    )
    assert bad.status_code == 403


def test_bad_signature_rejected(env) -> None:
    graph, store, _bot, _channel, app, _spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    r = _post(app, _msg("text", "hi"), secret="wrong-secret")
    assert r.status_code == 403
    assert store.last_inbound() is None and graph.sent == []
    raw = json.dumps(_msg("text", "hi")).encode()
    assert app.post("/webhook", content=raw).status_code == 403  # no header at all


def test_non_owner_ignored(env) -> None:
    graph, store, _bot, _channel, app, _spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    assert _post(app, _msg("text", "approve everything", sender="15550001111")).status_code == 200
    assert store.last_inbound() is None and graph.sent == []


# --- gates: open window, closed window --------------------------------------------------------


def test_open_window_sends_interactive_gate_and_document(env) -> None:
    graph, _store, _bot, channel, app, _spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    _open_window(app)
    graph.sent.clear()
    assert channel.ask(_req(), "run-1") is None  # pending: answered on WhatsApp later
    assert graph.types() == ["document", "interactive"]
    doc = graph.sent[0]["document"]
    assert doc["id"] == "media-1" and doc["filename"].endswith(".txt")
    inter = graph.sent[1]["interactive"]
    assert len(inter["body"]["text"]) <= 1024 and "Roadmap v1" in inter["body"]["text"]
    titles = [b["title"] for b in _buttons(graph)]
    assert titles == ["Approve", "Redirect", "Stop"]
    assert all(len(b["id"]) <= 128 for b in _buttons(graph))
    assert channel.ask(_req(), "run-1") is None
    assert len(graph.sent) == 2  # asking again doesn't re-send


def test_summary_only_sends_no_document(env, tmp_path: Path) -> None:
    graph, store, _bot, _channel, app, _spawned, clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    _open_window(app)
    graph.sent.clear()
    client = GraphClient(
        TOKEN, "1234567890", http=httpx.Client(transport=httpx.MockTransport(graph.handler))
    )
    ch = WhatsAppChannel(
        store, client, owner=OWNER, gates={**GATES, "full_text": "summary_only"}, now=clock
    )
    ch.ask(_req(), "run-2")
    assert graph.types() == ["interactive"]


def test_closed_window_sends_template_then_gate_on_show(env) -> None:
    graph, _store, _bot, channel, app, _spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    assert channel.ask(_req(), "run-1") is None  # no inbound message yet: window closed
    assert graph.types() == ["template"]
    tpl = graph.sent[0]["template"]
    assert tpl["name"] == "r2r_gate_waiting" and tpl["language"]["code"] == "en_US"
    payload = tpl["components"][1]["parameters"][0]["payload"]
    assert len(payload) <= 128
    _post(app, _msg("button", payload))  # owner taps "Show gate": window opens
    assert graph.types()[-2:] == ["document", "interactive"]


def test_closed_window_batches_pending_gates(env) -> None:
    graph, _store, _bot, channel, app, _spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    channel.ask(_req(), "run-1")
    channel.ask(
        gate_request("result", "Review the result of M1", roadmap(), editable=False), "run-2"
    )
    assert graph.types() == ["template"]  # one ping covers every waiting gate
    payload = graph.sent[0]["template"]["components"][1]["parameters"][0]["payload"]
    _post(app, _msg("button", payload))
    assert graph.types().count("interactive") == 2


# --- decisions -------------------------------------------------------------------------------


def test_approve_stores_decision_and_spawns_resume(env) -> None:
    graph, _store, _bot, channel, app, spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    _open_window(app)
    channel.ask(_req(), "run-1")
    approve = _buttons(graph)[0]["id"]
    _post(app, _msg("interactive", approve))
    assert spawned == ["run-1"]
    assert "Approved" in graph.sent[-1]["text"]["body"]
    d = channel.ask(_req(), "run-1")  # what `r2r resume` sees
    assert d is not None and d.action == "approve" and d.run_id == "run-1"
    assert d.created_by == "human"


def test_stop(env) -> None:
    graph, _store, _bot, channel, app, spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    _open_window(app)
    channel.ask(_req(), "run-1")
    _post(app, _msg("interactive", _buttons(graph)[2]["id"]))
    assert channel.ask(_req(), "run-1").action == "stop"
    assert spawned == ["run-1"]


def test_duplicate_delivery_processed_once(env) -> None:
    graph, _store, _bot, channel, app, spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    _open_window(app)
    channel.ask(_req(), "run-1")
    tap = _msg("interactive", _buttons(graph)[0]["id"], mid="wamid.same")
    _post(app, tap)
    _post(app, tap)  # Meta redelivers the same event
    assert spawned == ["run-1"]


def test_reused_or_forged_nonce_ignored(env) -> None:
    graph, _store, _bot, channel, app, spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    _open_window(app)
    channel.ask(_req(), "run-1")
    approve = _buttons(graph)[0]["id"]
    _post(app, _msg("interactive", approve))
    _post(app, _msg("interactive", approve))  # a second, later tap on the same button
    gate, _, _ = parse_button_id(approve)
    _post(app, _msg("interactive", button_id(gate, "stop", "deadbeef")))  # forged nonce
    assert spawned == ["run-1"]
    assert "no longer open" in graph.sent[-1]["text"]["body"]


def test_redirect_captures_next_message_as_note(env) -> None:
    graph, _store, _bot, channel, app, spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    _open_window(app)
    channel.ask(_req(), "run-1")
    _post(app, _msg("interactive", _buttons(graph)[1]["id"]))
    assert "note" in graph.sent[-1]["text"]["body"].lower()
    assert spawned == []
    _post(app, _msg("text", "Split M1 into two milestones."))
    assert spawned == ["run-1"]
    d = channel.ask(_req(), "run-1")
    assert d.action == "redirect" and d.note == "Split M1 into two milestones."


def test_edit_leaves_gate_open_and_other_text_gets_help(env) -> None:
    graph, _store, _bot, channel, app, spawned, _clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    _open_window(app)
    channel.ask(_req(), "run-1")
    _post(app, _msg("text", "edit"))
    assert "r2r resume run-1" in graph.sent[-1]["text"]["body"]
    _post(app, _msg("text", "please rewrite the coder prompt for me"))
    assert "Approve" in graph.sent[-1]["text"]["body"]  # help text only; never relayed
    assert spawned == []
    assert channel.ask(_req(), "run-1") is None  # still pending


# --- reminders, logging -------------------------------------------------------------------------


def test_reminders_until_max(env) -> None:
    graph, _store, bot, channel, _app, _spawned, clock = (
        env.graph,
        env.store,
        env.bot,
        env.channel,
        env.app,
        env.spawned,
        env.clock,
    )
    channel.ask(_req(), "run-1")  # window closed: template
    for _ in range(5):
        clock.t += 24 * 3600 + 1
        bot.remind()
    assert graph.types().count("template") == 1 + 3  # first ping + max_reminders
    bot.remind()
    assert graph.types().count("template") == 4


def test_tokens_never_logged(env, caplog: pytest.LogCaptureFixture) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"Invalid token {TOKEN}"}})

    client = GraphClient(
        TOKEN, "1234567890", http=httpx.Client(transport=httpx.MockTransport(failing))
    )
    caplog.set_level(logging.DEBUG)
    with pytest.raises(WhatsAppError) as e:
        client.send_text(OWNER, "hello")
    assert TOKEN not in str(e.value)
    assert TOKEN not in caplog.text


def test_button_id_round_trip() -> None:
    bid = button_id("abc123def0", "approve", "00ff00ff")
    assert len(bid) <= 128
    assert parse_button_id(bid) == ("abc123def0", "approve", "00ff00ff")
    assert parse_button_id("something else") is None
