import asyncio
from datetime import date
from decimal import Decimal

from app.llm.client import LLMResponse, ToolCall
from app.modules.parsing import (
    extract_heuristic,
    is_no,
    is_yes,
    normalize_extraction,
    parse_with_llm,
)

TODAY = date(2026, 11, 13)
PRODUCTS = ["chicken kota", "russian kota"]
INGREDIENTS = [
    {"name": "bread quarter loaf", "unit": "each"},
    {"name": "chips", "unit": "kg"},
    {"name": "russian sausage", "unit": "kg"},
]


class FakeLLM:
    def __init__(self, arguments):
        self.arguments = arguments
        self.calls = 0

    async def chat(self, messages, tools=None, tool_choice=None):
        self.calls += 1
        return LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="call-1", name="extract_shop_events", arguments=self.arguments)],
        )


def test_heuristic_sold_two_kotas():
    outcome = extract_heuristic("sold 12 chicken kota 8 russian", TODAY, PRODUCTS, INGREDIENTS)
    assert outcome.confirm_allowed
    assert [record.item_name for record in outcome.records] == ["chicken kota", "russian kota"]
    assert outcome.records[0].quantity == Decimal("12")
    assert outcome.records[1].quantity == Decimal("8")
    assert outcome.records[0].unit_price is None
    assert "YES" in outcome.read_back
    assert "chicken kota" in outcome.read_back
    assert "russian kota" in outcome.read_back
    assert "R" not in outcome.read_back.replace("Reply", "")


def test_heuristic_purchase_and_waste():
    purchase = extract_heuristic("bought 20 bread R180", TODAY, PRODUCTS, INGREDIENTS)
    assert purchase.confirm_allowed
    assert purchase.records[0].kind == "purchase"
    assert purchase.records[0].item_name == "bread quarter loaf"
    assert purchase.records[0].quantity == Decimal("20")
    assert purchase.records[0].total == Decimal("180.00")
    assert purchase.records[0].unit_cost_calculated
    assert purchase.records[0].unit_cost == Decimal("9.0000")
    assert "R180" in purchase.read_back
    assert "YES" in purchase.read_back

    waste = extract_heuristic("threw away 5 chips burnt", TODAY, PRODUCTS, INGREDIENTS)
    assert waste.confirm_allowed
    assert waste.records[0].kind == "waste"
    assert waste.records[0].item_name == "chips"
    assert waste.records[0].quantity == Decimal("5")
    assert waste.records[0].reason == "burnt"
    assert waste.records[0].unit == "kg"
    assert "burnt" in waste.read_back
    assert "YES" in waste.read_back


def test_slang_and_afrikaans_sale():
    outcome = extract_heuristic("sold 12 chkn kota n 8 rus", TODAY, PRODUCTS, INGREDIENTS)
    assert outcome.confirm_allowed
    assert [record.item_name for record in outcome.records] == ["chicken kota", "russian kota"]
    afrikaans = extract_heuristic("verkoop 3 chicken kota", TODAY, PRODUCTS, INGREDIENTS)
    assert afrikaans.confirm_allowed
    assert afrikaans.records[0].quantity == Decimal("3")


def test_ambiguous_kota_is_not_saved_as_a_guess():
    outcome = extract_heuristic("sold 12 kota", TODAY, PRODUCTS, INGREDIENTS)
    assert not outcome.confirm_allowed
    assert "chicken kota" in outcome.read_back
    assert "russian kota" in outcome.read_back
    assert "YES" not in outcome.read_back
    assert "not saved" in outcome.read_back.lower() or "have not saved" in outcome.read_back.lower()


def test_expense_rent():
    outcome = extract_heuristic("rent R3500", TODAY, PRODUCTS, INGREDIENTS)
    assert outcome.confirm_allowed
    assert outcome.records[0].kind == "expense"
    assert outcome.records[0].category == "rent"
    assert outcome.records[0].amount == Decimal("3500.00")


def test_llm_sale_is_grounded_and_drops_invented_price():
    fake = FakeLLM(
        {
            "events": [
                {
                    "kind": "sale",
                    "item_name": "chicken kota",
                    "quantity": 12,
                    "unit_price": 999,
                },
                {"kind": "sale", "item_name": "russian", "quantity": 8},
            ],
            "needs_clarification": False,
        }
    )
    outcome = asyncio.run(
        parse_with_llm("sold 12 chicken kota 8 russian", fake, TODAY, PRODUCTS, INGREDIENTS)
    )
    assert fake.calls == 1
    assert outcome.confirm_allowed
    assert outcome.records[0].unit_price is None
    assert "999" not in outcome.read_back
    assert outcome.records[1].item_name == "russian kota"


def test_llm_missing_quantity_asks_and_does_not_invent():
    fake = FakeLLM(
        {
            "events": [{"kind": "sale", "item_name": "chicken kota", "quantity": None}],
            "needs_clarification": True,
            "clarification_question": "How many chicken kota?",
        }
    )
    outcome = asyncio.run(parse_with_llm("sold chicken kota", fake, TODAY, PRODUCTS, INGREDIENTS))
    assert not outcome.confirm_allowed
    assert outcome.records[0].quantity is None
    assert "How many" in outcome.read_back


def test_llm_drops_item_that_was_not_said():
    raw = {"events": [{"kind": "sale", "item_name": "lobster", "quantity": 4, "unit_price": 99}]}
    outcome = normalize_extraction("sold 12 chicken kota", raw, TODAY, PRODUCTS, INGREDIENTS)
    assert outcome.records == [] or all(record.item_name != "lobster" for record in outcome.records)
    assert "99" not in outcome.read_back
    assert "lobster" not in outcome.read_back


def test_llm_unmentioned_quantity_is_dropped():
    raw = {"events": [{"kind": "sale", "item_name": "chicken kota", "quantity": 99}]}
    outcome = normalize_extraction("sold chicken kota", raw, TODAY, PRODUCTS, INGREDIENTS)
    assert not outcome.confirm_allowed
    assert outcome.records[0].quantity is None


def test_mixed_language_with_mocked_llm():
    fake = FakeLLM(
        {
            "events": [{"kind": "sale", "item_name": "chicken kota", "quantity": 12}],
            "needs_clarification": False,
        }
    )
    outcome = asyncio.run(
        parse_with_llm("ngithengise 12 chicken kota", fake, TODAY, PRODUCTS, INGREDIENTS)
    )
    assert outcome.confirm_allowed
    assert outcome.records[0].item_name == "chicken kota"
    assert outcome.records[0].quantity == Decimal("12")


def test_comma_separated_sale_still_matches_the_menu():
    outcome = extract_heuristic("sold 12 chicken kota, 8 russian", TODAY, PRODUCTS, INGREDIENTS)
    assert outcome.confirm_allowed
    assert [record.item_name for record in outcome.records] == ["chicken kota", "russian kota"]
    assert [record.quantity for record in outcome.records] == [Decimal("12"), Decimal("8")]


SUMMARY = (
    "Date: 2026-02-01 (Sunday) | Sold: 44 Classic Kotas, 34 Russian & Cheese, "
    "24 Full House (Total: 102) | Used: 20.4 loaves bread (1.0 spoilage), 8.16kg polony, 71 russians"
)


def test_dated_sold_line_keeps_the_date_and_ignores_the_total():
    outcome = extract_heuristic(SUMMARY, TODAY, [], [])
    assert outcome.confirm_allowed
    sales = [record for record in outcome.records if record.kind == "sale"]
    assert [(record.item_name, record.quantity, record.record_date) for record in sales] == [
        ("classic kota", Decimal("44"), date(2026, 2, 1)),
        ("russian cheese kota", Decimal("34"), date(2026, 2, 1)),
        ("full house kota", Decimal("24"), date(2026, 2, 1)),
    ]
    assert all(record.unit_price is None for record in sales)
    assert "2026-02-01" in outcome.read_back
    assert "102" not in outcome.read_back
    assert "YES" in outcome.read_back
    usage = {record.item_name: record.quantity for record in outcome.records if record.kind == "usage"}
    assert usage == {
        "bread loaf": Decimal("20.4"),
        "polony": Decimal("8.16"),
        "russian sausage": Decimal("71"),
    }
    waste = [record for record in outcome.records if record.kind == "waste"]
    assert len(waste) == 1
    assert waste[0].item_name == "bread loaf"
    assert waste[0].quantity == Decimal("1.0")
    assert waste[0].reason == "spoiled"
    assert waste[0].record_date == date(2026, 2, 1)


def test_yes_and_no_words():
    assert is_yes("YES")
    assert is_yes("ja")
    assert is_yes("yebo sharp")
    assert is_yes("ewe")
    assert not is_yes("yes I sold 12 chicken kota")
    assert is_no("no")
    assert is_no("hayi")
    assert is_no("nee")
    assert not is_no("no I bought bread")
