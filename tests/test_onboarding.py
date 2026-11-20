import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.llm.prompt import load_system_prompt
from app.modules.onboarding import business_name_from, menu_from, name_to_save
from app.orchestrator.handle import HELP, SYSTEM, TOOLS, _dispatch, build_reply
from app.webhook.parse import InboundEvent
from app.webhook.process import _reply_text

NAME = "Bra Kota Nate\nIs my business name"
CSV_QUESTION = "Can I send through my csv sheet"
SUMMARY = (
    "Date: 2026-02-01 (Sunday) | Sold: 44 Classic Kotas, 34 Russian & Cheese, "
    "24 Full House (Total: 102) | Used: 20.4 loaves bread (1.0 spoilage), 8.16kg polony, 71 russians"
)
SHEET = "2026-02-01,Sunday,1,44,34,24,102,20.4,1.0,8.16,71\n"


class BoomLLM:
    configured = True

    async def chat(self, *args, **kwargs):
        raise AssertionError("the model should not see a message the shop rules already understand")


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Conn:
    def __init__(self, demo=False):
        self.demo = demo
        self.statements = []

    def transaction(self):
        return _CM(None)

    async def execute(self, sql, params=()):
        text = " ".join(sql.split())
        self.statements.append((text, params))
        lowered = text.lower()
        if "from shops" in lowered:
            return Cursor({"is_demo": self.demo})
        if "insert into products" in lowered:
            return Cursor({"name": params[1]})
        if "from pending_confirmations" in lowered and "for update" in lowered:
            return Cursor(None)
        return Cursor(None)


class _CM:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def connection(self):
        return _CM(self.conn)


def _ctx(text, conn, llm=None):
    return SimpleNamespace(
        text=text,
        consent_at=object(),
        llm=llm or BoomLLM(),
        today=date(2026, 11, 19),
        products=[],
        ingredients=[],
        pool=Pool(conn),
        shop_id="shop-owner",
        user_id="user-1",
        message_id="msg-1",
        now=None,
        delete_after_send=False,
    )


def test_business_name_phrases():
    assert business_name_from(NAME) == "Bra Kota Nate"
    assert business_name_from("my shop is called Bra Kota Nate") == "Bra Kota Nate"
    assert business_name_from("business name: Bra Kota Nate") == "Bra Kota Nate"
    assert business_name_from("sold 12 chicken kota") is None
    assert name_to_save("We're called Bra Kota Nate", "Bra Kota Nate") == "Bra Kota Nate"
    assert name_to_save("hello", "Some Other Shop") is None


def test_menu_phrase_keeps_russian_and_cheese_together():
    assert menu_from("my products are classic kota, russian and cheese, and full house") == [
        "classic kota",
        "russian and cheese",
        "full house",
    ]


def test_help_mentions_the_shop_name_and_csv():
    lowered = HELP.lower()
    assert "business name" in lowered
    assert "csv" in lowered
    assert "yes" in lowered
    assert "sold 12 chicken kota" in HELP


def test_screenshot_messages_do_not_fall_through_to_help():
    conn = Conn()

    name_reply = asyncio.run(build_reply(_ctx(NAME, conn)))
    assert "Bra Kota Nate" in name_reply
    assert "Saved" in name_reply
    assert "Send me what happened" not in name_reply

    csv_reply = asyncio.run(build_reply(_ctx(CSV_QUESTION, Conn())))
    assert "paste" in csv_reply.lower()
    assert "YES" in csv_reply
    assert "Send me what happened" not in csv_reply

    sheet_conn = Conn()
    sheet_reply = asyncio.run(build_reply(_ctx(SHEET, sheet_conn)))
    assert "YES" in sheet_reply
    assert "classic kota" in sheet_reply
    assert "2026-02-01" in sheet_reply
    assert any("pending_confirmations" in sql.lower() for sql, _ in sheet_conn.statements)
    assert not any(sql.lower().startswith("insert into sales_entries") for sql, _ in sheet_conn.statements)

    summary_reply = asyncio.run(build_reply(_ctx(SUMMARY, Conn())))
    assert "YES" in summary_reply
    assert "44" in summary_reply
    assert "2026-02-01" in summary_reply
    assert "102" not in summary_reply

    menu_reply = asyncio.run(build_reply(_ctx("my products are classic kota, russian and cheese, full house", Conn())))
    assert "classic kota" in menu_reply
    assert "russian cheese kota" in menu_reply
    assert "Saved your menu" in menu_reply


def test_yes_delete_and_plain_help_still_bypass_the_model():
    waiting = asyncio.run(build_reply(_ctx("YES", Conn())))
    assert "waiting" in waiting.lower()

    delete_reply = asyncio.run(build_reply(_ctx("DELETE MY DATA", Conn())))
    assert "YES" in delete_reply
    assert "delete" in delete_reply.lower()

    quiet = SimpleNamespace(**{**_ctx("hello there", Conn()).__dict__})
    quiet.llm = SimpleNamespace(configured=False)
    help_reply = asyncio.run(build_reply(quiet))
    assert "business name" in help_reply.lower()
    assert "csv" in help_reply.lower()


def test_demo_shop_is_not_renamed_or_loaded():
    conn = Conn(demo=True)
    ctx = _ctx("my shop is called Bra Kota Nate", conn)
    ctx.shop_id = "demo-kota"
    reply = asyncio.run(build_reply(ctx))
    assert "demo" in reply.lower()
    assert not any("update shops" in sql.lower() for sql, _ in conn.statements)

    sheet_conn = Conn(demo=True)
    sheet_ctx = _ctx(SHEET, sheet_conn)
    sheet_ctx.shop_id = "demo-kota"
    sheet_reply = asyncio.run(build_reply(sheet_ctx))
    assert "demo" in sheet_reply.lower()
    assert not any("pending_confirmations" in sql.lower() and "insert" in sql.lower() for sql, _ in sheet_conn.statements)


def test_llm_tools_save_a_grounded_name_and_explain_csv():
    conn = Conn()
    ctx = _ctx("Please use Bra Kota Nate for the shop", conn)
    reply = asyncio.run(_dispatch(ctx, "set_shop_name", {"name": "Bra Kota Nate"}))
    assert reply["final"] is True
    assert "Bra Kota Nate" in reply["reply"]
    assert any("update shops" in sql.lower() for sql, _ in conn.statements)

    invented = asyncio.run(_dispatch(_ctx("hello", Conn()), "set_shop_name", {"name": "Other Place"}))
    assert "business name" in invented["reply"].lower()
    assert "Other Place" not in invented["reply"]

    csv_tool = asyncio.run(_dispatch(_ctx("please import my sheet", Conn()), "propose_csv_import", {}))
    assert "YES" in csv_tool["reply"]
    assert "paste" in csv_tool["reply"].lower()


def test_attached_csv_is_read_for_the_sender():
    event = InboundEvent(
        channel="whatsapp",
        external_id="wamid.doc",
        sender_id="27820001111",
        sender_name="Nate",
        sent_at=datetime.now(timezone.utc),
        type="document",
        filename="kota.csv",
        mime_type="text/csv",
        media_id="doc-1",
    )
    conn = Conn()
    reply = asyncio.run(_reply_text(event, _ctx("", conn), SHEET.encode()))
    assert "YES" in reply
    assert "classic kota" in reply
    assert not any(sql.lower().startswith("insert into sales_entries") for sql, _ in conn.statements)

    other = InboundEvent(
        channel="whatsapp",
        external_id="wamid.pdf",
        sender_id="27820001111",
        sender_name="Nate",
        sent_at=datetime.now(timezone.utc),
        type="document",
        filename="notes.pdf",
        mime_type="application/pdf",
    )
    pdf_reply = asyncio.run(_reply_text(other, _ctx("", Conn()), b"%PDF-1.4 not a sheet"))
    assert "csv" in pdf_reply.lower()


def test_system_prompt_is_one_voice_and_matches_the_tools():
    text = load_system_prompt()
    lowered = text.lower()
    assert text == SYSTEM
    assert "whatsapp" in lowered
    assert "township" in lowered or "kota" in lowered
    assert "yes" in lowered
    assert "rand" in lowered
    assert "never invent" in lowered
    assert "missing" in lowered
    assert "promo" in lowered and "draft" in lowered
    assert "database id" in lowered or "shop id" in lowered
    assert "propose_records" in text
    assert "confirm_latest" in text
    assert "set_shop_name" in text
    assert "set_menu" in text
    assert "propose_csv_import" in text
    assert "/home/box" not in text
    assert "DATABASE_URL" not in text
    assert "kota-shop-001" not in text
    names = {tool["function"]["name"] for tool in TOOLS}
    assert {"set_shop_name", "set_menu", "propose_csv_import", "propose_records", "confirm_latest"} <= names
