"""Claim, store, and answer one inbound event. The HTTP handler returns before this runs."""

import logging
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.channels.meta import provider_message_id
from app.channels.window import TEMPLATE_NAMES, outbound_mode
from app.orchestrator.handle import build_reply
from app.store import claim_event, fetchone, insert_message, load_catalog
from app.timeutil import jhb_now

logger = logging.getLogger("smartstock.webhook")

MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "application/pdf": ".pdf",
}


async def process_events(events, app) -> None:
    for event in events:
        try:
            await process_one(event, app)
        except Exception:
            logger.exception("event failed channel=%s", event.channel)


async def process_one(event, app) -> None:
    pool = app.state.db
    if pool is None:
        logger.warning("no database configured, event skipped")
        return
    async with pool.connection() as conn:
        async with conn.transaction():
            if not await claim_event(conn, event.channel, event.external_id):
                logger.info("duplicate event %s", event.external_id)
                return
            user = await _resolve_user(conn, event)
            message_id = await insert_message(
                conn,
                shop_id=user["shop_id"],
                user_id=user["id"],
                direction="inbound",
                channel=event.channel,
                body=_body(event),
                media_url=event.media_url,
                sent_at=event.sent_at,
                raw=event.raw,
                external_id=event.external_id,
            )
            products, ingredients = await load_catalog(conn, user["shop_id"])
    await _store_media(app, event, user["shop_id"], message_id)
    now = jhb_now()
    ctx = SimpleNamespace(
        pool=pool,
        settings=app.state.settings,
        llm=app.state.llm,
        shop_id=user["shop_id"],
        user_id=user["id"],
        message_id=message_id,
        channel=event.channel,
        sender_id=event.sender_id,
        text=event.text or event.caption or "",
        today=now.date(),
        now=now,
        consent_at=user["consent_at"],
        products=products,
        ingredients=ingredients,
        delete_after_send=False,
    )
    try:
        reply = await _reply_text(event, ctx)
    except Exception:
        logger.exception("reply failed shop=%s", user["shop_id"])
        reply = "Something went wrong on my side. Please send that again."
    reply = _clip(event.channel, reply)
    raw, external_out = await _send(app, event, reply, now)
    async with pool.connection() as conn:
        async with conn.transaction():
            await insert_message(
                conn,
                shop_id=user["shop_id"],
                user_id=user["id"],
                direction="outbound",
                channel=event.channel,
                body=reply,
                media_url=None,
                sent_at=jhb_now(),
                raw={"in_reply_to_external": event.external_id, "provider": raw},
                external_id=external_out,
            )
            if ctx.delete_after_send and "error" not in raw and "skipped" not in raw:
                await conn.execute("DELETE FROM shops WHERE id = %s", (user["shop_id"],))


async def _resolve_user(conn, event) -> dict:
    if event.channel == "whatsapp":
        wa = _digits(event.sender_id)
        row = await fetchone(
            conn,
            """
            SELECT id, shop_id, consent_at, name
            FROM users
            WHERE whatsapp_wa_id = %s OR phone = %s
            ORDER BY (whatsapp_wa_id = %s) DESC
            LIMIT 1
            """,
            (wa, wa, wa),
        )
        if row:
            await conn.execute(
                """
                UPDATE users
                SET last_inbound_at = now(),
                    name = COALESCE(name, %s),
                    whatsapp_wa_id = COALESCE(whatsapp_wa_id, %s),
                    phone = COALESCE(phone, %s)
                WHERE id = %s
                """,
                (event.sender_name, wa, wa, row["id"]),
            )
            return row
        shop_id = "shop-" + uuid4().hex[:12]
        shop_name = event.sender_name or f"Shop {wa[-4:]}"
        await conn.execute(
            "INSERT INTO shops (id, name, business_type) VALUES (%s, %s, 'kota')",
            (shop_id, shop_name),
        )
        created = await fetchone(
            conn,
            """
            INSERT INTO users (shop_id, name, phone, role, whatsapp_wa_id, last_inbound_at)
            VALUES (%s, %s, %s, 'owner', %s, now())
            RETURNING id, shop_id, consent_at, name
            """,
            (shop_id, event.sender_name, wa, wa),
        )
        return created
    row = await fetchone(
        conn,
        """
        SELECT id, shop_id, consent_at, name
        FROM users
        WHERE instagram_user_id = %s
        LIMIT 1
        """,
        (event.sender_id,),
    )
    if row:
        await conn.execute(
            "UPDATE users SET last_inbound_at = now(), name = COALESCE(name, %s) WHERE id = %s",
            (event.sender_name, row["id"]),
        )
        return row
    shop_id = "shop-" + uuid4().hex[:12]
    shop_name = event.sender_name or "Instagram shop"
    await conn.execute(
        "INSERT INTO shops (id, name, business_type) VALUES (%s, %s, 'kota')",
        (shop_id, shop_name),
    )
    return await fetchone(
        conn,
        """
        INSERT INTO users (shop_id, name, role, instagram_user_id, last_inbound_at)
        VALUES (%s, %s, 'owner', %s, now())
        RETURNING id, shop_id, consent_at, name
        """,
        (shop_id, event.sender_name, event.sender_id),
    )


async def _reply_text(event, ctx) -> str:
    caption = (event.caption or "").strip()
    text = (event.text or "").strip()
    if event.type == "audio" and not text and not caption:
        return (
            "I got your voice note and saved it. I can't listen yet. "
            "Please type what you said and I will record it."
        )
    if event.type == "image" and not text and not caption:
        return (
            "I got your photo and saved it. If it is a supplier invoice, "
            "tell me the supplier and the total."
        )
    if event.type in {"image", "audio"} and (caption or text):
        ctx.text = caption or text
        follow = await build_reply(ctx)
        lead = "I got your photo and saved it." if event.type == "image" else "I got your voice note."
        return lead + "\n" + follow
    ctx.text = text
    return await build_reply(ctx)


async def _send(app, event, reply: str, now) -> tuple[dict, str | None]:
    mode = outbound_mode(event.channel, "reply", now, now)
    meta = app.state.meta
    try:
        if mode == "skip":
            return {"skipped": "outside_instagram_window"}, None
        if mode == "template":
            raw = await meta.send_whatsapp_template(event.sender_id, TEMPLATE_NAMES["reply"], reply)
        elif event.channel == "instagram":
            raw = await meta.send_instagram_text(event.sender_id, reply)
        else:
            raw = await meta.send_whatsapp_text(_digits(event.sender_id), reply)
        return raw, provider_message_id(raw)
    except Exception as exc:
        logger.exception("send failed")
        return {"error": str(exc)[:300]}, None


async def _store_media(app, event, shop_id: str, message_id) -> None:
    content = None
    mime = event.mime_type or "application/octet-stream"
    try:
        if event.channel == "whatsapp" and event.media_id:
            downloaded = await app.state.meta.download_whatsapp_media(event.media_id)
            if downloaded:
                content, mime = downloaded
        elif event.media_url:
            response = await app.state.http.get(event.media_url)
            if response.status_code < 300:
                content = response.content
                mime = response.headers.get("content-type", mime)
    except Exception:
        logger.exception("media store failed")
        return
    if not content:
        return
    if len(content) > 8_000_000:
        return
    ext = MIME_EXT.get(mime.split(";")[0].strip().lower(), ".bin")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", event.external_id)[:80]
    folder = Path(app.state.settings.media_dir) / shop_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe}{ext}"
    path.write_bytes(content)
    pool = app.state.db
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE messages SET media_url = %s WHERE id = %s AND shop_id = %s",
            (str(path), message_id, shop_id),
        )


def _body(event) -> str:
    if event.text:
        return event.text[:8000]
    if event.caption:
        return event.caption[:8000]
    if event.type == "image":
        return "[image]"
    if event.type == "audio":
        return "[voice note]"
    return "[message]"


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit()) or value


def _clip(channel: str, text: str) -> str:
    limit = 1000 if channel == "instagram" else 4096
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
