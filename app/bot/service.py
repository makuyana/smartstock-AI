"""Shop reads and writes for the bot API.

Every shop query includes shop_id. Writes go through the same record helpers
the WhatsApp YES path uses. Demo shops are read-only here: the bot API does
not change them. WhatsApp and the demo page are left alone.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from psycopg import Error
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
from psycopg.types.json import Jsonb

from app.modules.parsing import canonical_ingredient, canonical_product
from app.modules.records import write_record
from app.money import D, q2, q3, q4
from app.store import execute, fetchall, fetchone
from app.timeutil import JOHANNESBURG

logger = logging.getLogger("smartstock.bot")

SHOP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
BOT_REF = uuid.UUID("6f1c2c3e-7b1a-4d5e-9c3a-1a2b3c4d5e6f")

EXPENSE_CATEGORIES = {
    "utilities_electricity",
    "utilities_gas",
    "equipment_fridge",
    "equipment_appliances",
    "general_ops",
    "rent",
    "fuel",
    "other",
}
WASTE_REASONS = {"expired", "burnt", "spoiled", "unsold", "other"}
MOVEMENT_TYPES = {"purchase", "sale_deduction", "waste", "count_adjustment", "manual"}
RECORD_KINDS = {
    "sale",
    "purchase",
    "expense",
    "waste",
    "usage",
    "stock_count",
    "invoice",
    "set_price",
    "set_target",
}
DRAFT_KINDS = {"sale", "purchase", "expense", "waste", "invoice", "stock_count", "other"}
CONFIDENCE = {"high", "medium", "low"}
DAY_TYPES = {"busy", "normal", "quiet"}
PERIOD_TYPES = {"day", "week", "month"}
DRAFT_STATUSES = {"pending", "confirmed", "edited", "cancelled"}
MESSAGE_CHANNELS = {"whatsapp", "instagram", "demo", "bot", "all"}

LINKED_TABLE = {
    "sale": "sales_entries",
    "purchase": "purchases",
    "expense": "expenses",
    "waste": "waste",
    "invoice": "invoices",
    "stock_count": "stock_counts",
}

_SENSITIVE_PARTS = ("token", "secret", "password", "consent", "phone", "apikey", "credential")


class BotError(Exception):
    status = 500
    detail = "error"


class Unavailable(BotError):
    status = 503
    detail = "unavailable"


class Invalid(BotError):
    status = 422
    detail = "invalid"


class NotFound(BotError):
    status = 404
    detail = "not found"


class Forbidden(BotError):
    status = 403
    detail = "forbidden"


class Conflict(BotError):
    status = 409
    detail = "conflict"


def checked_shop_id(shop_id: str) -> str:
    if not SHOP_ID_RE.fullmatch(shop_id or ""):
        raise NotFound()
    return shop_id


def _pool(pool):
    if pool is None:
        raise Unavailable()
    return pool


def _sensitive(key: str) -> bool:
    lowered = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return any(part in lowered for part in _SENSITIVE_PARTS)


def sanitize(value):
    """Drop secrets and shrink a delete-my-data draft to its action name."""
    if isinstance(value, dict):
        if str(value.get("action") or "") == "delete_shop_data":
            return {"action": "delete_shop_data"}
        cleaned = {}
        for key, item in value.items():
            if _sensitive(key):
                continue
            cleaned[str(key)[:80]] = sanitize(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize(item) for item in value[:100]]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _num(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str):
        return value[:4000]
    return value


def _num(value) -> str | None:
    if value is None:
        return None
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _iso_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _iso_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _id(value):
    if value is None:
        return None
    return str(value)


def _flags(value) -> list:
    if not value:
        return []
    return [str(item) for item in value]


def _parse_limit(raw, default=50) -> int:
    if raw is None or raw == "":
        return default
    if isinstance(raw, str) and raw.isdigit():
        value = int(raw)
    else:
        raise Invalid()
    if value < 1 or value > 100:
        raise Invalid()
    return value


def _parse_offset(raw) -> int:
    if raw is None or raw == "":
        return 0
    if isinstance(raw, str) and raw.isdigit():
        value = int(raw)
    else:
        raise Invalid()
    if value > 10000:
        raise Invalid()
    return value


def _parse_day_param(raw) -> date | None:
    if raw is None or raw == "":
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw)):
        raise Invalid()
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise Invalid() from None


def _parse_dt_param(raw) -> datetime | None:
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    if len(text) > 40:
        raise Invalid()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise Invalid() from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JOHANNESBURG)
    return parsed


def page_from(params, date_names=("from", "to")) -> tuple[date | None, date | None, int, int]:
    start = _parse_day_param(params.get(date_names[0]))
    end = _parse_day_param(params.get(date_names[1]))
    if start and end and end < start:
        raise Invalid()
    return start, end, _parse_limit(params.get("limit")), _parse_offset(params.get("offset"))


def message_query(params) -> tuple[str | None, datetime | None, datetime | None, int, int]:
    channel = params.get("channel") or "whatsapp"
    if channel not in MESSAGE_CHANNELS:
        raise Invalid()
    since = _parse_dt_param(params.get("since"))
    until = _parse_dt_param(params.get("until"))
    if since and until and until <= since:
        raise Invalid()
    stored = None if channel == "all" else channel
    return stored, since, until, _parse_limit(params.get("limit")), _parse_offset(params.get("offset"))


def draft_query(params) -> tuple[str, int, int]:
    status = params.get("status") or "pending"
    if status not in DRAFT_STATUSES:
        raise Invalid()
    return status, _parse_limit(params.get("limit")), _parse_offset(params.get("offset"))


def idempotency_key(header: str | None, body: dict) -> str | None:
    raw_body = body.get("idempotency_key")
    if header not in (None, "") and raw_body not in (None, ""):
        if str(header) != str(raw_body):
            raise Invalid()
    raw = header if header not in (None, "") else raw_body
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or not KEY_RE.fullmatch(raw):
        raise Invalid()
    return raw


def _object(body) -> dict:
    if not isinstance(body, dict):
        raise Invalid()
    return body


def _same_shop(body: dict, shop_id: str) -> None:
    other = body.get("shop_id")
    if other is not None and str(other) != shop_id:
        raise Invalid()


def _plain_name(value) -> str:
    if not isinstance(value, str):
        raise Invalid()
    name = re.sub(r"\s+", " ", value).strip()
    if not name or len(name) > 80 or any(ord(ch) < 32 for ch in name):
        raise Invalid()
    return name


def _positive(value) -> Decimal:
    return parse_decimal(value, allow_zero=False)


def parse_decimal(value, *, allow_zero=True) -> Decimal:
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        raise Invalid()
    try:
        number = D(value.strip() if isinstance(value, str) else value)
    except (ArithmeticError, ValueError, TypeError):
        raise Invalid() from None
    if number is None or number >= Decimal("1000000000") or number < 0:
        raise Invalid()
    if not allow_zero and number == 0:
        raise Invalid()
    return number


def _signed(value) -> Decimal:
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        raise Invalid()
    try:
        number = D(value.strip() if isinstance(value, str) else value)
    except (ArithmeticError, ValueError, TypeError):
        raise Invalid() from None
    if number is None or number.copy_abs() >= Decimal("1000000000") or number == 0:
        raise Invalid()
    return q3(number)


def _hour(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise Invalid()
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or not 0 <= value <= 23:
        raise Invalid()
    return value


def _day_value(value, today: date) -> date:
    if value is None or value == "":
        return today
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_day_param(value) or today


def _uuid(value) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise Invalid() from None


def _short_text(value, limit=120) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise Invalid()
    text = re.sub(r"\s+", " ", value).strip()
    if not text or len(text) > limit or any(ord(ch) < 32 for ch in text):
        raise Invalid()
    return text


def clean_record(body: dict, today: date) -> dict:
    """Keep the fields write_record already accepts, with the same required ones."""
    _object(body)
    kind = body.get("kind")
    if kind not in RECORD_KINDS:
        raise Invalid()
    record: dict = {"kind": kind}
    names_an_item = kind in {"sale", "purchase", "waste", "usage", "stock_count", "set_price"} or (
        kind == "set_target" and body.get("item_name") not in (None, "")
    )
    if names_an_item:
        spoken = _plain_name(body.get("item_name"))
        unit = _short_text(body.get("unit"), 40)
        product_item = kind in {"sale", "set_price", "set_target"} or (
            kind == "waste" and body.get("item_kind") == "product"
        )
        if product_item:
            record["item_name"] = canonical_product(spoken) or spoken
            if kind in {"sale", "waste"}:
                record["unit"] = unit or "each"
        else:
            name, mapped_unit = _ingredient(spoken, unit)
            record["item_name"] = name
            record["unit"] = mapped_unit or unit or "each"
    if kind in {"sale", "purchase", "waste", "usage"}:
        record["quantity"] = format(q3(_positive(body.get("quantity"))), "f")
    if kind == "stock_count":
        record["quantity"] = format(q3(parse_decimal(body.get("quantity"), allow_zero=True)), "f")
    if kind == "sale":
        price = body.get("unit_price")
        if price not in (None, ""):
            record["unit_price"] = format(q2(parse_decimal(price)), "f")
        record["sale_hour"] = _hour(body.get("sale_hour"))
    if kind == "purchase":
        cost = body.get("unit_cost")
        price = body.get("unit_price")
        total = body.get("total")
        if cost not in (None, ""):
            record["unit_cost"] = format(q2(parse_decimal(cost)), "f")
        elif price not in (None, ""):
            record["unit_cost"] = format(q2(parse_decimal(price)), "f")
        if total not in (None, ""):
            record["total"] = format(q2(parse_decimal(total)), "f")
        if "unit_cost" not in record and "total" not in record:
            raise Invalid()
    if kind == "expense":
        record["amount"] = format(q2(parse_decimal(body.get("amount"))), "f")
        category = body.get("category")
        if category not in EXPENSE_CATEGORIES:
            raise Invalid()
        record["category"] = category
        record["spoken_category"] = _short_text(body.get("spoken_category") or body.get("note"), 80)
    if kind == "waste":
        reason = body.get("reason")
        if reason not in (None, ""):
            if reason not in WASTE_REASONS:
                raise Invalid()
            record["reason"] = reason
        item_kind = body.get("item_kind") or "ingredient"
        if item_kind not in {"ingredient", "product"}:
            raise Invalid()
        record["item_kind"] = item_kind
    if kind == "invoice":
        record["supplier"] = _short_text(body.get("supplier"), 120)
        for field in ("subtotal", "vat", "discount", "total"):
            raw = body.get(field)
            if raw not in (None, ""):
                record[field] = format(q2(parse_decimal(raw)), "f")
        if not any(field in record for field in ("subtotal", "vat", "total")):
            raise Invalid()
        record["lines"] = _invoice_lines(body.get("lines") or [])
    if kind == "set_price":
        record["unit_price"] = format(q2(parse_decimal(body.get("unit_price"))), "f")
        record["action"] = "set_price"
    if kind == "set_target":
        record["action"] = "set_target"
        period = body.get("period_type") or "day"
        if period not in PERIOD_TYPES:
            raise Invalid()
        record["period_type"] = period
        start = _day_value(body.get("period_start"), today)
        end = _day_value(body.get("period_end"), start)
        if end < start:
            raise Invalid()
        record["period_start"] = start.isoformat()
        record["period_end"] = end.isoformat()
        if body.get("quantity") not in (None, ""):
            record["quantity"] = format(q3(_positive(body.get("quantity"))), "f")
        if body.get("amount") not in (None, ""):
            record["amount"] = format(q2(parse_decimal(body.get("amount"))), "f")
        if "quantity" not in record and "amount" not in record:
            raise Invalid()
    if kind not in {"set_price", "set_target"}:
        record["record_date"] = _day_value(body.get("record_date"), today).isoformat()
    return record


def _ingredient(spoken: str, unit: str | None) -> tuple[str, str | None]:
    mapped = canonical_ingredient(spoken)
    if mapped:
        return mapped[0], unit or mapped[1]
    return spoken, unit


def _invoice_lines(lines) -> list[dict]:
    if not isinstance(lines, list) or len(lines) > 40:
        raise Invalid()
    cleaned = []
    for line in lines:
        if not isinstance(line, dict):
            raise Invalid()
        item = line.get("item_name") or line.get("description")
        row = {"item_name": _plain_name(item) if item else None}
        if line.get("quantity") not in (None, ""):
            row["quantity"] = format(q3(_positive(line.get("quantity"))), "f")
        unit = _short_text(line.get("unit"), 40)
        if unit:
            row["unit"] = unit
        if line.get("unit_price") not in (None, ""):
            row["unit_price"] = format(q2(parse_decimal(line.get("unit_price"))), "f")
        if line.get("line_total") not in (None, ""):
            row["line_total"] = format(q2(parse_decimal(line.get("line_total"))), "f")
        confidence = line.get("confidence")
        if confidence not in (None, ""):
            if confidence not in CONFIDENCE:
                raise Invalid()
            row["confidence"] = confidence
        cleaned.append(row)
    return cleaned


def _shop_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "business_type": row["business_type"],
        "timezone": row["timezone"],
        "currency": row["currency"],
        "is_demo": bool(row["is_demo"]),
        "demo_as_of": _iso_date(row.get("demo_as_of")),
        "created_at": _iso_dt(row.get("created_at")),
    }


def _product_out(row: dict) -> dict:
    return {
        "id": _id(row["id"]),
        "name": row["name"],
        "unit": row["unit"],
        "usual_price": _num(row.get("usual_price")),
        "is_active": bool(row.get("is_active", True)),
    }


async def _shop_row(conn, shop_id: str) -> dict:
    row = await fetchone(
        conn,
        """
        SELECT id, name, business_type, timezone, currency, is_demo, demo_as_of, created_at
        FROM shops
        WHERE id = %s
        """,
        (shop_id,),
    )
    if row is None:
        raise NotFound()
    return row


def _demo(shop_id: str, row: dict) -> bool:
    return shop_id.startswith("demo-") or bool(row.get("is_demo"))


async def list_shops(pool, limit: int, offset: int) -> list[dict]:
    async with _pool(pool).connection() as conn:
        rows = await fetchall(
            conn,
            """
            SELECT id, name, business_type, timezone, currency, is_demo, demo_as_of, created_at
            FROM shops
            ORDER BY name
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
    return [_shop_out(row) for row in rows]


async def shop_profile(pool, shop_id: str) -> dict:
    checked_shop_id(shop_id)
    async with _pool(pool).connection() as conn:
        shop = await _shop_row(conn, shop_id)
        products = await fetchall(
            conn,
            """
            SELECT id, name, unit, usual_price, is_active
            FROM products
            WHERE shop_id = %s AND is_active
            ORDER BY name
            LIMIT 500
            """,
            (shop_id,),
        )
    return {"shop": _shop_out(shop), "products": [_product_out(row) for row in products]}


async def list_messages(pool, shop_id, channel, since, until, limit, offset) -> list[dict]:
    checked_shop_id(shop_id)
    async with _pool(pool).connection() as conn:
        await _shop_row(conn, shop_id)
        rows = await fetchall(
            conn,
            """
            SELECT id, user_id, direction, channel, body, sent_at
            FROM messages
            WHERE shop_id = %s
              AND (%s::text IS NULL OR channel = %s)
              AND (%s::timestamptz IS NULL OR sent_at >= %s)
              AND (%s::timestamptz IS NULL OR sent_at < %s)
            ORDER BY sent_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (shop_id, channel, channel, since, since, until, until, limit, offset),
        )
    return [
        {
            "id": _id(row["id"]),
            "user_id": _id(row.get("user_id")),
            "direction": row["direction"],
            "channel": row["channel"],
            "body": row.get("body"),
            "sent_at": _iso_dt(row.get("sent_at")),
        }
        for row in rows
    ]


async def list_sales(pool, shop_id, start, end, limit, offset) -> list[dict]:
    checked_shop_id(shop_id)
    async with _pool(pool).connection() as conn:
        await _shop_row(conn, shop_id)
        rows = await fetchall(
            conn,
            """
            SELECT se.id, se.sale_date, se.product_id, p.name AS product_name,
                   se.quantity, se.unit_price, se.sale_hour, se.created_at
            FROM sales_entries se
            JOIN products p ON p.id = se.product_id AND p.shop_id = se.shop_id
            WHERE se.shop_id = %s
              AND (%s::date IS NULL OR se.sale_date >= %s)
              AND (%s::date IS NULL OR se.sale_date <= %s)
            ORDER BY se.sale_date DESC, se.created_at DESC
            LIMIT %s OFFSET %s
            """,
            (shop_id, start, start, end, end, limit, offset),
        )
    return [
        {
            "id": _id(row["id"]),
            "sale_date": _iso_date(row["sale_date"]),
            "product_id": _id(row["product_id"]),
            "product_name": row["product_name"],
            "quantity": _num(row["quantity"]),
            "unit_price": _num(row.get("unit_price")),
            "sale_hour": row.get("sale_hour"),
            "created_at": _iso_dt(row.get("created_at")),
        }
        for row in rows
    ]


async def list_stock(pool, shop_id, limit, offset) -> list[dict]:
    checked_shop_id(shop_id)
    async with _pool(pool).connection() as conn:
        await _shop_row(conn, shop_id)
        rows = await fetchall(
            conn,
            """
            SELECT id, name, unit, stock_on_hand, reorder_level, safety_buffer,
                   latest_unit_cost, avg_unit_cost, pack_size
            FROM ingredients
            WHERE shop_id = %s
            ORDER BY name
            LIMIT %s OFFSET %s
            """,
            (shop_id, limit, offset),
        )
    return [
        {
            "id": _id(row["id"]),
            "name": row["name"],
            "unit": row["unit"],
            "stock_on_hand": _num(row.get("stock_on_hand")),
            "reorder_level": _num(row.get("reorder_level")),
            "safety_buffer": _num(row.get("safety_buffer")),
            "latest_unit_cost": _num(row.get("latest_unit_cost")),
            "avg_unit_cost": _num(row.get("avg_unit_cost")),
            "pack_size": _num(row.get("pack_size")),
        }
        for row in rows
    ]


async def list_movements(pool, shop_id, start, end, limit, offset) -> list[dict]:
    checked_shop_id(shop_id)
    async with _pool(pool).connection() as conn:
        await _shop_row(conn, shop_id)
        rows = await fetchall(
            conn,
            """
            SELECT m.id, m.ingredient_id, i.name AS ingredient_name, m.movement_date,
                   m.movement_type, m.qty_change, m.unit_cost, m.stock_after, m.created_at
            FROM stock_movements m
            JOIN ingredients i ON i.id = m.ingredient_id AND i.shop_id = m.shop_id
            WHERE m.shop_id = %s
              AND (%s::date IS NULL OR m.movement_date >= %s)
              AND (%s::date IS NULL OR m.movement_date <= %s)
            ORDER BY m.movement_date DESC, m.created_at DESC
            LIMIT %s OFFSET %s
            """,
            (shop_id, start, start, end, end, limit, offset),
        )
    return [_movement_out(row) for row in rows]


def _movement_out(row: dict) -> dict:
    return {
        "id": _id(row["id"]),
        "ingredient_id": _id(row["ingredient_id"]),
        "ingredient_name": row.get("ingredient_name"),
        "movement_date": _iso_date(row.get("movement_date")),
        "movement_type": row.get("movement_type"),
        "qty_change": _num(row.get("qty_change")),
        "unit_cost": _num(row.get("unit_cost")),
        "stock_after": _num(row.get("stock_after")),
        "created_at": _iso_dt(row.get("created_at")),
    }


async def list_revenue(pool, shop_id, start, end, limit, offset) -> list[dict]:
    checked_shop_id(shop_id)
    async with _pool(pool).connection() as conn:
        await _shop_row(conn, shop_id)
        rows = await fetchall(
            conn,
            """
            SELECT id, revenue_date, amount
            FROM daily_revenue
            WHERE shop_id = %s
              AND (%s::date IS NULL OR revenue_date >= %s)
              AND (%s::date IS NULL OR revenue_date <= %s)
            ORDER BY revenue_date DESC
            LIMIT %s OFFSET %s
            """,
            (shop_id, start, start, end, end, limit, offset),
        )
    return [
        {
            "id": _id(row["id"]),
            "revenue_date": _iso_date(row["revenue_date"]),
            "amount": _num(row["amount"]),
        }
        for row in rows
    ]


async def list_invoices(pool, shop_id, start, end, limit, offset) -> list[dict]:
    checked_shop_id(shop_id)
    async with _pool(pool).connection() as conn:
        await _shop_row(conn, shop_id)
        rows = await fetchall(
            conn,
            """
            SELECT id, supplier, invoice_number, invoice_date, subtotal, vat, discount, total,
                   confirmed, delivery_status, flags, created_at
            FROM invoices
            WHERE shop_id = %s
              AND (%s::date IS NULL OR invoice_date >= %s)
              AND (%s::date IS NULL OR invoice_date <= %s)
            ORDER BY invoice_date DESC NULLS LAST, created_at DESC
            LIMIT %s OFFSET %s
            """,
            (shop_id, start, start, end, end, limit, offset),
        )
        lines = []
        if rows:
            lines = await fetchall(
                conn,
                """
                SELECT id, invoice_id, line_no, description, quantity, unit, unit_price,
                       line_total, confidence, confirmed
                FROM invoice_lines
                WHERE shop_id = %s AND invoice_id = ANY(%s::uuid[])
                ORDER BY invoice_id, line_no
                """,
                (shop_id, [str(row["id"]) for row in rows]),
            )
    grouped: dict[str, list] = {}
    for line in lines:
        grouped.setdefault(str(line["invoice_id"]), []).append(
            {
                "id": _id(line["id"]),
                "line_no": line.get("line_no"),
                "description": line.get("description"),
                "quantity": _num(line.get("quantity")),
                "unit": line.get("unit"),
                "unit_price": _num(line.get("unit_price")),
                "line_total": _num(line.get("line_total")),
                "confidence": line.get("confidence"),
                "confirmed": bool(line.get("confirmed")),
            }
        )
    invoices = []
    for row in rows:
        invoices.append(
            {
                "id": _id(row["id"]),
                "supplier": row.get("supplier"),
                "invoice_number": row.get("invoice_number"),
                "invoice_date": _iso_date(row.get("invoice_date")),
                "subtotal": _num(row.get("subtotal")),
                "vat": _num(row.get("vat")),
                "discount": _num(row.get("discount")),
                "total": _num(row.get("total")),
                "confirmed": bool(row.get("confirmed")),
                "delivery_status": row.get("delivery_status"),
                "flags": _flags(row.get("flags")),
                "created_at": _iso_dt(row.get("created_at")),
                "lines": grouped.get(str(row["id"]), []),
            }
        )
    return invoices


async def list_drafts(pool, shop_id, status, limit, offset) -> list[dict]:
    checked_shop_id(shop_id)
    async with _pool(pool).connection() as conn:
        await _shop_row(conn, shop_id)
        rows = await fetchall(
            conn,
            """
            SELECT id, kind, status, read_back, payload, created_at
            FROM pending_confirmations
            WHERE shop_id = %s AND status = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (shop_id, status, limit, offset),
        )
    return [
        {
            "id": _id(row["id"]),
            "kind": row["kind"],
            "status": row["status"],
            "read_back": (row.get("read_back") or "")[:4000] or None,
            "payload": sanitize(row.get("payload") or {}),
            "created_at": _iso_dt(row.get("created_at")),
        }
        for row in rows
    ]


async def list_forecasts(pool, shop_id, start, end, limit, offset) -> list[dict]:
    checked_shop_id(shop_id)
    async with _pool(pool).connection() as conn:
        await _shop_row(conn, shop_id)
        rows = await fetchall(
            conn,
            """
            SELECT f.id, f.target_date, f.product_id, p.name AS product_name,
                   f.predicted_qty, f.range_low, f.range_high, f.method, f.confidence,
                   f.reason, f.expected_revenue, f.is_early_estimate, f.day_type,
                   f.adjustments, f.created_at
            FROM forecasts f
            JOIN products p ON p.id = f.product_id AND p.shop_id = f.shop_id
            WHERE f.shop_id = %s
              AND (%s::date IS NULL OR f.target_date >= %s)
              AND (%s::date IS NULL OR f.target_date <= %s)
            ORDER BY f.target_date DESC, p.name
            LIMIT %s OFFSET %s
            """,
            (shop_id, start, start, end, end, limit, offset),
        )
    return [_forecast_out(row) for row in rows]


def _forecast_out(row: dict) -> dict:
    return {
        "id": _id(row.get("id")),
        "target_date": _iso_date(row.get("target_date")),
        "product_id": _id(row.get("product_id")),
        "product_name": row.get("product_name"),
        "predicted_qty": _num(row.get("predicted_qty")),
        "range_low": _num(row.get("range_low")),
        "range_high": _num(row.get("range_high")),
        "method": row.get("method"),
        "confidence": row.get("confidence"),
        "reason": row.get("reason"),
        "expected_revenue": _num(row.get("expected_revenue")),
        "is_early_estimate": bool(row.get("is_early_estimate")),
        "day_type": row.get("day_type"),
        "adjustments": _flags(row.get("adjustments")),
        "created_at": _iso_dt(row.get("created_at")),
    }


async def _writable(conn, shop_id: str) -> dict:
    shop = await _shop_row(conn, shop_id)
    if _demo(shop_id, shop):
        raise Forbidden()
    return shop


async def save_record(pool, shop_id: str, body: dict, key: str | None, today: date) -> dict:
    checked_shop_id(shop_id)
    _same_shop(body, shop_id)
    record = clean_record(body, today)
    async with _pool(pool).connection() as conn:
        async with conn.transaction():
            await _writable(conn, shop_id)
            try:
                return await _save_record(conn, shop_id, record, key, today)
            except BotError:
                raise
            except (CheckViolation, ForeignKeyViolation, ArithmeticError, KeyError, TypeError, ValueError):
                logger.info("bot api rejected a %s record", record.get("kind"))
                raise Invalid() from None
            except UniqueViolation:
                raise Conflict() from None
            except Error:
                logger.exception("bot api record write failed")
                raise BotError() from None


async def _save_record(conn, shop_id: str, record: dict, key: str | None, today: date) -> dict:
    kind = record["kind"]
    message_id = None
    replay = False
    if key:
        external_id = f"{shop_id}:{kind}:{key}"
        existing = await fetchone(
            conn,
            """
            SELECT id, shop_id, body
            FROM messages
            WHERE channel = 'bot' AND direction = 'inbound' AND external_id = %s
            """,
            (external_id,),
        )
        if existing:
            if existing["shop_id"] != shop_id:
                raise Conflict()
            replay = True
            message_id = existing["id"]
            if await _linked(conn, shop_id, message_id, kind):
                return {"ok": True, "idempotent": True, "kind": kind}
        else:
            created = await fetchone(
                conn,
                """
                INSERT INTO messages
                    (shop_id, direction, channel, body, sent_at, external_id)
                VALUES (%s, 'inbound', 'bot', NULL, now(), %s)
                ON CONFLICT (channel, direction, external_id) WHERE external_id IS NOT NULL
                DO NOTHING
                RETURNING id
                """,
                (shop_id, external_id),
            )
            if created is None:
                raise Conflict()
            message_id = created["id"]
    if kind == "set_target" and await _target_exists(conn, shop_id, record):
        if replay:
            return {"ok": True, "idempotent": True, "kind": kind}
        raise Conflict()
    ctx = SimpleNamespace(shop_id=shop_id, message_id=message_id, today=today)
    note = await write_record(conn, ctx, record)
    if message_id is not None:
        await execute(
            conn,
            "UPDATE messages SET body = %s WHERE id = %s AND shop_id = %s",
            ("saved", message_id, shop_id),
        )
    return {"ok": True, "idempotent": False, "kind": kind, "note": note}


async def _linked(conn, shop_id, message_id, kind: str) -> bool:
    table = LINKED_TABLE.get(kind)
    if table is None:
        row = await fetchone(
            conn,
            "SELECT body FROM messages WHERE id = %s AND shop_id = %s",
            (message_id, shop_id),
        )
        return bool(row and row.get("body") == "saved")
    row = await fetchone(
        conn,
        f"SELECT 1 AS ok FROM {table} WHERE shop_id = %s AND source_message_id = %s LIMIT 1",
        (shop_id, message_id),
    )
    return row is not None


async def _target_exists(conn, shop_id, record: dict) -> bool:
    product_id = None
    if record.get("item_name"):
        product = await fetchone(
            conn,
            "SELECT id FROM products WHERE shop_id = %s AND name = %s",
            (shop_id, record["item_name"]),
        )
        if product is None:
            return False
        product_id = product["id"]
    row = await fetchone(
        conn,
        """
        SELECT 1 AS ok FROM sales_targets
        WHERE shop_id = %s AND period_type = %s AND period_start = %s
          AND product_id IS NOT DISTINCT FROM %s
        LIMIT 1
        """,
        (shop_id, record.get("period_type") or "day", record.get("period_start"), product_id),
    )
    return row is not None


async def save_movement(pool, shop_id: str, body: dict, key: str | None, today: date) -> dict:
    checked_shop_id(shop_id)
    _same_shop(body, shop_id)
    movement_type = body.get("movement_type")
    if movement_type not in MOVEMENT_TYPES:
        raise Invalid()
    qty_change = _signed(body.get("qty_change"))
    if movement_type == "purchase" and qty_change <= 0:
        raise Invalid()
    if movement_type in {"sale_deduction", "waste"} and qty_change >= 0:
        raise Invalid()
    movement_date = _day_value(body.get("movement_date"), today)
    unit_cost = None
    if body.get("unit_cost") not in (None, ""):
        unit_cost = q4(parse_decimal(body.get("unit_cost")))
    ingredient_id = body.get("ingredient_id")
    item_name = body.get("item_name")
    if ingredient_id:
        ingredient_id = _uuid(ingredient_id)
    elif item_name:
        item_name = _plain_name(item_name)
        mapped = canonical_ingredient(item_name)
        if mapped:
            item_name = mapped[0]
    else:
        raise Invalid()
    async with _pool(pool).connection() as conn:
        async with conn.transaction():
            await _writable(conn, shop_id)
            try:
                return await _save_movement(
                    conn,
                    shop_id,
                    ingredient_id,
                    item_name,
                    movement_type,
                    qty_change,
                    movement_date,
                    unit_cost,
                    key,
                )
            except BotError:
                raise
            except (CheckViolation, ForeignKeyViolation):
                raise Invalid() from None
            except UniqueViolation:
                raise Conflict() from None
            except Error:
                logger.exception("bot api movement write failed")
                raise BotError() from None


async def _save_movement(
    conn,
    shop_id,
    ingredient_id,
    item_name,
    movement_type,
    qty_change,
    movement_date,
    unit_cost,
    key,
) -> dict:
    if ingredient_id:
        ingredient = await fetchone(
            conn,
            """
            SELECT id, name, unit, stock_on_hand
            FROM ingredients
            WHERE shop_id = %s AND id = %s
            FOR UPDATE
            """,
            (shop_id, ingredient_id),
        )
    else:
        ingredient = await fetchone(
            conn,
            """
            SELECT id, name, unit, stock_on_hand
            FROM ingredients
            WHERE shop_id = %s AND name = %s
            FOR UPDATE
            """,
            (shop_id, item_name),
        )
    if ingredient is None:
        raise NotFound()
    if item_name and ingredient_id and ingredient["name"] != item_name:
        raise Invalid()
    ref_id = None
    if key:
        ref_id = uuid.uuid5(BOT_REF, f"{shop_id}:{key}:{ingredient['id']}")
        existing = await fetchone(
            conn,
            """
            SELECT id, ingredient_id, movement_date, movement_type, qty_change, unit_cost, stock_after
            FROM stock_movements
            WHERE shop_id = %s AND ref_table = 'bot' AND ref_id = %s AND ingredient_id = %s
            """,
            (shop_id, ref_id, ingredient["id"]),
        )
        if existing:
            existing["ingredient_name"] = ingredient["name"]
            return {"ok": True, "idempotent": True, "movement": _movement_out(existing)}
    else:
        ref_id = uuid.uuid4()
    stock = ingredient["stock_on_hand"]
    stock_after = None
    if stock is not None:
        stock_after = q3(stock + qty_change)
        if stock_after < 0:
            raise Invalid()
        await execute(
            conn,
            """
            UPDATE ingredients
            SET stock_on_hand = %s, updated_at = now()
            WHERE id = %s AND shop_id = %s
            """,
            (stock_after, ingredient["id"], shop_id),
        )
    row = await fetchone(
        conn,
        """
        INSERT INTO stock_movements
            (shop_id, ingredient_id, movement_date, movement_type, qty_change,
             unit_cost, ref_table, ref_id, stock_after)
        VALUES (%s, %s, %s, %s, %s, %s, 'bot', %s, %s)
        RETURNING id, ingredient_id, movement_date, movement_type, qty_change, unit_cost, stock_after
        """,
        (
            shop_id,
            ingredient["id"],
            movement_date,
            movement_type,
            qty_change,
            unit_cost,
            ref_id,
            stock_after,
        ),
    )
    row["ingredient_name"] = ingredient["name"]
    return {"ok": True, "idempotent": False, "movement": _movement_out(row)}


async def save_forecast(pool, shop_id: str, body: dict) -> dict:
    checked_shop_id(shop_id)
    _same_shop(body, shop_id)
    target = _parse_day_param(body.get("target_date"))
    if target is None:
        raise Invalid()
    predicted = q3(parse_decimal(body.get("predicted_qty"), allow_zero=True))
    low = body.get("range_low")
    high = body.get("range_high")
    range_low = q3(parse_decimal(low, allow_zero=True)) if low not in (None, "") else None
    range_high = q3(parse_decimal(high, allow_zero=True)) if high not in (None, "") else None
    if range_low is not None and range_high is not None and range_low > range_high:
        raise Invalid()
    method = body.get("method")
    if not isinstance(method, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,40}", method):
        raise Invalid()
    confidence = body.get("confidence")
    if confidence not in (None, "") and confidence not in CONFIDENCE:
        raise Invalid()
    if confidence == "":
        confidence = None
    day_type = body.get("day_type")
    if day_type not in (None, "") and day_type not in DAY_TYPES:
        raise Invalid()
    if day_type == "":
        day_type = None
    reason = _short_text(body.get("reason"), 500)
    expected = body.get("expected_revenue")
    expected_revenue = q2(parse_decimal(expected)) if expected not in (None, "") else None
    early = body.get("is_early_estimate", False)
    if not isinstance(early, bool):
        raise Invalid()
    adjustments = body.get("adjustments") or []
    if not isinstance(adjustments, list) or len(adjustments) > 8:
        raise Invalid()
    clean_adjustments = []
    for item in adjustments:
        text = _short_text(item, 40)
        if text:
            clean_adjustments.append(text)
    product_id = body.get("product_id")
    product_name = body.get("product_name") or body.get("item_name")
    if product_id:
        product_id = _uuid(product_id)
    elif product_name:
        product_name = canonical_product(_plain_name(product_name)) or _plain_name(product_name)
    else:
        raise Invalid()
    async with _pool(pool).connection() as conn:
        async with conn.transaction():
            await _writable(conn, shop_id)
            if product_id:
                product = await fetchone(
                    conn,
                    """
                    SELECT id, name FROM products
                    WHERE shop_id = %s AND id = %s
                    """,
                    (shop_id, product_id),
                )
            else:
                product = await fetchone(
                    conn,
                    """
                    SELECT id, name FROM products
                    WHERE shop_id = %s AND name = %s
                    """,
                    (shop_id, product_name),
                )
            if product is None:
                raise NotFound()
            if product_name and product_id and product["name"] != product_name:
                raise Invalid()
            try:
                row = await fetchone(
                    conn,
                    """
                    INSERT INTO forecasts (
                        shop_id, target_date, product_id, predicted_qty, range_low, range_high,
                        method, confidence, reason, expected_revenue, is_early_estimate,
                        day_type, adjustments
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (shop_id, target_date, product_id, method)
                    DO UPDATE SET
                        predicted_qty = EXCLUDED.predicted_qty,
                        range_low = EXCLUDED.range_low,
                        range_high = EXCLUDED.range_high,
                        confidence = EXCLUDED.confidence,
                        reason = EXCLUDED.reason,
                        expected_revenue = EXCLUDED.expected_revenue,
                        is_early_estimate = EXCLUDED.is_early_estimate,
                        day_type = EXCLUDED.day_type,
                        adjustments = EXCLUDED.adjustments
                    RETURNING id, target_date, product_id, predicted_qty, range_low, range_high,
                              method, confidence, reason, expected_revenue, is_early_estimate,
                              day_type, adjustments
                    """,
                    (
                        shop_id,
                        target,
                        product["id"],
                        predicted,
                        range_low,
                        range_high,
                        method,
                        confidence,
                        reason,
                        expected_revenue,
                        early,
                        day_type,
                        clean_adjustments,
                    ),
                )
            except (CheckViolation, ForeignKeyViolation):
                raise Invalid() from None
            except Error:
                logger.exception("bot api forecast write failed")
                raise BotError() from None
    row["product_name"] = product["name"]
    return {"ok": True, "forecast": _forecast_out(row)}


def _contains_delete(value) -> bool:
    if isinstance(value, dict):
        if str(value.get("action") or "") == "delete_shop_data":
            return True
        return any(_contains_delete(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_delete(item) for item in value)
    return False


async def save_draft(pool, shop_id: str, body: dict, key: str | None) -> dict:
    checked_shop_id(shop_id)
    _same_shop(body, shop_id)
    kind = body.get("kind")
    if kind not in DRAFT_KINDS:
        raise Invalid()
    payload = body.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict) or _contains_delete(payload) or _contains_delete(body):
        raise Invalid()
    read_back = _short_text(body.get("read_back"), 4000)
    stored = sanitize(payload)
    if not isinstance(stored, dict):
        raise Invalid()
    if key:
        stored["bot_idempotency_key"] = key
    async with _pool(pool).connection() as conn:
        async with conn.transaction():
            await _writable(conn, shop_id)
            if key:
                existing = await fetchone(
                    conn,
                    """
                    SELECT id, kind, status, read_back, payload, created_at
                    FROM pending_confirmations
                    WHERE shop_id = %s AND status = 'pending'
                      AND payload->>'bot_idempotency_key' = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (shop_id, key),
                )
                if existing:
                    return {"ok": True, "idempotent": True, "draft": _draft_out(existing)}
            try:
                row = await fetchone(
                    conn,
                    """
                    INSERT INTO pending_confirmations
                        (shop_id, kind, payload, read_back, status)
                    VALUES (%s, %s, %s, %s, 'pending')
                    RETURNING id, kind, status, read_back, payload, created_at
                    """,
                    (shop_id, kind, Jsonb(stored), read_back),
                )
            except (CheckViolation, ForeignKeyViolation):
                raise Invalid() from None
            except Error:
                logger.exception("bot api draft write failed")
                raise BotError() from None
    return {"ok": True, "idempotent": False, "draft": _draft_out(row)}


def _draft_out(row: dict) -> dict:
    return {
        "id": _id(row["id"]),
        "kind": row["kind"],
        "status": row["status"],
        "read_back": row.get("read_back"),
        "payload": sanitize(row.get("payload") or {}),
        "created_at": _iso_dt(row.get("created_at")),
    }
