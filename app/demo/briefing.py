"""Deterministic answers from saved rows. No prices are filled in."""

from datetime import date, timedelta
from decimal import Decimal

from app.money import fmt_money, fmt_qty
from app.modules import insights


def last_seven_days(today: date) -> tuple[date, date]:
    return today - timedelta(days=6), today


def price_changes(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["item_name"], []).append(row)
    changes = []
    for item, observations in grouped.items():
        ordered = sorted(observations, key=lambda row: row["observed_on"])
        previous = None
        for row in ordered:
            if previous is not None and row["unit_price"] != previous["unit_price"]:
                changes.append(
                    {
                        "item": item,
                        "unit": row["unit"],
                        "on": row["observed_on"],
                        "old": previous["unit_price"],
                        "new": row["unit_price"],
                    }
                )
            previous = row
    return changes


def _day_label(day: date) -> str:
    return f"{day.day} {day.strftime('%b')}"


def render_price_changes(changes: list[dict]) -> list[str]:
    lines = []
    for change in changes:
        lines.append(
            f"{change['item']} went from {fmt_money(change['old'])} to {fmt_money(change['new'])} "
            f"per {change['unit']} on {_day_label(change['on'])}."
        )
    return lines


def waste_by_weekday(rows: list[dict]) -> list[tuple[str, list[tuple[date, Decimal]]]]:
    grouped: dict[str, list[tuple[date, Decimal]]] = {}
    for row in rows:
        day = row["waste_date"]
        grouped.setdefault(day.strftime("%A"), []).append((day, row["quantity"]))
    ranked = sorted(grouped.items(), key=lambda item: sum(qty for _, qty in item[1]) / len(item[1]), reverse=True)
    return ranked


def render_waste(rows: list[dict]) -> list[str]:
    if not rows:
        return ["No waste is saved."]
    name = rows[0]["name"]
    ranked = waste_by_weekday(rows)
    worst, details = ranked[0]
    listed = ", ".join(f"{fmt_qty(qty)} on {_day_label(day)}" for day, qty in sorted(details))
    sentence = f"{name} spoilage is highest on {worst}: {listed}."
    lines = [
        sentence[:1].upper() + sentence[1:],
        "There is no price for this item, so I cannot turn the spoilage into rand.",
    ]
    return lines


def render_sellers(totals: list[dict], prices_missing: bool) -> list[str]:
    if not totals:
        return ["No sales are saved for this period."]
    lines = ["Best sellers by units:"]
    for row in totals:
        lines.append(f"• {row['name']}: {fmt_qty(row['qty'])}")
    if prices_missing:
        lines.append("Menu prices are missing, so this ranking is units only, not rand.")
    return lines


def render_takings(amounts: list[Decimal], prices_missing: bool) -> str:
    if amounts:
        total = sum(amounts, Decimal("0"))
        return f"Recorded takings: {fmt_money(total)} from the daily totals on the sheet. This is not profit."
    if prices_missing:
        return "No daily takings were on the sheet, and menu prices are missing, so I cannot give a rand sales total."
    return "No daily takings were on the sheet."


def render_peaks(daily: list[tuple[date, Decimal]]) -> str:
    if not daily:
        return ""
    grouped: dict[str, list[Decimal]] = {}
    for day, qty in daily:
        grouped.setdefault(day.strftime("%A"), []).append(qty)
    ranked = sorted(
        ((name, sum(values, Decimal("0")) / Decimal(len(values))) for name, values in grouped.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    if len(ranked) == 1:
        name, average = ranked[0]
        return f"The busiest weekday is {name}, average {fmt_qty(average)} units."
    first, second = ranked[0], ranked[1]
    return (
        f"{first[0]} and {second[0]} are the busiest days "
        f"(average {fmt_qty(first[1])} and {fmt_qty(second[1])} units)."
    )


def render_fuel(rows: list[dict]) -> str:
    if not rows:
        return ""
    total = sum((row["amount"] for row in rows), Decimal("0"))
    top = max(rows, key=lambda row: row["amount"])
    return (
        f"Fuel expenses saved: {fmt_money(total)}. "
        f"The highest day is {_day_label(top['expense_date'])} at {fmt_money(top['amount'])}."
    )


def render_week(
    start: date,
    end: date,
    totals: list[dict],
    daily: list[tuple[date, Decimal]],
    takings: list[Decimal],
    waste_rows: list[dict],
    prices_missing: bool,
) -> str:
    lines = [f"Last 7 saved days, {_day_label(start)} to {_day_label(end)}:"]
    lines.extend(render_sellers(totals, prices_missing))
    if daily:
        busiest = max(daily, key=lambda item: item[1])
        lines.append(f"Busiest day in this stretch: {_day_label(busiest[0])} with {fmt_qty(busiest[1])} units.")
    peak = render_peaks(daily)
    if peak:
        lines.append(peak)
    if waste_rows:
        wasted = sum((row["quantity"] for row in waste_rows), Decimal("0"))
        lines.append(
            f"Spoilage in these 7 days: {fmt_qty(wasted)} {waste_rows[0]['name']} (no cost on file)."
        )
    lines.append(render_takings(takings, prices_missing))
    return "\n".join(lines)


def render_losses(waste_rows: list[dict], changes: list[dict], fuel_rows: list[dict], prices_missing: bool, takings: list[Decimal]) -> str:
    lines = ["Where the sheet shows money going out:"]
    if waste_rows:
        lines.extend(render_waste(waste_rows))
    else:
        lines.append("No waste rows are saved.")
    change_lines = render_price_changes(changes)
    if change_lines:
        lines.append("Price changes on the sheet:")
        lines.extend(f"• {line}" for line in change_lines)
        lines.append("I did not turn that rise into a loss amount.")
    else:
        lines.append("No unit-price changes are saved.")
    fuel = render_fuel(fuel_rows)
    if fuel:
        lines.append(fuel)
    lines.append(render_takings(takings, prices_missing))
    lines.append("I will not state a profit figure unless sales prices, food cost, and waste cost are all saved.")
    return "\n".join(lines)


async def _sales(conn, shop_id, start, end) -> list[dict]:
    cursor = await conn.execute(
        """
        SELECT p.name, sum(se.quantity) AS qty,
               bool_or(se.unit_price IS NULL AND p.usual_price IS NULL) AS price_missing
        FROM sales_entries se
        JOIN products p ON p.id = se.product_id AND p.shop_id = se.shop_id
        WHERE se.shop_id = %s AND se.sale_date BETWEEN %s AND %s
        GROUP BY p.name
        ORDER BY sum(se.quantity) DESC, p.name
        """,
        (shop_id, start, end),
    )
    return await cursor.fetchall()


async def _daily_units(conn, shop_id, start, end) -> list[tuple[date, Decimal]]:
    cursor = await conn.execute(
        """
        SELECT sale_date, sum(quantity) AS qty
        FROM sales_entries
        WHERE shop_id = %s AND sale_date BETWEEN %s AND %s
        GROUP BY sale_date
        ORDER BY sale_date
        """,
        (shop_id, start, end),
    )
    rows = await cursor.fetchall()
    return [(row["sale_date"], row["qty"]) for row in rows]


async def _takings(conn, shop_id, start, end) -> list[Decimal]:
    cursor = await conn.execute(
        """
        SELECT amount FROM daily_revenue
        WHERE shop_id = %s AND revenue_date BETWEEN %s AND %s
        """,
        (shop_id, start, end),
    )
    rows = await cursor.fetchall()
    return [row["amount"] for row in rows]


async def _waste(conn, shop_id, start, end) -> list[dict]:
    cursor = await conn.execute(
        """
        SELECT w.waste_date, w.quantity, w.unit, i.name
        FROM waste w
        JOIN ingredients i ON i.id = w.ingredient_id
        WHERE w.shop_id = %s AND w.waste_date BETWEEN %s AND %s
        ORDER BY w.waste_date
        """,
        (shop_id, start, end),
    )
    return await cursor.fetchall()


async def _prices(conn, shop_id) -> list[dict]:
    cursor = await conn.execute(
        """
        SELECT observed_on, item_name, unit, unit_price
        FROM price_observations
        WHERE shop_id = %s
        ORDER BY item_name, observed_on
        """,
        (shop_id,),
    )
    return await cursor.fetchall()


async def _fuel(conn, shop_id, start, end) -> list[dict]:
    cursor = await conn.execute(
        """
        SELECT expense_date, amount
        FROM expenses
        WHERE shop_id = %s AND category = 'fuel' AND expense_date BETWEEN %s AND %s
        """,
        (shop_id, start, end),
    )
    return await cursor.fetchall()


def _missing(totals: list[dict]) -> bool:
    return not totals or any(row["price_missing"] for row in totals)


async def answer(conn, shop_id: str, text: str, today: date) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("losing", "loss", "waste", "spoil", "spike", "petrol", "oil")):
        return await _losses(conn, shop_id, today)
    if "week" in lowered or "how did" in lowered:
        return await _week(conn, shop_id, today)
    if any(word in lowered for word in ("month", "report", "profit", "p&l")):
        return await _month(conn, shop_id, today)
    return await insights.answer(conn, shop_id, text, today)


async def _week(conn, shop_id, today: date) -> str:
    start, end = last_seven_days(today)
    totals = await _sales(conn, shop_id, start, end)
    return render_week(
        start,
        end,
        totals,
        await _daily_units(conn, shop_id, start, end),
        await _takings(conn, shop_id, start, end),
        await _waste(conn, shop_id, start, end),
        _missing(totals),
    )


async def _month(conn, shop_id, today: date) -> str:
    start = today.replace(day=1)
    base = await insights.answer(conn, shop_id, "profit this month", today)
    totals = await _sales(conn, shop_id, start, today)
    lines = [base, "", f"Units saved in {start.strftime('%B %Y')}:"]
    lines.extend(render_sellers(totals, _missing(totals)))
    peak = render_peaks(await _daily_units(conn, shop_id, start, today))
    if peak:
        lines.append(peak)
    lines.extend(render_waste(await _waste(conn, shop_id, start, today)))
    lines.extend(render_price_changes(price_changes(await _prices(conn, shop_id))))
    fuel = render_fuel(await _fuel(conn, shop_id, start, today))
    if fuel:
        lines.append(fuel)
    lines.append(render_takings(await _takings(conn, shop_id, start, today), _missing(totals)))
    return "\n".join(line for line in lines if line is not None)


async def _losses(conn, shop_id, today: date) -> str:
    start = today.replace(day=1)
    totals = await _sales(conn, shop_id, start, today)
    return render_losses(
        await _waste(conn, shop_id, date(2000, 1, 1), today),
        price_changes(await _prices(conn, shop_id)),
        await _fuel(conn, shop_id, date(2000, 1, 1), today),
        _missing(totals),
        await _takings(conn, shop_id, start, today),
    )
