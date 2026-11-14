"""Insights specialist. Figures come from saved rows and the monthly_pnl view."""

from datetime import date, timedelta

from app.money import fmt_money, fmt_qty


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _add_months(day: date, count: int) -> date:
    month_index = day.month - 1 + count
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def week_bounds(today: date) -> tuple[date, date]:
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def render_daily(day: date, sales: list[dict], expenses: list[dict], waste: list[dict]) -> str:
    if not sales and not expenses and not waste:
        return (
            f"I have no saved sales, expenses, or waste for {day.isoformat()}. "
            "Tell me what you sold or spent."
        )
    lines = [f"For {day.isoformat()}:"]
    if not sales:
        lines.append("No sales saved.")
    for sale in sales:
        if sale.get("price_missing"):
            lines.append(f"• {fmt_qty(sale['qty'])} {sale['name']} (price missing, so no rand total)")
        else:
            lines.append(f"• {fmt_qty(sale['qty'])} {sale['name']}, {fmt_money(sale['revenue'])}")
    if expenses:
        for expense in expenses:
            lines.append(f"• Expense {expense['category']} {fmt_money(expense['amount'])}")
    else:
        lines.append("No expenses saved for this day.")
    if waste:
        for item in waste:
            if item.get("cost") is None:
                lines.append(
                    f"• Waste {fmt_qty(item['qty'])} {item.get('unit') or ''} {item['name']} (no cost on file)"
                )
            else:
                lines.append(
                    f"• Waste {fmt_qty(item['qty'])} {item.get('unit') or ''} {item['name']}, {fmt_money(item['cost'])}"
                )
    else:
        lines.append("No waste saved for this day.")
    return "\n".join(lines)


def render_weekly(start: date, end: date, sales: list[dict], expense_total, waste_known, waste_missing: bool) -> str:
    lines = [f"Week {start.isoformat()} to {end.isoformat()}:"]
    if not sales:
        lines.append("No sales saved this week.")
    incomplete = False
    for sale in sales:
        if sale.get("price_missing"):
            incomplete = True
            lines.append(f"• {fmt_qty(sale['qty'])} {sale['name']} (some prices missing)")
        else:
            lines.append(f"• {fmt_qty(sale['qty'])} {sale['name']}, {fmt_money(sale['revenue'])}")
    if expense_total is None:
        lines.append("No expenses saved this week.")
    else:
        lines.append(f"Expenses saved: {fmt_money(expense_total)}.")
    if waste_missing:
        lines.append("Some waste has no cost, so I won't give a profit figure for the week.")
    elif waste_known is None:
        lines.append("No waste cost recorded this week.")
    else:
        lines.append(f"Recorded waste cost: {fmt_money(waste_known)}.")
    if incomplete:
        lines.append("Sales money is incomplete because some items have no price.")
    lines.append("I only show money that is already saved. I do not fill gaps.")
    return "\n".join(lines)


def render_monthly(month: date, row: dict | None, margins: list[dict]) -> str:
    label = month.strftime("%B %Y")
    lines = [f"{label}:"]
    if row is None:
        lines.append("I don't have sales, waste, or expenses saved for this month, so I can't give a profit figure.")
    else:
        if row.get("revenue") is None:
            lines.append("Sales money: missing.")
        elif row.get("revenue_incomplete"):
            lines.append(
                f"Sales so far {fmt_money(row['revenue'])}, but some items have no price, so this is not the full total."
            )
        else:
            lines.append(f"Sales: {fmt_money(row['revenue'])}.")
        if row.get("cogs") is None or row.get("cogs_incomplete"):
            lines.append("Food cost: missing. I need recipes and ingredient costs.")
        else:
            lines.append(f"Food cost: {fmt_money(row['cogs'])}.")
        if row.get("waste_incomplete"):
            lines.append("Waste cost: some waste has no cost.")
        else:
            lines.append(f"Recorded waste cost: {fmt_money(row.get('waste_cost') or 0)}.")
        lines.append(f"Other costs saved: {fmt_money(row.get('opex') or 0)}.")
        complete = not any(
            row.get(flag) for flag in ("revenue_incomplete", "cogs_incomplete", "waste_incomplete")
        )
        if complete and row.get("net_profit") is not None and row.get("revenue") is not None and row.get("cogs") is not None:
            lines.append(f"Profit: {fmt_money(row['net_profit'])}.")
        else:
            lines.append("I won't give a profit figure until sales prices, food cost, and waste cost are complete.")
    if margins:
        lines.append("Item margins:")
        for item in margins[:15]:
            if item.get("usual_price") is None:
                lines.append(f"• {item['name']}: no selling price yet.")
            elif item.get("unit_cogs") is None:
                lines.append(f"• {item['name']}: no food cost yet (need a recipe and ingredient costs).")
            else:
                margin = item.get("unit_margin")
                pct = item.get("margin_pct")
                pct_bit = f" ({pct}%)" if pct is not None else ""
                lines.append(
                    f"• {item['name']}: sell {fmt_money(item['usual_price'])}, "
                    f"food cost {fmt_money(item['unit_cogs'])}, margin {fmt_money(margin)}{pct_bit}."
                )
    else:
        lines.append("No products yet, so no margins.")
    return "\n".join(lines)


def render_margins(margins: list[dict]) -> str:
    if not margins:
        return "This shop has no products yet, so I can't show margins."
    lines = ["Item margins:"]
    for item in margins:
        if item.get("usual_price") is None:
            lines.append(f"• {item['name']}: no selling price yet.")
        elif item.get("unit_cogs") is None:
            lines.append(f"• {item['name']}: no food cost yet.")
        else:
            lines.append(
                f"• {item['name']}: sell {fmt_money(item['usual_price'])}, "
                f"food cost {fmt_money(item['unit_cogs'])}, margin {fmt_money(item['unit_margin'])}."
            )
    return "\n".join(lines)


async def _sales(conn, shop_id, start, end):
    from app.store import fetchall

    return await fetchall(
        conn,
        """
        SELECT p.name,
               sum(se.quantity) AS qty,
               sum(se.quantity * se.unit_price) FILTER (WHERE se.unit_price IS NOT NULL) AS revenue,
               bool_or(se.unit_price IS NULL) AS price_missing
        FROM sales_entries se
        JOIN products p ON p.id = se.product_id AND p.shop_id = se.shop_id
        WHERE se.shop_id = %s AND se.sale_date BETWEEN %s AND %s
        GROUP BY p.name
        ORDER BY p.name
        """,
        (shop_id, start, end),
    )


async def answer(conn, shop_id: str, text: str, today: date) -> str:
    from app.store import fetchall, fetchone

    lowered = text.lower()
    if "margin" in lowered:
        margins = await fetchall(
            conn,
            "SELECT name, usual_price, unit_cogs, unit_margin, margin_pct FROM product_margins WHERE shop_id = %s ORDER BY name",
            (shop_id,),
        )
        return render_margins(margins)
    if any(word in lowered for word in ("week", "weekly")):
        start, end = week_bounds(today)
        sales = await _sales(conn, shop_id, start, end)
        expense = await fetchone(
            conn,
            "SELECT sum(amount) AS total FROM expenses WHERE shop_id = %s AND expense_date BETWEEN %s AND %s",
            (shop_id, start, end),
        )
        waste = await fetchone(
            conn,
            """
            SELECT sum(estimated_cost) AS total, bool_or(estimated_cost IS NULL) AS missing
            FROM waste WHERE shop_id = %s AND waste_date BETWEEN %s AND %s
            """,
            (shop_id, start, end),
        )
        return render_weekly(
            start,
            end,
            sales,
            expense["total"] if expense else None,
            waste["total"] if waste else None,
            bool(waste and waste["missing"]),
        )
    if any(word in lowered for word in ("month", "profit", "p&l", "pnl")):
        month = _month_start(today)
        if "last month" in lowered:
            month = _add_months(month, -1)
        row = await fetchone(
            conn,
            """
            SELECT month, revenue, cogs, waste_cost, opex, utility_appliance_costs, net_profit,
                   revenue_incomplete, cogs_incomplete, waste_incomplete
            FROM monthly_pnl
            WHERE shop_id = %s AND month = %s
            """,
            (shop_id, month),
        )
        margins = await fetchall(
            conn,
            "SELECT name, usual_price, unit_cogs, unit_margin, margin_pct FROM product_margins WHERE shop_id = %s ORDER BY name",
            (shop_id,),
        )
        return render_monthly(month, row, margins)
    sales = await _sales(conn, shop_id, today, today)
    expenses = await fetchall(
        conn,
        "SELECT category, amount FROM expenses WHERE shop_id = %s AND expense_date = %s ORDER BY category",
        (shop_id, today),
    )
    waste = await fetchall(
        conn,
        """
        SELECT COALESCE(i.name, p.name) AS name, w.quantity AS qty, w.unit, w.estimated_cost AS cost
        FROM waste w
        LEFT JOIN ingredients i ON i.id = w.ingredient_id
        LEFT JOIN products p ON p.id = w.product_id
        WHERE w.shop_id = %s AND w.waste_date = %s
        ORDER BY name
        """,
        (shop_id, today),
    )
    return render_daily(today, sales, expenses, waste)
