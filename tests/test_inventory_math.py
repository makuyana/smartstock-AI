from decimal import Decimal

from app.modules.inventory import buy_qty, is_low_stock, new_average_cost, plan_deduction, render_shopping


def test_deduction_plans():
    unknown = plan_deduction(None, Decimal("4"))
    assert unknown.action == "skip_unknown"
    assert unknown.new_stock is None

    ok = plan_deduction(Decimal("10"), Decimal("4"))
    assert ok.action == "deduct"
    assert ok.new_stock == Decimal("6")
    assert ok.qty_change == Decimal("-4")

    short = plan_deduction(Decimal("2"), Decimal("5"))
    assert short.action == "short"
    assert short.new_stock == Decimal("0")
    assert short.qty_change == Decimal("-2")
    assert short.shortfall == Decimal("3")


def test_average_cost_does_not_invent_a_weight():
    assert new_average_cost(None, None, Decimal("7"), Decimal("10")) == Decimal("7")
    assert new_average_cost(Decimal("5"), None, Decimal("7"), Decimal("10")) == Decimal("5")
    blended = new_average_cost(Decimal("5"), Decimal("10"), Decimal("7"), Decimal("10"))
    assert blended == Decimal("6")


def test_low_stock_and_shopping_list_skip_unknowns():
    assert is_low_stock(None, Decimal("5"), None) is None
    assert is_low_stock(Decimal("3"), Decimal("5"), None) is True
    assert is_low_stock(Decimal("6"), Decimal("5"), None) is False
    assert is_low_stock(Decimal("3"), None, None) is None
    assert buy_qty(Decimal("3"), Decimal("5"), Decimal("2")) == Decimal("2")
    text = render_shopping(
        [
            {
                "name": "chips",
                "unit": "kg",
                "stock": None,
                "reorder": Decimal("5"),
                "safety": None,
                "is_low": False,
                "buy": None,
            }
        ]
    )
    assert "don't have stock counts" in text
    assert "5" not in text
