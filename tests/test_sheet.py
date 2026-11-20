import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.demo.seed import load_plans
from app.modules.sheet import (
    SheetDraft,
    apply_sheet,
    document_is_csv,
    is_csv_question,
    looks_like_csv,
    parse_sheet_text,
    stitch_rows,
)

KOTA = Path("data/demo/kota_shop_feb2026.csv")
HEADERLESS = """2026-02-01,Sunday,1,44,34,24,102,20.4,1.0,8.16,71
2026-02-02,Monday,1,29,23,16,68,13.6,
4.5,5.44,47
"""


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Conn:
    def __init__(self, demo=False):
        self.demo = demo
        self.statements = []

    async def execute(self, sql, params=()):
        text = " ".join(sql.split())
        self.statements.append((text, params))
        lowered = text.lower()
        if "from shops" in lowered:
            return Cursor({"is_demo": self.demo, "name": "Shop"})
        if "insert into products" in lowered:
            return Cursor({"id": "p-" + str(params[1]), "name": params[1], "unit": "each", "usual_price": None})
        if "insert into ingredients" in lowered:
            return Cursor(
                {
                    "id": "i-" + str(params[1]),
                    "name": params[1],
                    "unit": params[2],
                    "stock_on_hand": None,
                    "latest_unit_cost": None,
                    "avg_unit_cost": None,
                    "reorder_level": None,
                    "safety_buffer": None,
                }
            )
        return Cursor(None)


def _ctx(shop_id="shop-owner"):
    return SimpleNamespace(shop_id=shop_id, message_id=None, today=date(2026, 11, 19))


def test_full_kota_sheet_matches_the_demo_plan_without_inventing_prices():
    parsed = parse_sheet_text(KOTA.read_text())
    plan = [plan for plan in load_plans() if plan.shop_id == "demo-kota"][0]
    assert parsed.error is None
    assert parsed.row_count == 28
    assert parsed.start == date(2026, 2, 1)
    assert parsed.end == date(2026, 2, 28)
    sales = {
        (date.fromisoformat(row["date"]), row["product"], Decimal(row["quantity"])) for row in parsed.sales
    }
    assert sales == set(plan.sales)
    assert all(row.get("unit_price") is None for row in parsed.sales)
    usage = {
        (
            date.fromisoformat(row["date"]),
            row["ingredient"],
            Decimal(row["quantity"]),
            Decimal(row["unit_cost"]) if row.get("unit_cost") else None,
        )
        for row in parsed.usage
    }
    assert usage == set(plan.usage)
    waste = {
        (date.fromisoformat(row["date"]), row["ingredient"], Decimal(row["quantity"])) for row in parsed.waste
    }
    assert waste == set(plan.waste)
    prices = {
        (date.fromisoformat(row["date"]), row["item"], row["unit"], Decimal(row["unit_price"]))
        for row in parsed.prices
    }
    assert prices == set(plan.prices)


def test_headerless_wrapped_kota_rows():
    rows = stitch_rows(HEADERLESS)
    assert len(rows) == 2
    assert rows[0].endswith("71")
    assert rows[1].endswith("47")
    parsed = parse_sheet_text(HEADERLESS)
    assert parsed.row_count == 2
    assert parsed.start == date(2026, 2, 1)
    assert parsed.end == date(2026, 2, 2)
    classic = [row for row in parsed.sales if row["product"] == "classic kota"]
    assert [row["quantity"] for row in classic] == ["44", "29"]
    assert any(row["ingredient"] == "bread loaf" and row["quantity"] == "1.0" for row in parsed.waste)
    assert not parsed.prices
    text = SheetDraft("shop-owner", parsed).read_back
    assert "2 rows" in text
    assert "2026-02-01 to 2026-02-02" in text
    assert "classic kota" in text
    assert "YES" in text
    assert "demo-kota" not in text


def test_csv_question_is_not_treated_as_a_sheet():
    question = "Can I send through my csv sheet"
    assert is_csv_question(question)
    assert not looks_like_csv(question)
    assert parse_sheet_text(question) is None


def test_document_csv_detection_uses_the_file_not_a_demo_name():
    body = b"2026-02-01,Sunday,1,44,34,24,102,20.4,1.0,8.16,71\n"
    assert document_is_csv("sales.csv", "application/octet-stream", body)
    assert document_is_csv(None, "text/csv", b"not really")
    assert not document_is_csv("photo.jpg", "image/jpeg", b"\xff\xd8not a sheet")


def test_confirm_writes_only_the_sender_shop_and_replaces_the_same_days():
    parsed = parse_sheet_text(HEADERLESS)
    payload = SheetDraft("shop-owner", parsed).as_payload()
    conn = Conn()
    first = asyncio.run(apply_sheet(conn, _ctx(), payload))
    assert first.saved
    inserts = [item for item in conn.statements if item[0].lower().startswith("insert into sales_entries")]
    deletes = [item for item in conn.statements if item[0].lower().startswith("delete from sales_entries")]
    assert inserts and deletes
    assert {params[0] for _, params in inserts} == {"shop-owner"}
    assert all("demo-kota" not in text and "demo-kota" not in str(params) for text, params in conn.statements)
    again = Conn()
    second = asyncio.run(apply_sheet(again, _ctx(), payload))
    assert second.saved
    assert any(text.lower().startswith("delete from sales_entries") for text, _ in again.statements)


def test_yes_confirms_the_sheet_onto_the_sender_shop():
    from app.modules.intake import confirm

    parsed = parse_sheet_text(HEADERLESS)
    payload = SheetDraft("shop-owner", parsed).as_payload()
    conn = Conn()
    conn.pending = {"id": "pend-1", "payload": payload, "read_back": payload and "back"}

    async def execute(sql, params=()):
        text = " ".join(sql.split())
        conn.statements.append((text, params))
        lowered = text.lower()
        if "for update" in lowered:
            return Cursor(conn.pending)
        if "from shops" in lowered:
            return Cursor({"is_demo": False, "name": "Shop"})
        if "insert into products" in lowered:
            return Cursor({"id": "p-" + str(params[1]), "name": params[1], "unit": "each", "usual_price": None})
        if "insert into ingredients" in lowered:
            return Cursor(
                {
                    "id": "i-" + str(params[1]),
                    "name": params[1],
                    "unit": params[2],
                    "stock_on_hand": None,
                    "latest_unit_cost": None,
                    "avg_unit_cost": None,
                    "reorder_level": None,
                    "safety_buffer": None,
                }
            )
        return Cursor(None)

    conn.execute = execute
    message = asyncio.run(confirm(conn, _ctx()))
    assert message.startswith("Saved 2 rows")
    assert "shop-owner" not in message
    assert any(
        text.lower().startswith("update pending_confirmations") and params[0] == "confirmed"
        for text, params in conn.statements
    )
    assert any(text.lower().startswith("insert into sales_entries") and params[0] == "shop-owner" for text, params in conn.statements)


def test_dates_without_the_kota_layout_ask_for_a_header():
    parsed = parse_sheet_text("2026-02-01,44,34,24\n")
    assert parsed is not None and parsed.error
    assert "Date" in parsed.error


def test_sheet_refuses_a_demo_shop_and_a_mismatched_shop():
    parsed = parse_sheet_text(HEADERLESS)
    payload = SheetDraft("shop-owner", parsed).as_payload()
    demo = Conn(demo=True)
    refused = asyncio.run(apply_sheet(demo, _ctx("demo-kota"), payload | {"shop_id": "demo-kota"}))
    assert not refused.saved
    assert "demo" in refused.message.lower()
    assert not any("sales_entries" in text.lower() for text, _ in demo.statements)

    mismatched = Conn()
    other = asyncio.run(apply_sheet(mismatched, _ctx("shop-owner"), payload | {"shop_id": "demo-kota"}))
    assert not other.saved
    assert not any("sales_entries" in text.lower() for text, _ in mismatched.statements)
