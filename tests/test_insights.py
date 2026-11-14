from datetime import date
from decimal import Decimal

from app.modules.insights import render_daily, render_monthly


def test_daily_states_missing_prices():
    text = render_daily(
        date(2026, 11, 13),
        [{"name": "chicken kota", "qty": Decimal("12"), "revenue": None, "price_missing": True}],
        [],
        [],
    )
    assert "price missing" in text
    assert "R" not in text.split("price missing")[0]


def test_monthly_pnl_hides_profit_when_incomplete():
    text = render_monthly(
        date(2026, 11, 1),
        {
            "revenue": Decimal("1200"),
            "cogs": None,
            "waste_cost": Decimal("0"),
            "opex": Decimal("3500"),
            "net_profit": None,
            "revenue_incomplete": False,
            "cogs_incomplete": True,
            "waste_incomplete": False,
        },
        [{"name": "chicken kota", "usual_price": None, "unit_cogs": None, "unit_margin": None, "margin_pct": None}],
    )
    assert "Food cost: missing" in text
    assert "Profit:" not in text
    assert "no selling price" in text


def test_monthly_pnl_shows_profit_only_when_complete():
    text = render_monthly(
        date(2026, 11, 1),
        {
            "revenue": Decimal("2000"),
            "cogs": Decimal("800"),
            "waste_cost": Decimal("40"),
            "opex": Decimal("500"),
            "net_profit": Decimal("660"),
            "revenue_incomplete": False,
            "cogs_incomplete": False,
            "waste_incomplete": False,
        },
        [],
    )
    assert "Profit: R660" in text
    assert "Sales: R2000" in text
