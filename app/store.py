"""Parameterised SQL. Every shop query includes shop_id."""

from psycopg.types.json import Jsonb


async def fetchone(conn, sql, params=()):
    cursor = await conn.execute(sql, params)
    return await cursor.fetchone()


async def fetchall(conn, sql, params=()):
    cursor = await conn.execute(sql, params)
    return await cursor.fetchall()


async def execute(conn, sql, params=()):
    await conn.execute(sql, params)


async def claim_event(conn, channel: str, external_id: str) -> bool:
    row = await fetchone(
        conn,
        """
        INSERT INTO webhook_events (channel, external_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        RETURNING channel
        """,
        (channel, external_id),
    )
    return row is not None


async def insert_message(
    conn,
    *,
    shop_id,
    user_id,
    direction,
    channel,
    body,
    media_url,
    sent_at,
    raw,
    external_id,
):
    row = await fetchone(
        conn,
        """
        INSERT INTO messages
            (shop_id, user_id, direction, channel, body, media_url, sent_at, raw, external_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            shop_id,
            user_id,
            direction,
            channel,
            body,
            media_url,
            sent_at,
            Jsonb(raw) if raw is not None else None,
            external_id,
        ),
    )
    return row["id"]


async def load_catalog(conn, shop_id: str) -> tuple[list[dict], list[dict]]:
    products = await fetchall(
        conn,
        """
        SELECT id, name, unit, usual_price
        FROM products
        WHERE shop_id = %s AND is_active
        ORDER BY name
        """,
        (shop_id,),
    )
    ingredients = await fetchall(
        conn,
        """
        SELECT id, name, unit, stock_on_hand, reorder_level, safety_buffer,
               latest_unit_cost, avg_unit_cost, pack_size
        FROM ingredients
        WHERE shop_id = %s
        ORDER BY name
        """,
        (shop_id,),
    )
    return products, ingredients
