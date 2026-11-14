"""Inventory specialist: recipe deductions, weighted cost, low stock, shopping list."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_UP

from app.money import fmt_qty, q3, q4


@dataclass(frozen=True)
class DeductionPlan:
    action: str
    new_stock: Decimal | None
    qty_change: Decimal | None
    shortfall: Decimal | None


def plan_deduction(stock: Decimal | None, need: Decimal) -> DeductionPlan:
    if stock is None:
        return DeductionPlan("skip_unknown", None, None, None)
    if need <= 0:
        raise ValueError("need must be positive")
    if need > stock:
        shortfall = need - stock
        change = -stock if stock > 0 else Decimal("0")
        return DeductionPlan("short", Decimal("0"), change, shortfall)
    return DeductionPlan("deduct", stock - need, -need, Decimal("0"))


def new_average_cost(old_avg, old_stock, unit_cost, qty):
    """Weighted average only when both the old average and the old stock are known."""
    if unit_cost is None or qty is None or qty <= 0:
        return old_avg
    if old_avg is None:
        return unit_cost
    if old_stock is None:
        return old_avg
    total = old_stock + qty
    if total <= 0:
        return unit_cost
    return (old_avg * old_stock + unit_cost * qty) / total


def is_low_stock(stock, reorder, safety) -> bool | None:
    if stock is None:
        return None
    bars = [bar for bar in (reorder, safety) if bar is not None]
    if not bars:
        return None
    return stock <= max(bars)


def buy_qty(stock, reorder, pack_size) -> Decimal | None:
    if stock is None or reorder is None:
        return None
    gap = reorder - stock
    if gap <= 0:
        return None
    if pack_size is not None and pack_size > 0:
        packs = (gap / pack_size).to_integral_value(rounding=ROUND_UP)
        return packs * pack_size
    return gap


def render_stock(rows: list[dict], expiring: list[dict] | None = None) -> str:
    if not rows:
        return "This shop has no stock items yet. Tell me what you bought, or send a stock count."
    lines = ["Stock on hand:"]
    unknown = []
    for row in rows:
        if row.get("stock") is None:
            unknown.append(row["name"])
            continue
        low = " low" if row.get("is_low") else ""
        lines.append(f"• {row['name']}: {fmt_qty(row['stock'])} {row['unit']}{low}")
    if unknown:
        lines.append("No count yet for: " + ", ".join(unknown) + ".")
    if not any(row.get("stock") is not None for row in rows):
        lines = [
            "I don't have stock counts yet, so I can't say what is low. "
            "Send one like: counted 10 bread."
        ]
    for item in expiring or []:
        lines.append(
            f"• {item['name']}: {fmt_qty(item['qty'])} {item['unit']} expires on {item['expiry_date']}."
        )
    return "\n".join(lines)


def render_shopping(rows: list[dict]) -> str:
    known = [row for row in rows if row.get("stock") is not None]
    unknown = [row["name"] for row in rows if row.get("stock") is None]
    if not known:
        return (
            "I don't have stock counts yet, so I can't build a shopping list. "
            "Send a stock count, for example: counted 10 bread."
        )
    low = [row for row in known if row.get("is_low")]
    if not low:
        text = "Nothing is at or below its reorder level."
        if unknown:
            text += " I still don't have counts for: " + ", ".join(unknown) + "."
        return text
    lines = ["Shopping list:"]
    for row in low:
        if row.get("buy") is not None:
            level = row.get("reorder") if row.get("reorder") is not None else row.get("safety")
            lines.append(
                f"• {row['name']}: buy {fmt_qty(row['buy'])} {row['unit']} "
                f"(you have {fmt_qty(row['stock'])}, reorder at {fmt_qty(level)})"
            )
        else:
            lines.append(
                f"• {row['name']}: low, you have {fmt_qty(row['stock'])} {row['unit']}. "
                "I don't have a reorder level, so I won't guess a quantity."
            )
    if unknown:
        lines.append("No count yet for: " + ", ".join(unknown) + ".")
    return "\n".join(lines)


def shopping_rows(ingredients: list[dict]) -> list[dict]:
    rows = []
    for item in ingredients:
        stock = item.get("stock_on_hand")
        reorder = item.get("reorder_level")
        safety = item.get("safety_buffer")
        low = is_low_stock(stock, reorder, safety)
        rows.append(
            {
                "name": item["name"],
                "unit": item.get("unit") or "",
                "stock": stock,
                "reorder": reorder,
                "safety": safety,
                "is_low": bool(low) if low is not None else False,
                "buy": buy_qty(stock, reorder if reorder is not None else safety, item.get("pack_size")),
            }
        )
    return rows


async def fetch_ingredients(conn, shop_id: str) -> list[dict]:
    from app.store import fetchall

    return await fetchall(
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


async def answer_stock(conn, shop_id: str, text: str, today: date | None = None) -> str:
    from app.store import fetchall

    ingredients = await fetch_ingredients(conn, shop_id)
    rows = shopping_rows(ingredients)
    expiring = await fetchall(
        conn,
        """
        SELECT i.name, i.unit, p.qty_remaining AS qty, p.expiry_date
        FROM purchases p
        JOIN ingredients i ON i.id = p.ingredient_id AND i.shop_id = p.shop_id
        WHERE p.shop_id = %s
          AND p.qty_remaining IS NOT NULL
          AND p.qty_remaining > 0
          AND p.expiry_date IS NOT NULL
          AND p.expiry_date <= %s
        ORDER BY p.expiry_date, i.name
        """,
        (shop_id, (today or date.today()) + timedelta(days=2)),
    )
    lowered = text.lower()
    if any(word in lowered for word in ("shop", "buy", "need", "list")):
        return render_shopping(rows)
    return render_stock(rows, expiring)


async def apply_receipt(conn, shop_id, ingredient_id, qty, unit_cost, movement_date, ref_table, ref_id) -> str:
    """Add stock from a confirmed purchase or invoice line."""
    from app.store import fetchone, execute

    row = await fetchone(
        conn,
        """
        SELECT id, name, unit, stock_on_hand, reorder_level, safety_buffer,
               avg_unit_cost, latest_unit_cost
        FROM ingredients
        WHERE id = %s AND shop_id = %s
        FOR UPDATE
        """,
        (ingredient_id, shop_id),
    )
    if row is None:
        return "I could not find that stock item."
    already = await fetchone(
        conn,
        """
        SELECT 1 FROM stock_movements
        WHERE ref_table = %s AND ref_id = %s AND ingredient_id = %s
        """,
        (ref_table, ref_id, ingredient_id),
    )
    avg = new_average_cost(row["avg_unit_cost"], row["stock_on_hand"], unit_cost, qty)
    if unit_cost is not None:
        await execute(
            conn,
            """
            UPDATE ingredients
            SET latest_unit_cost = %s, avg_unit_cost = %s, updated_at = now()
            WHERE id = %s AND shop_id = %s
            """,
            (q2_or_none(unit_cost), q4(avg) if avg is not None else None, ingredient_id, shop_id),
        )
    if already:
        return f"{row['name']} was already updated for this record."
    if row["stock_on_hand"] is None:
        await execute(
            conn,
            """
            INSERT INTO stock_movements
                (shop_id, ingredient_id, movement_date, movement_type, qty_change, unit_cost, ref_table, ref_id, stock_after)
            VALUES (%s, %s, %s, 'purchase', %s, %s, %s, %s, NULL)
            """,
            (shop_id, ingredient_id, movement_date, q3(qty), _cost(unit_cost), ref_table, ref_id),
        )
        return (
            f"Saved {fmt_qty(qty)} {row['unit']} {row['name']}. "
            "Stock on hand is still unknown until you send a count."
        )
    new_stock = q3(row["stock_on_hand"] + qty)
    await execute(
        conn,
        "UPDATE ingredients SET stock_on_hand = %s, updated_at = now() WHERE id = %s AND shop_id = %s",
        (new_stock, ingredient_id, shop_id),
    )
    await execute(
        conn,
        """
        INSERT INTO stock_movements
            (shop_id, ingredient_id, movement_date, movement_type, qty_change, unit_cost, ref_table, ref_id, stock_after)
        VALUES (%s, %s, %s, 'purchase', %s, %s, %s, %s, %s)
        """,
        (shop_id, ingredient_id, movement_date, q3(qty), _cost(unit_cost), ref_table, ref_id, new_stock),
    )
    note = f"Stock for {row['name']} is now {fmt_qty(new_stock)} {row['unit']}."
    if is_low_stock(new_stock, row["reorder_level"], row["safety_buffer"]):
        note += " That is at or below the reorder level."
    return note


async def apply_deduction(
    conn, shop_id, ingredient_id, need, movement_date, ref_table, ref_id, movement_type
) -> str:
    from app.store import fetchone, execute

    row = await fetchone(
        conn,
        """
        SELECT id, name, unit, stock_on_hand, reorder_level, safety_buffer
        FROM ingredients
        WHERE id = %s AND shop_id = %s
        FOR UPDATE
        """,
        (ingredient_id, shop_id),
    )
    if row is None:
        return "Missing ingredient."
    already = await fetchone(
        conn,
        """
        SELECT 1 FROM stock_movements
        WHERE ref_table = %s AND ref_id = %s AND ingredient_id = %s
        """,
        (ref_table, ref_id, ingredient_id),
    )
    if already:
        return f"{row['name']} was already deducted for this record."
    plan = plan_deduction(row["stock_on_hand"], need)
    if plan.action == "skip_unknown":
        return f"I didn't change {row['name']} stock because I don't have a count yet."
    if plan.qty_change is None or plan.qty_change == 0:
        return (
            f"{row['name']} is already at 0 and this needed {fmt_qty(need)} {row['unit']}. "
            "Please send a stock count."
        )
    await execute(
        conn,
        "UPDATE ingredients SET stock_on_hand = %s, updated_at = now() WHERE id = %s AND shop_id = %s",
        (q3(plan.new_stock), ingredient_id, shop_id),
    )
    await execute(
        conn,
        """
        INSERT INTO stock_movements
            (shop_id, ingredient_id, movement_date, movement_type, qty_change, ref_table, ref_id, stock_after)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            shop_id,
            ingredient_id,
            movement_date,
            movement_type,
            q3(plan.qty_change),
            ref_table,
            ref_id,
            q3(plan.new_stock),
        ),
    )
    if plan.action == "short":
        return (
            f"{row['name']} was short (needed {fmt_qty(need)} {row['unit']}, "
            f"had {fmt_qty(row['stock_on_hand'])}). I set it to 0. Please send a stock count."
        )
    note = f"{row['name']} is now {fmt_qty(plan.new_stock)} {row['unit']}."
    if is_low_stock(plan.new_stock, row["reorder_level"], row["safety_buffer"]):
        note += " Low stock."
    return note


def q2_or_none(value):
    from app.money import q2

    if value is None:
        return None
    return q2(value)


def _cost(value):
    if value is None:
        return None
    return q4(value)
