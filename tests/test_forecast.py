from datetime import date
from decimal import Decimal

from app.modules.forecast import compute_prep, render_forecast


def test_no_history_does_not_invent_a_quantity():
    assert compute_prep([]) is None
    text = render_forecast(date(2026, 11, 14), [], ["chicken kota"])
    assert "will not guess" in text
    assert "chicken kota" in text


def test_range_comes_from_observed_sales():
    prep = compute_prep([Decimal("10"), Decimal("12"), Decimal("8"), Decimal("14")])
    assert prep is not None
    assert prep["low"] == Decimal("8")
    assert prep["high"] == Decimal("14")
    assert prep["predicted"] == Decimal("11.0")
    assert prep["early"] is False
    assert prep["method"] == "weekday_average"


def test_one_day_is_an_early_estimate_not_a_wider_guess():
    prep = compute_prep([Decimal("9")])
    assert prep["predicted"] == Decimal("9")
    assert prep["low"] == Decimal("9")
    assert prep["high"] == Decimal("9")
    assert prep["early"] is True
    assert prep["confidence"] == "low"
