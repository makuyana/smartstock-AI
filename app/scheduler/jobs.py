"""07:00 briefing, 19:00 check-in, Sunday 18:00 wrap. WhatsApp templates only."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.channels.meta import provider_message_id
from app.channels.window import TEMPLATE_NAMES
from app.modules.forecast import build_for_shop
from app.modules.insights import answer as insights_answer
from app.modules.inventory import fetch_ingredients, shopping_rows
from app.scheduler.copy import evening_text, morning_text, weekly_text
from app.store import fetchall, fetchone, insert_message
from app.timeutil import jhb_now

logger = logging.getLogger("smartstock.scheduler")
TZ = "Africa/Johannesburg"


def start_scheduler(app) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TZ)

    async def morning():
        await run_slot(app, "morning")

    async def evening():
        await run_slot(app, "evening")

    async def weekly():
        await run_slot(app, "weekly")

    scheduler.add_job(morning, CronTrigger(hour=7, minute=0, timezone=TZ), id="morning", replace_existing=True)
    scheduler.add_job(evening, CronTrigger(hour=19, minute=0, timezone=TZ), id="evening", replace_existing=True)
    scheduler.add_job(
        weekly,
        CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=TZ),
        id="weekly",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler


async def run_slot(app, slot: str) -> int:
    pool = app.state.db
    if pool is None:
        return 0
    now = jhb_now()
    today = now.date()
    if slot == "weekly":
        iso = today.isocalendar()
        key = f"weekly:{iso.year}-W{iso.week:02d}"
    else:
        key = f"{slot}:{today.isoformat()}"
    async with pool.connection() as conn:
        owners = await fetchall(
            conn,
            """
            SELECT u.id AS user_id, u.shop_id, u.whatsapp_wa_id, s.name AS shop_name
            FROM users u
            JOIN shops s ON s.id = u.shop_id
            WHERE u.role = 'owner'
              AND u.consent_at IS NOT NULL
              AND u.whatsapp_wa_id IS NOT NULL
            """,
        )
    sent = 0
    for owner in owners:
        try:
            text = await _text_for(pool, slot, owner, today)
        except Exception:
            logger.exception("slot text failed shop=%s", owner["shop_id"])
            continue
        async with pool.connection() as conn:
            claimed = await fetchone(
                conn,
                """
                INSERT INTO scheduled_sends (user_id, slot)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                RETURNING user_id
                """,
                (owner["user_id"], key),
            )
        if claimed is None:
            continue
        try:
            raw = await app.state.meta.send_whatsapp_template(
                owner["whatsapp_wa_id"],
                TEMPLATE_NAMES[slot],
                text,
            )
        except Exception:
            logger.exception("template send failed")
            async with pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM scheduled_sends WHERE user_id = %s AND slot = %s",
                    (owner["user_id"], key),
                )
            continue
        async with pool.connection() as conn:
            await insert_message(
                conn,
                shop_id=owner["shop_id"],
                user_id=owner["user_id"],
                direction="outbound",
                channel="whatsapp",
                body=text,
                media_url=None,
                sent_at=jhb_now(),
                raw={"slot": slot, "provider": raw},
                external_id=provider_message_id(raw),
            )
        sent += 1
    logger.info("slot %s sent %s", slot, sent)
    return sent


async def _text_for(pool, slot: str, owner: dict, today) -> str:
    async with pool.connection() as conn:
        if slot == "morning":
            forecasts, _missing = await build_for_shop(conn, owner["shop_id"], today)
            ingredients = await fetch_ingredients(conn, owner["shop_id"])
            low = [
                row
                for row in shopping_rows(ingredients)
                if row["is_low"] and row["stock"] is not None
            ]
            expiring = await fetchall(
                conn,
                """
                SELECT i.name, p.expiry_date
                FROM purchases p
                JOIN ingredients i ON i.id = p.ingredient_id
                WHERE p.shop_id = %s
                  AND p.qty_remaining > 0
                  AND p.expiry_date IS NOT NULL
                  AND p.expiry_date <= %s
                ORDER BY p.expiry_date
                """,
                (owner["shop_id"], today),
            )
            return morning_text(owner["shop_name"], forecasts, low, expiring)
        if slot == "evening":
            sales = await fetchall(
                conn,
                """
                SELECT p.name,
                       sum(se.quantity) AS qty,
                       sum(se.quantity * se.unit_price) FILTER (WHERE se.unit_price IS NOT NULL) AS revenue,
                       bool_or(se.unit_price IS NULL) AS price_missing
                FROM sales_entries se
                JOIN products p ON p.id = se.product_id
                WHERE se.shop_id = %s AND se.sale_date = %s
                GROUP BY p.name
                ORDER BY p.name
                """,
                (owner["shop_id"], today),
            )
            return evening_text(today, sales)
        summary = await insights_answer(conn, owner["shop_id"], "this week", today)
        return weekly_text(summary)
