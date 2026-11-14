"""Stop a free-text model reply that contains a number we did not get from the shop."""

from decimal import Decimal

from app.modules.parsing import text_numbers

SAFE_REPLY = (
    "I can only share figures that are already in your saved records. "
    "Ask me for today's sales, stock, or profit, and I will read those back."
)


def guard_reply(reply: str, allowed: set[Decimal]) -> str:
    for number in text_numbers(reply):
        if not any(number == item for item in allowed):
            return SAFE_REPLY
    return reply


def numbers_in(value, found: set[Decimal] | None = None) -> set[Decimal]:
    bucket = found if found is not None else set()
    if isinstance(value, Decimal):
        bucket.add(value)
    elif isinstance(value, bool) or value is None:
        return bucket
    elif isinstance(value, (int, float)):
        bucket.add(Decimal(str(value)))
    elif isinstance(value, str):
        bucket |= text_numbers(value)
    elif isinstance(value, dict):
        for item in value.values():
            numbers_in(item, bucket)
    elif isinstance(value, (list, tuple)):
        for item in value:
            numbers_in(item, bucket)
    return bucket
