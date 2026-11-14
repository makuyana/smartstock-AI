"""Intake specialist: drafts, YES / NO, and POPIA consent."""

from psycopg.types.json import Jsonb

from app.modules.records import write_record
from app.store import execute, fetchone


async def propose(conn, ctx, outcome) -> str:
    await execute(
        conn,
        """
        UPDATE pending_confirmations
        SET status = 'cancelled', resolved_at = now()
        WHERE shop_id = %s AND status = 'pending'
        """,
        (ctx.shop_id,),
    )
    await execute(
        conn,
        """
        INSERT INTO pending_confirmations
            (shop_id, kind, payload, read_back, source_message_id, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
        """,
        (ctx.shop_id, outcome.kind, Jsonb(outcome.as_payload()), outcome.read_back, ctx.message_id),
    )
    return outcome.read_back


async def confirm(conn, ctx) -> str:
    pending = await fetchone(
        conn,
        """
        SELECT id, payload, read_back
        FROM pending_confirmations
        WHERE shop_id = %s AND status = 'pending'
        ORDER BY created_at DESC
        LIMIT 1
        FOR UPDATE
        """,
        (ctx.shop_id,),
    )
    if pending is None:
        return "I don't have anything waiting to save. Tell me a sale, a purchase, or waste."
    payload = pending["payload"] or {}
    if payload.get("action") == "delete_shop_data":
        await execute(
            conn,
            """
            UPDATE pending_confirmations
            SET status = 'confirmed', resolved_at = now()
            WHERE id = %s AND shop_id = %s
            """,
            (pending["id"], ctx.shop_id),
        )
        ctx.delete_after_send = True
        return "I will delete this shop's records now. This cannot be undone."
    if not payload.get("confirm_allowed"):
        return pending["read_back"] or "I still need a clearer answer before I can save that."
    notes = []
    for record in payload.get("records") or []:
        notes.append(await write_record(conn, ctx, record))
    await execute(
        conn,
        """
        UPDATE pending_confirmations
        SET status = 'confirmed', resolved_at = now()
        WHERE id = %s AND shop_id = %s
        """,
        (pending["id"], ctx.shop_id),
    )
    return "Saved.\n" + "\n".join(f"• {note}" for note in notes)


async def cancel(conn, ctx) -> str:
    cursor = await conn.execute(
        """
        UPDATE pending_confirmations
        SET status = 'cancelled', resolved_at = now()
        WHERE shop_id = %s AND status = 'pending'
        """,
        (ctx.shop_id,),
    )
    if cursor.rowcount == 0:
        return "Nothing was waiting to save."
    return "Cancelled. I did not save it."


async def grant_consent(conn, user_id) -> None:
    await execute(
        conn,
        "UPDATE users SET consent_at = COALESCE(consent_at, now()) WHERE id = %s",
        (user_id,),
    )


async def previous_inbound(conn, shop_id, user_id, current_id) -> str | None:
    row = await fetchone(
        conn,
        """
        SELECT body FROM messages
        WHERE shop_id = %s AND user_id = %s AND direction = 'inbound' AND id <> %s
          AND body IS NOT NULL AND body <> ''
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (shop_id, user_id, current_id),
    )
    if row is None:
        return None
    return row["body"]
