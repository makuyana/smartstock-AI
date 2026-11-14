"""Growth and capital specialist. Promo copy and a funding checklist. No invented loan amounts."""

from decimal import Decimal

from app.money import fmt_money


def promo_draft(product: str | None, usual_price: Decimal | None, special_price: Decimal | None) -> str:
    if not product:
        return "Which item is the promo for? I will not write a price until you name the item."
    lines = [f"Draft promo for {product} (not posted):"]
    if usual_price is not None:
        lines.append(f"Usual price on file: {fmt_money(usual_price)}.")
    else:
        lines.append("No usual price on file.")
    if special_price is not None:
        lines.append(f"You said the special price is {fmt_money(special_price)}.")
    else:
        lines.append("I did not put a special price in. Reply with one if you want it.")
    return " ".join(lines)


def funding_readiness(facts: dict) -> str:
    checks = [
        (
            facts.get("distinct_sale_days", 0) >= 30,
            "At least 30 different days of sales",
            f"{facts.get('distinct_sale_days', 0)} days on record",
        ),
        (
            facts.get("complete_pnl_months", 0) >= 1,
            "At least one month where sales, food cost, and waste cost are all complete",
            f"{facts.get('complete_pnl_months', 0)} complete month(s)",
        ),
        (
            facts.get("confirmed_invoices", 0) >= 1,
            "At least one confirmed supplier invoice",
            f"{facts.get('confirmed_invoices', 0)} confirmed",
        ),
        (
            facts.get("cash_snapshots", 0) >= 1,
            "A saved cash snapshot",
            f"{facts.get('cash_snapshots', 0)} snapshot(s)",
        ),
        (
            facts.get("products", 0) > 0 and facts.get("products_with_price", 0) == facts.get("products", 0),
            "A selling price on every product",
            f"{facts.get('products_with_price', 0)} of {facts.get('products', 0)} priced",
        ),
        (
            facts.get("recipes", 0) >= 1,
            "At least one recipe so food cost can be worked out",
            f"{facts.get('recipes', 0)} recipe line(s)",
        ),
    ]
    ready = sum(1 for ok, _, _ in checks if ok)
    lines = [f"Funding checklist: {ready} of {len(checks)} items are in place."]
    for ok, label, detail in checks:
        mark = "done" if ok else "missing"
        lines.append(f"• {mark}: {label} ({detail}).")
    if ready == len(checks):
        lines.append("The records a funder usually asks for are present. This is not a loan offer.")
    else:
        lines.append("I did not estimate a loan amount. Finish the missing items first.")
    return "\n".join(lines)


def product_mentioned(text: str, names: list[str]) -> str | None:
    lowered = text.lower()
    matches = [name for name in names if name.lower() in lowered]
    if not matches:
        return None
    matches.sort(key=len, reverse=True)
    return matches[0]


async def answer(conn, shop_id: str, text: str, products: list[dict]) -> str:
    from app.modules.parsing import money_amounts
    from app.store import fetchone

    lowered = text.lower()
    if any(word in lowered for word in ("funding", "loan", "investor", "capital")):
        facts = {}
        facts["distinct_sale_days"] = (
            await fetchone(
                conn,
                "SELECT count(DISTINCT sale_date) AS n FROM sales_entries WHERE shop_id = %s",
                (shop_id,),
            )
        )["n"]
        facts["confirmed_invoices"] = (
            await fetchone(
                conn,
                "SELECT count(*) AS n FROM invoices WHERE shop_id = %s AND confirmed",
                (shop_id,),
            )
        )["n"]
        facts["cash_snapshots"] = (
            await fetchone(conn, "SELECT count(*) AS n FROM cash_snapshots WHERE shop_id = %s", (shop_id,))
        )["n"]
        facts["products"] = (
            await fetchone(conn, "SELECT count(*) AS n FROM products WHERE shop_id = %s", (shop_id,))
        )["n"]
        facts["products_with_price"] = (
            await fetchone(
                conn,
                "SELECT count(*) AS n FROM products WHERE shop_id = %s AND usual_price IS NOT NULL",
                (shop_id,),
            )
        )["n"]
        facts["recipes"] = (
            await fetchone(conn, "SELECT count(*) AS n FROM recipes WHERE shop_id = %s", (shop_id,))
        )["n"]
        facts["complete_pnl_months"] = (
            await fetchone(
                conn,
                """
                SELECT count(*) AS n FROM monthly_pnl
                WHERE shop_id = %s
                  AND net_profit IS NOT NULL
                  AND NOT revenue_incomplete
                  AND NOT cogs_incomplete
                  AND NOT waste_incomplete
                """,
                (shop_id,),
            )
        )["n"]
        return funding_readiness(facts)

    from app.modules.parsing import normalize_words

    names = [product["name"] for product in products]
    chosen = product_mentioned(text, names)
    usual = None
    if chosen:
        for product in products:
            if product["name"] == chosen:
                usual = product.get("usual_price")
                break
    amounts = money_amounts(normalize_words(text))
    special = amounts[0] if amounts else None
    return promo_draft(chosen, usual, special)
