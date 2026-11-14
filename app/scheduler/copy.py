"""Text for the three scheduled WhatsApp templates. Only real figures are included."""

from datetime import date

from app.money import fmt_money, fmt_qty


def morning_text(shop_name: str, forecasts: list[dict], low_items: list[dict], expiring: list[dict]) -> str:
    lines = [f"Morning. Prep for {shop_name} today:"]
    if not forecasts:
        lines.append("I can't give prep numbers yet because there are no saved sales for this weekday.")
    for row in forecasts:
        early = " Early guess." if row.get("early") else ""
        lines.append(
            f"{row['name']}: {fmt_qty(row['predicted'])} "
            f"(range {fmt_qty(row['low'])} to {fmt_qty(row['high'])}).{early}"
        )
    if low_items:
        lines.append("Low stock:")
        for item in low_items:
            lines.append(f"{item['name']}: {fmt_qty(item['stock'])} {item['unit']} left.")
    else:
        lines.append("No low-stock warning, or stock counts are still missing.")
    for item in expiring:
        lines.append(f"{item['name']} expires on {item['expiry_date']}.")
    return " ".join(lines)


def evening_text(day: date, sales: list[dict]) -> str:
    if not sales:
        return (
            f"Evening check-in for {day.isoformat()}. I don't have any sales saved for today. "
            "Reply with what you sold, bought, or threw away."
        )
    lines = [f"Evening check-in for {day.isoformat()}. I have these sales:"]
    for sale in sales:
        if sale.get("price_missing"):
            lines.append(f"{fmt_qty(sale['qty'])} {sale['name']} (no price).")
        else:
            lines.append(f"{fmt_qty(sale['qty'])} {sale['name']} {fmt_money(sale['revenue'])}.")
    lines.append("Reply if something is missing.")
    return " ".join(lines)


def weekly_text(summary: str) -> str:
    return "Weekly wrap. " + " ".join(summary.split())
