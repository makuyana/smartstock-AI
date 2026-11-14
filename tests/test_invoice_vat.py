from decimal import Decimal

from app.modules.invoice import check_vat, price_changed


def test_vat_15_percent_passes():
    assert check_vat(Decimal("100"), Decimal("15"), Decimal("115")) == []


def test_vat_mismatch_and_total_mismatch():
    assert "vat_mismatch" in check_vat(Decimal("100"), Decimal("20"), Decimal("120"))
    assert "total_mismatch" in check_vat(Decimal("100"), Decimal("15"), Decimal("120"))


def test_missing_vat_is_not_filled_in():
    flags = check_vat(Decimal("100"), None, Decimal("115"))
    assert "vat_missing" in flags
    assert Decimal("15") not in flags


def test_price_change_uses_real_old_and_new():
    assert price_changed(Decimal("10"), Decimal("12"))
    assert not price_changed(Decimal("10.00"), Decimal("10.02"))
    assert not price_changed(None, Decimal("10"))
    assert not price_changed(Decimal("10"), None)
