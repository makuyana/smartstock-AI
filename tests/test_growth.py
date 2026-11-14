from decimal import Decimal

from app.modules.growth import funding_readiness, promo_draft
from app.scheduler.copy import morning_text


def test_promo_does_not_invent_a_discount():
    text = promo_draft("chicken kota", Decimal("25"), None)
    assert "R25" in text
    assert "did not put a special price" in text
    bare = promo_draft(None, None, None)
    assert "will not write a price" in bare


def test_funding_checklist_has_no_loan_amount():
    text = funding_readiness(
        {
            "distinct_sale_days": 10,
            "complete_pnl_months": 0,
            "confirmed_invoices": 1,
            "cash_snapshots": 0,
            "products": 2,
            "products_with_price": 0,
            "recipes": 0,
        }
    )
    assert "10" in text
    assert "missing" in text
    assert "loan amount" in text
    assert "R" not in text


def test_morning_without_sales_has_no_pretend_prep_count():
    text = morning_text("Demo Kota Shop", [], [], [])
    assert "can't give prep numbers" in text
    assert "0" not in text
