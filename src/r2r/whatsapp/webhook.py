"""FastAPI webhook for Meta: GET verification handshake, POST with HMAC signature check."""

from __future__ import annotations

import json

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from r2r.whatsapp.bot import Bot
from r2r.whatsapp.messages import signature_ok


def create_app(bot: Bot, app_secret: str, verify_token: str) -> FastAPI:
    app = FastAPI(title="r2r WhatsApp gates", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/webhook")
    def verify(request: Request) -> PlainTextResponse:
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == verify_token:
            return PlainTextResponse(q.get("hub.challenge", ""))
        return PlainTextResponse("forbidden", status_code=403)

    @app.post("/webhook")
    async def receive(request: Request, tasks: BackgroundTasks) -> JSONResponse:
        raw = await request.body()
        if not signature_ok(raw, request.headers.get("X-Hub-Signature-256"), app_secret):
            return JSONResponse({"error": "bad signature"}, status_code=403)
        try:
            payload = json.loads(raw)
        except ValueError:
            return JSONResponse({"error": "bad json"}, status_code=400)
        tasks.add_task(bot.handle, payload)  # answer Meta at once; work happens afterwards
        return JSONResponse({"ok": True})

    return app
