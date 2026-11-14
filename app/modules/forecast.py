"""Forecast specialist. Prep ranges come only from saved sales."""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.money import fmt_money, fmt_qty, q2


def compute_prep(samples: list[Decimal]) -> dict | None:
    if not samples:
        return None
    count = len(samples)
    average = sum(samples, Decimal("0")) / Decimal(count)
    low = min(samples)
    high = max(samples)
    predicted = average.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if predicted < low:
        predicted = low
    if predicted > high:
        predicted = high
    if count < 4:
        confidence = "low"
    elif count < 8:
        confidence = "medium"
    else:
        confidence = "high"
    return {
        "predicted": predicted,
        "low": low,
        "high": high,
        "early": count < 4,
        "confidence": confidence,
        "sample_size": count,
        "method": "weekday_average",
    }


def day_type(weekday_avg: Decimal | None, overall_avg: Decimal | None, samples: int) -> str | None:
    if weekday_avg is None or overall_avg is None or samples < 4 or overall_avg <= 0:
        return None
    if weekday_avg >= overall_avg * Decimal("1.2"):
        return "busy"
    if weekday_avg <= overall_avg * Decimal("0.8"):
        return "quiet"
    return "normal"


def payday_adjustment(target: date) -> list[str]:
    """Tag common South African paydays. Do not change the quantity."""
    if target.day in {15, 25}:
        return ["payday"]
    next_day = target + timedelta(days=1)
    if next_day.month != target.month:
        return ["payday"]
    return []


def render_forecast(target: date, rows: list[dict], missing_names: list[str]) -> str:
    if not rows and not missing_names:
        return (
            f"I can't give prep numbers for {target.isoformat()} because this shop has no products yet."
        )
    if not rows:
        missing = ""
        if missing_names:
            missing = " No sales history yet for: " + ", ".join(missing_names) + "."
        return (
            f"I can't give prep numbers for {target.isoformat()}. "
            "There are no saved sales for that weekday yet, so I will not guess a quantity."
            + missing
        )
    lines = [f"Prep for {target.isoformat()}:"]
    for row in rows:
        early = " Early guess, not many days of sales." if row["early"] else ""
        price = ""
        if row.get("expected_revenue") is not None:
            price = f", about {fmt_money(row['expected_revenue'])} if the usual price holds"
        lines.append(
            f"• {row['name']}: {fmt_qty(row['predicted'])} "
            f"(range {fmt_qty(row['low'])} to {fmt_qty(row['high'])}, "
            f"from {row['sample_size']} days).{early}{price}"
        )
        if row.get("day_type"):
            lines.append(f"  {row['name']} looks like a {row['day_type']} day compared with your average.")
        if row.get("adjustments"):
            lines.append(
                "  Today is a common payday. I did not increase the numbers, because I don't have a payday pattern saved."
            )
    if missing_names:
        lines.append("No sales history yet for: " + ", ".join(missing_names) + ".")
    return "\n".join(lines)


def _pg_dow(target: date) -> int:
    return (target.weekday() + 1) % 7


async def build_for_shop(conn, shop_id: str, target: date) -> tuple[list[dict], list[str]]:
    from app.store import fetchall, execute

    products = await fetchall(
        conn,
        """
        SELECT id, name, usual_price
        FROM products
        WHERE shop_id = %s AND is_active
        ORDER BY name
        """,
        (shop_id,),
    )
    start = target - timedelta(days=56)
    rows = []
    missing = []
    adjustments = payday_adjustment(target)
    for product in products:
        samples_rows = await fetchall(
            conn,
            """
            SELECT sum(quantity) AS qty
            FROM sales_entries
            WHERE shop_id = %s AND product_id = %s
              AND sale_date < %s AND sale_date >= %s
              AND EXTRACT(DOW FROM sale_date) = %s
            GROUP BY sale_date
            ORDER BY sale_date
            """,
            (shop_id, product["id"], target, start, _pg_dow(target)),
        )
        samples = [row["qty"] for row in samples_rows if row["qty"] is not None]
        prep = compute_prep(samples)
        if prep is None:
            missing.append(product["name"])
            continue
        overall_rows = await fetchall(
            conn,
            """
            SELECT sum(quantity) AS qty
            FROM sales_entries
            WHERE shop_id = %s AND product_id = %s
              AND sale_date < %s AND sale_date >= %s
            GROUP BY sale_date
            """,
            (shop_id, product["id"], target, start),
        )
        overall = [row["qty"] for row in overall_rows if row["qty"] is not None]
        overall_avg = sum(overall, Decimal("0")) / Decimal(len(overall)) if overall else None
        weekday_avg = sum(samples, Decimal("0")) / Decimal(len(samples))
        kind = day_type(weekday_avg, overall_avg, len(samples))
        expected = None
        if product["usual_price"] is not None:
            expected = q2(prep["predicted"] * product["usual_price"])
        reason = f"Average of {prep['sample_size']} same weekdays."
        if prep["early"]:
            reason += " Early estimate."
        await execute(
            conn,
            """
            INSERT INTO forecasts (
                shop_id, target_date, product_id, predicted_qty, range_low, range_high,
                method, confidence, reason, expected_revenue, is_early_estimate, day_type, adjustments
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
            """,
            (
                shop_id,
                target,
                product["id"],
                prep["predicted"],
                prep["low"],
                prep["high"],
                prep["method"],
                prep["confidence"],
                reason,
                expected,
                prep["early"],
                kind,
                adjustments,
            ),
        )
        rows.append(
            {
                "name": product["name"],
                "predicted": prep["predicted"],
                "low": prep["low"],
                "high": prep["high"],
                "early": prep["early"],
                "sample_size": prep["sample_size"],
                "expected_revenue": expected,
                "day_type": kind,
                "adjustments": adjustments,
            }
        )
    return rows, missing


async def answer(conn, shop_id: str, text: str, today: date) -> str:
    lowered = text.lower()
    target = today + timedelta(days=1) if "tomorrow" in lowered else today
    rows, missing = await build_for_shop(conn, shop_id, target)
    return render_forecast(target, rows, missing)
