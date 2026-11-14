"""HTTP routes: health, Meta webhook, and the optional scheduler trigger."""

import hmac
import json
import logging

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from app.webhook.parse import parse_webhook
from app.webhook.process import process_events
from app.webhook.signature import verification_ok, verify_signature

logger = logging.getLogger("smartstock.http")
MAX_BODY = 2_000_000


def register(app: FastAPI) -> None:
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request):
        pool = request.app.state.db
        if pool is None:
            return JSONResponse({"status": "degraded", "db": "unconfigured"}, status_code=503)
        try:
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
        except Exception:
            logger.exception("ready check failed")
            return JSONResponse({"status": "degraded", "db": "down"}, status_code=503)
        return {"status": "ok", "db": "ok"}

    @app.get("/")
    async def root():
        return PlainTextResponse("SmartStock AI is running. Open /health to check it.")

    @app.get("/webhook")
    async def verify(request: Request):
        settings = request.app.state.settings
        params = request.query_params
        challenge = params.get("hub.challenge")
        if challenge is not None and verification_ok(
            params.get("hub.mode"),
            params.get("hub.verify_token"),
            settings.webhook_verify_token,
        ):
            return PlainTextResponse(challenge)
        return Response(status_code=403)

    @app.post("/webhook")
    async def receive(request: Request, background: BackgroundTasks):
        body = await request.body()
        if len(body) > MAX_BODY:
            return Response(status_code=413)
        settings = request.app.state.settings
        if not verify_signature(body, request.headers.get("x-hub-signature-256"), settings.meta_app_secret):
            return Response(status_code=401)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return Response(status_code=400)
        events = parse_webhook(payload)
        logger.info("webhook accepted events=%s", len(events))
        background.add_task(process_events, events, request.app)
        return Response(status_code=200)

    @app.post("/internal/jobs/{name}")
    async def run_job(name: str, request: Request):
        from app.scheduler.jobs import run_slot

        settings = request.app.state.settings
        provided = request.headers.get("x-scheduler-secret") or ""
        if not settings.scheduler_secret or not _same(provided, settings.scheduler_secret):
            return Response(status_code=403)
        if name not in {"morning", "evening", "weekly"}:
            return Response(status_code=404)
        sent = await run_slot(request.app, name)
        return {"ok": True, "sent": sent}


def _same(provided: str, secret: str) -> bool:
    if len(provided) != len(secret):
        return False
    return hmac.compare_digest(provided, secret)
