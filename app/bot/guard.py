"""Bearer-token check, rate limit, and request size limit for the bot API.

If BOT_API_TOKEN is unset the routes stay registered but every call is refused.
The comparison hashes both values first so a short guess cannot skip the check.
"""

import hashlib
import hmac
import logging
import time
from collections import deque

from fastapi import HTTPException, Request

logger = logging.getLogger("smartstock.bot")

MAX_REQUESTS = 120
WINDOW_SECONDS = 60
MAX_BODY = 256 * 1024

_buckets: dict[str, deque] = {}


def tokens_equal(provided: str, expected: str) -> bool:
    """Constant-time equality for secrets of any length."""
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def safe_bot_name(value: str | None) -> str:
    """A short audit label. Newlines and other characters are dropped."""
    if not value:
        return "-"
    cleaned = "".join(ch if (ch.isalnum() or ch in "._- ") else " " for ch in value)
    cleaned = " ".join(cleaned.split())[:40]
    return cleaned or "invalid"


def reset_limits() -> None:
    _buckets.clear()


def allow(key: str, now: float | None = None) -> bool:
    moment = time.monotonic() if now is None else now
    if len(_buckets) > 5000:
        _buckets.clear()
    bucket = _buckets.setdefault(key, deque())
    cutoff = moment - WINDOW_SECONDS
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= MAX_REQUESTS:
        return False
    bucket.append(moment)
    return True


def _client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return host + ":" + safe_bot_name(request.headers.get("x-bot-name"))


def _bearer(header: str | None) -> str:
    if not header:
        return ""
    scheme, _, rest = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return rest.strip()


async def require_bot(request: Request) -> None:
    """Refuse the call unless the configured token matches. Never open when unset."""
    if not allow(_client_key(request)):
        raise HTTPException(status_code=429, detail="too many requests")

    settings = request.app.state.settings
    expected = (settings.bot_api_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="unavailable")

    provided = _bearer(request.headers.get("authorization"))
    if not tokens_equal(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")

    if request.method in {"POST", "PUT", "PATCH"}:
        claimed = request.headers.get("content-length")
        if claimed:
            try:
                size = int(claimed)
            except ValueError:
                raise HTTPException(status_code=400, detail="invalid") from None
            if size > MAX_BODY:
                raise HTTPException(status_code=413, detail="too large")


def body_too_large(body: bytes) -> bool:
    return len(body) > MAX_BODY


class BotAuditMiddleware:
    """Log the bot name and status without reading or buffering the body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path != "/bot" and not path.startswith("/bot/"):
            await self.app(scope, receive, send)
            return

        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            bot_name = "-"
            for key, value in scope.get("headers") or []:
                if key.lower() == b"x-bot-name":
                    bot_name = safe_bot_name(value.decode("latin-1"))
                    break
            logger.info(
                "bot api bot=%s method=%s path=%s status=%s",
                bot_name,
                scope.get("method"),
                path,
                status,
            )
