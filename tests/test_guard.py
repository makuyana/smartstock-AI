from decimal import Decimal

from app.llm.guard import guard_reply


def test_guard_blocks_a_number_the_tools_did_not_return():
    safe = guard_reply("You made R999 today", {Decimal("12"), Decimal("8")})
    assert "999" not in safe
    assert "saved records" in safe


def test_guard_allows_numbers_from_the_tool_result():
    reply = "You sold 12 chicken kota."
    assert guard_reply(reply, {Decimal("12")}) == reply
