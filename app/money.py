"""Money and quantity formatting. Amounts stay Decimal, never float."""

from decimal import Decimal, ROUND_HALF_UP


def D(value) -> Decimal | None:
    if value is None or value is False:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        number = Decimal(str(value))
    else:
        text = str(value).strip().replace("R", "").replace("r", "").replace(" ", "")
        if text.count(",") == 1 and "." not in text:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
        if not text:
            return None
        number = Decimal(text)
    return number


def q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def q3(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def q4(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def fmt_qty(value: Decimal) -> str:
    quantized = q3(value)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def fmt_money(value) -> str:
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    quantized = q2(number)
    if quantized == quantized.to_integral():
        return f"R{quantized:.0f}"
    return f"R{quantized:.2f}"
