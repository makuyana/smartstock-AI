"""Invoice specialist: VAT checks and price-change flags. Never fills in a missing VAT amount."""

from decimal import Decimal

from app.money import q2

VAT_RATE = Decimal("0.15")
TOLERANCE = Decimal("0.05")


def check_vat(subtotal, vat, total, discount=None) -> list[str]:
    """Compare the invoice's own figures to South Africa's 15% VAT rate."""
    flags: list[str] = []
    if subtotal is None and vat is None and total is None:
        return ["amounts_missing"]
    if vat is None and (subtotal is not None or total is not None):
        flags.append("vat_missing")
    if subtotal is None and vat is not None:
        flags.append("subtotal_missing")
    if subtotal is not None and vat is not None:
        expected = q2(subtotal * VAT_RATE)
        if (expected - vat).copy_abs() > TOLERANCE:
            flags.append("vat_mismatch")
    if subtotal is not None and vat is not None and total is not None:
        discount_amount = discount or Decimal("0")
        expected_total = q2(subtotal - discount_amount + vat)
        if (expected_total - total).copy_abs() > TOLERANCE:
            flags.append("total_mismatch")
    return flags


def price_changed(old, new) -> bool:
    if old is None or new is None:
        return False
    if (old - new).copy_abs() < TOLERANCE:
        return False
    if old == 0:
        return new != 0
    return (old - new).copy_abs() / abs(old) >= Decimal("0.01")
