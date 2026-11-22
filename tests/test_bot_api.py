"""Token, reads, and writes for the bot API. The database is an in-memory stand-in."""

import logging
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.bot.guard import reset_limits
from app.bot.guard import MAX_BODY as GUARD_MAX_BODY
from app.config import Settings
from app.main import create_app
from app.timeutil import JOHANNESBURG

TOKEN = "test-token-value"
OTHER = "shop-b"
PROD_A = "11111111-1111-4111-8111-111111111111"
PROD_B = "22222222-2222-4222-8222-222222222222"
ING_A = "33333333-3333-4333-8333-333333333333"
ING_B = "44444444-4444-4444-8444-444444444444"
ING_OIL = "55555555-5555-4555-8555-555555555555"


@pytest.fixture(autouse=True)
def _limits():
    reset_limits()
    yield
    reset_limits()


class Cursor:
    def __init__(self, row=None, rows=None, rowcount=1):
        if rows is None:
            rows = [] if row is None else [row]
        self._rows = rows
        self._row = row if row is not None else (rows[0] if rows else None)
        self.rowcount = rowcount

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _CM:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _json_obj(value):
    return getattr(value, "obj", value)


class Memory:
    def __init__(self):
        self.statements = []
        self.n = 0
        when = datetime(2026, 11, 20, 9, 0, tzinfo=JOHANNESBURG)
        older = datetime(2026, 11, 1, 9, 0, tzinfo=JOHANNESBURG)
        self.shops = {
            "shop-a": _shop("shop-a", "Shop A", False),
            "shop-b": _shop("shop-b", "Shop B", False),
            "demo-kota": _shop("demo-kota", "Demo Kota", True),
            "demo-extra": _shop("demo-extra", "Prefixed", False),
            "flagged-demo": _shop("flagged-demo", "Flagged", True),
        }
        self.products = [
            _product(PROD_A, "shop-a", "classic kota"),
            _product(PROD_B, "shop-b", "braid"),
        ]
        self.ingredients = {
            ING_A: _ingredient(ING_A, "shop-a", "bread loaf", Decimal("10")),
            ING_B: _ingredient(ING_B, "shop-b", "petrol", Decimal("4")),
            ING_OIL: _ingredient(ING_OIL, "shop-a", "oil", None),
        }
        self.messages = [
            _message("wa-new", "shop-a", "whatsapp", "sold 2 classic kota", when),
            _message("wa-old", "shop-a", "whatsapp", "old note", older),
            _message("ig-a", "shop-a", "instagram", "instagram only", when),
            _message("wa-b", "shop-b", "whatsapp", "shop-b-secret", when),
        ]
        self.sales = [
            _sale("sale-a", "shop-a", PROD_A, "classic kota", Decimal("2"), date(2026, 11, 20)),
            _sale("sale-b", "shop-b", PROD_B, "braid", Decimal("1"), date(2026, 11, 20)),
        ]
        self.expenses = []
        self.movements = [
            _movement("mov-a", "shop-a", ING_A, "bread loaf", Decimal("-1"), Decimal("9")),
            _movement("mov-b", "shop-b", ING_B, "petrol", Decimal("-1"), Decimal("3")),
        ]
        self.revenue = [
            {"id": "rev-a", "shop_id": "shop-a", "revenue_date": date(2026, 11, 20), "amount": Decimal("500")},
            {"id": "rev-b", "shop_id": "shop-b", "revenue_date": date(2026, 11, 20), "amount": Decimal("99")},
        ]
        self.invoices = [
            _invoice("inv-a", "shop-a", "Boxer", Decimal("575")),
            _invoice("inv-b", "shop-b", "Other Supplier", Decimal("10")),
        ]
        self.lines = [
            {
                "id": "line-a",
                "invoice_id": "inv-a",
                "shop_id": "shop-a",
                "line_no": 1,
                "description": "polony",
                "quantity": Decimal("2"),
                "unit": "kg",
                "unit_price": Decimal("40"),
                "line_total": Decimal("80"),
                "confidence": "high",
                "confirmed": True,
            }
        ]
        self.drafts = [
            {
                "id": "draft-a",
                "shop_id": "shop-a",
                "kind": "sale",
                "status": "pending",
                "read_back": "Sold classic kota.",
                "payload": {
                    "item_name": "classic kota",
                    "phone": "27820001111",
                    "token": "should-not-leak",
                    "nested": {"api_key": "hidden-key", "ok": True},
                },
                "created_at": when,
            },
            {
                "id": "draft-delete",
                "shop_id": "shop-a",
                "kind": "other",
                "status": "pending",
                "read_back": "Delete?",
                "payload": {"action": "delete_shop_data", "phone": "27829999999", "note": "gone"},
                "created_at": older,
            },
            {
                "id": "draft-b",
                "shop_id": "shop-b",
                "kind": "sale",
                "status": "pending",
                "read_back": "shop-b draft",
                "payload": {"item_name": "braid"},
                "created_at": when,
            },
            {
                "id": "draft-done",
                "shop_id": "shop-a",
                "kind": "sale",
                "status": "confirmed",
                "read_back": "already confirmed",
                "payload": {"item_name": "confirmed kota"},
                "created_at": when,
            },
        ]
        self.forecasts = [
            _forecast("fc-a", "shop-a", PROD_A, "classic kota", Decimal("8")),
            _forecast("fc-b", "shop-b", PROD_B, "braid", Decimal("3")),
        ]
        self.targets = []

    def next_id(self, prefix):
        self.n += 1
        return f"{prefix}-{self.n}"

    def transaction(self):
        return _CM(None)

    async def execute(self, sql, params=()):
        text = " ".join(sql.split())
        lowered = text.lower()
        self.statements.append((text, params))
        if " from users" in lowered or "consent" in lowered or "whatsapp_wa_id" in lowered:
            raise AssertionError(text)
        if "file_url" in lowered or " raw" in lowered or ",raw" in lowered:
            raise AssertionError(text)
        row, rows, count = self._dispatch(lowered, params)
        return Cursor(row=row, rows=rows, rowcount=count)

    def _dispatch(self, lowered, params):
        if lowered.startswith("insert into products"):
            return self._insert_product(params), None, 1
        if lowered.startswith("insert into messages"):
            return self._insert_message(params), None, 1
        if lowered.startswith("update messages"):
            self._update_message(params)
            return None, [], 1
        if lowered.startswith("insert into sales_entries"):
            return self._insert_sale(params), None, 1
        if lowered.startswith("insert into expenses"):
            self.expenses.append(
                {"shop_id": params[0], "source_message_id": params[5], "category": params[3]}
            )
            return None, [], 1
        if lowered.startswith("insert into stock_movements"):
            return self._insert_movement(params), None, 1
        if lowered.startswith("update ingredients"):
            self._update_stock(params)
            return None, [], 1
        if lowered.startswith("insert into forecasts"):
            return self._upsert_forecast(params), None, 1
        if lowered.startswith("insert into pending_confirmations"):
            return self._insert_draft(params), None, 1
        if "from recipes" in lowered:
            return None, [], 1
        if "from shops" in lowered and "where id" in lowered:
            return self.shops.get(params[0]), None, 1
        if "from shops" in lowered:
            limit, offset = _page(params)
            rows = list(self.shops.values())
            return None, rows[offset : offset + limit], 1
        if "from products" in lowered and " where " in lowered and " and id " in lowered:
            return self._one_product(params, by="id"), None, 1
        if "from products" in lowered and " and name " in lowered:
            return self._one_product(params, by="name"), None, 1
        if "from products" in lowered:
            shop_id = _shop_param(params, self.shops)
            rows = [row for row in self.products if row["shop_id"] == shop_id and row["is_active"]]
            return None, rows, 1
        if "from messages" in lowered and "external_id" in lowered:
            return self._message_by_external(params), None, 1
        if "from messages" in lowered and " where id " in lowered:
            return self._message_by_id(params), None, 1
        if "from messages" in lowered:
            return None, self._list_messages(lowered, params), 1
        if "from sales_entries" in lowered and "source_message_id" in lowered:
            return self._linked(self.sales, params), None, 1
        if "from expenses" in lowered and "source_message_id" in lowered:
            return self._linked(self.expenses, params), None, 1
        if "from sales_entries" in lowered:
            return None, self._list_dated(self.sales, params), 1
        if "from ingredients" in lowered and "for update" in lowered:
            return self._lock_ingredient(lowered, params), None, 1
        if "from ingredients" in lowered:
            return None, self._list_ingredients(params), 1
        if "from stock_movements" in lowered and "ref_table" in lowered:
            return self._movement_by_ref(params), None, 1
        if "from stock_movements" in lowered:
            return None, self._list_dated(self.movements, params), 1
        if "from daily_revenue" in lowered:
            return None, self._list_dated(self.revenue, params, "revenue_date"), 1
        if "from invoices" in lowered:
            return None, self._list_dated(self.invoices, params, "invoice_date"), 1
        if "from invoice_lines" in lowered:
            return None, self._lines(params), 1
        if "from pending_confirmations" in lowered and "bot_idempotency_key" in lowered:
            return self._draft_by_key(params), None, 1
        if "from pending_confirmations" in lowered:
            return None, self._list_drafts(params), 1
        if "from forecasts" in lowered and lowered.startswith("select"):
            return None, self._list_dated(self.forecasts, params, "target_date"), 1
        if "from sales_targets" in lowered:
            return None, [], 1
        return None, [], 1

    def _insert_product(self, params):
        shop_id, name, unit = params[:3]
        for row in self.products:
            if row["shop_id"] == shop_id and row["name"] == name:
                return row
        row = _product(self.next_id("prod"), shop_id, name, unit)
        self.products.append(row)
        return row

    def _insert_message(self, params):
        shop_id, external_id = params[0], params[1]
        for row in self.messages:
            if row.get("external_id") == external_id:
                return None
        row = {
            "id": self.next_id("msg"),
            "shop_id": shop_id,
            "user_id": None,
            "direction": "inbound",
            "channel": "bot",
            "body": None,
            "sent_at": datetime(2026, 11, 22, 12, tzinfo=JOHANNESBURG),
            "external_id": external_id,
        }
        self.messages.append(row)
        return {"id": row["id"]}

    def _update_message(self, params):
        body, message_id, shop_id = params
        for row in self.messages:
            if row["id"] == message_id and row["shop_id"] == shop_id:
                row["body"] = body

    def _message_by_external(self, params):
        external_id = params[0]
        for row in self.messages:
            if row.get("external_id") == external_id:
                return {"id": row["id"], "shop_id": row["shop_id"], "body": row.get("body")}
        return None

    def _message_by_id(self, params):
        message_id, shop_id = params
        for row in self.messages:
            if row["id"] == message_id and row["shop_id"] == shop_id:
                return {"body": row.get("body")}
        return None

    def _list_messages(self, lowered, params):
        shop_id = params[0]
        channel = params[1]
        since = params[3]
        until = params[5]
        limit, offset = _page(params)
        rows = []
        for row in self.messages:
            if row["shop_id"] != shop_id:
                continue
            if "channel = %s" in lowered and channel is not None and row["channel"] != channel:
                continue
            sent = row["sent_at"]
            if "sent_at >=" in lowered and since is not None and sent < since:
                continue
            if "sent_at <" in lowered and until is not None and sent >= until:
                continue
            rows.append(row)
        rows.sort(key=lambda row: (row["sent_at"], row["id"]), reverse=True)
        page = rows[offset : offset + limit]
        return [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "direction": row["direction"],
                "channel": row["channel"],
                "body": row["body"],
                "sent_at": row["sent_at"],
                "raw": {"token": "leak-raw"},
                "phone": "27820001111",
            }
            for row in page
        ]

    def _insert_sale(self, params):
        shop_id, sale_date, product_id, quantity, unit_price, message_id, sale_hour = params
        product = next(row for row in self.products if row["id"] == product_id)
        row = {
            "id": self.next_id("sale"),
            "shop_id": shop_id,
            "sale_date": sale_date,
            "product_id": product_id,
            "product_name": product["name"],
            "quantity": quantity,
            "unit_price": unit_price,
            "sale_hour": sale_hour,
            "source_message_id": message_id,
            "created_at": datetime(2026, 11, 22, 12, tzinfo=JOHANNESBURG),
        }
        self.sales.append(row)
        return {"id": row["id"]}

    def _linked(self, rows, params):
        shop_id, message_id = params
        for row in rows:
            if row["shop_id"] == shop_id and row.get("source_message_id") == message_id:
                return {"ok": 1}
        return None

    def _list_dated(self, rows, params, field=None):
        shop_id = params[0]
        start = params[1]
        end = params[3]
        limit, offset = _page(params)
        picked = []
        for row in rows:
            if row["shop_id"] != shop_id:
                continue
            if field is None:
                when = row.get("sale_date") or row.get("movement_date")
            else:
                when = row.get(field)
            if start is not None and when is not None and when < start:
                continue
            if end is not None and when is not None and when > end:
                continue
            picked.append(row)
        return picked[offset : offset + limit]

    def _lock_ingredient(self, lowered, params):
        shop_id = params[0]
        if " and id " in lowered:
            wanted = params[1]
            row = self.ingredients.get(wanted)
            if row and row["shop_id"] == shop_id:
                return dict(row)
            return None
        name = params[1]
        for row in self.ingredients.values():
            if row["shop_id"] == shop_id and row["name"] == name:
                return dict(row)
        return None

    def _list_ingredients(self, params):
        shop_id = params[0]
        limit, offset = _page(params)
        rows = [row for row in self.ingredients.values() if row["shop_id"] == shop_id]
        rows.sort(key=lambda row: row["name"])
        return rows[offset : offset + limit]

    def _update_stock(self, params):
        stock_after, ingredient_id, shop_id = params
        row = self.ingredients.get(ingredient_id)
        if row and row["shop_id"] == shop_id:
            row["stock_on_hand"] = stock_after

    def _movement_by_ref(self, params):
        shop_id, ref_id, ingredient_id = params
        for row in self.movements:
            if (
                row["shop_id"] == shop_id
                and row.get("ref_id") == ref_id
                and row["ingredient_id"] == ingredient_id
            ):
                return dict(row)
        return None

    def _insert_movement(self, params):
        shop_id, ingredient_id, movement_date, movement_type, qty_change, unit_cost, ref_id, stock_after = params
        ingredient = self.ingredients[ingredient_id]
        row = {
            "id": self.next_id("mov"),
            "shop_id": shop_id,
            "ingredient_id": ingredient_id,
            "ingredient_name": ingredient["name"],
            "movement_date": movement_date,
            "movement_type": movement_type,
            "qty_change": qty_change,
            "unit_cost": unit_cost,
            "stock_after": stock_after,
            "ref_id": ref_id,
            "created_at": datetime(2026, 11, 22, 12, tzinfo=JOHANNESBURG),
        }
        self.movements.append(row)
        return row

    def _upsert_forecast(self, params):
        (
            shop_id,
            target,
            product_id,
            predicted,
            low,
            high,
            method,
            confidence,
            reason,
            expected,
            early,
            day_type,
            adjustments,
        ) = params
        product = next(row for row in self.products if row["id"] == product_id and row["shop_id"] == shop_id)
        for row in self.forecasts:
            if (
                row["shop_id"] == shop_id
                and row["target_date"] == target
                and row["product_id"] == product_id
                and row["method"] == method
            ):
                row.update(
                    {
                        "predicted_qty": predicted,
                        "range_low": low,
                        "range_high": high,
                        "confidence": confidence,
                        "reason": reason,
                        "expected_revenue": expected,
                        "is_early_estimate": early,
                        "day_type": day_type,
                        "adjustments": adjustments,
                        "product_name": product["name"],
                    }
                )
                return row
        row = _forecast(self.next_id("fc"), shop_id, product_id, product["name"], predicted)
        row.update(
            {
                "target_date": target,
                "range_low": low,
                "range_high": high,
                "method": method,
                "confidence": confidence,
                "reason": reason,
                "expected_revenue": expected,
                "is_early_estimate": early,
                "day_type": day_type,
                "adjustments": adjustments,
            }
        )
        self.forecasts.append(row)
        return row

    def _insert_draft(self, params):
        shop_id, kind, payload, read_back = params
        row = {
            "id": self.next_id("draft"),
            "shop_id": shop_id,
            "kind": kind,
            "status": "pending",
            "read_back": read_back,
            "payload": _json_obj(payload),
            "created_at": datetime(2026, 11, 22, 12, tzinfo=JOHANNESBURG),
        }
        self.drafts.append(row)
        return row

    def _draft_by_key(self, params):
        shop_id, key = params
        for row in self.drafts:
            payload = row.get("payload") or {}
            if (
                row["shop_id"] == shop_id
                and row["status"] == "pending"
                and payload.get("bot_idempotency_key") == key
            ):
                return row
        return None

    def _list_drafts(self, params):
        shop_id, status, limit, offset = params[0], params[1], params[2], params[3]
        rows = [row for row in self.drafts if row["shop_id"] == shop_id and row["status"] == status]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows[offset : offset + limit]

    def _lines(self, params):
        shop_id, invoice_ids = params
        wanted = {str(item) for item in invoice_ids}
        return [
            row
            for row in self.lines
            if row["shop_id"] == shop_id and str(row["invoice_id"]) in wanted
        ]

    def _one_product(self, params, by):
        shop_id = params[0]
        wanted = str(params[1])
        for row in self.products:
            if row["shop_id"] == shop_id and str(row[by]) == wanted:
                return row
        return None


def _shop(shop_id, name, is_demo):
    return {
        "id": shop_id,
        "name": name,
        "business_type": "kota",
        "timezone": "Africa/Johannesburg",
        "currency": "ZAR",
        "is_demo": is_demo,
        "demo_as_of": date(2026, 11, 19) if is_demo else None,
        "created_at": datetime(2026, 11, 1, tzinfo=JOHANNESBURG),
    }


def _product(product_id, shop_id, name, unit="each"):
    return {
        "id": product_id,
        "shop_id": shop_id,
        "name": name,
        "unit": unit,
        "usual_price": None,
        "is_active": True,
    }


def _ingredient(ingredient_id, shop_id, name, stock):
    return {
        "id": ingredient_id,
        "shop_id": shop_id,
        "name": name,
        "unit": "each",
        "stock_on_hand": stock,
        "reorder_level": Decimal("2"),
        "safety_buffer": None,
        "latest_unit_cost": Decimal("5"),
        "avg_unit_cost": Decimal("4"),
        "pack_size": None,
    }


def _message(message_id, shop_id, channel, body, sent_at):
    return {
        "id": message_id,
        "shop_id": shop_id,
        "user_id": "user-" + shop_id,
        "direction": "inbound",
        "channel": channel,
        "body": body,
        "sent_at": sent_at,
        "external_id": None,
        "body_saved": None,
    }


def _sale(sale_id, shop_id, product_id, name, quantity, sale_date):
    return {
        "id": sale_id,
        "shop_id": shop_id,
        "sale_date": sale_date,
        "product_id": product_id,
        "product_name": name,
        "quantity": quantity,
        "unit_price": Decimal("25"),
        "sale_hour": 12,
        "source_message_id": None,
        "created_at": datetime(2026, 11, 20, 10, tzinfo=JOHANNESBURG),
    }


def _movement(movement_id, shop_id, ingredient_id, name, qty, stock_after):
    return {
        "id": movement_id,
        "shop_id": shop_id,
        "ingredient_id": ingredient_id,
        "ingredient_name": name,
        "movement_date": date(2026, 11, 20),
        "movement_type": "sale_deduction",
        "qty_change": qty,
        "unit_cost": None,
        "stock_after": stock_after,
        "ref_id": None,
        "created_at": datetime(2026, 11, 20, 10, tzinfo=JOHANNESBURG),
    }


def _invoice(invoice_id, shop_id, supplier, total):
    return {
        "id": invoice_id,
        "shop_id": shop_id,
        "supplier": supplier,
        "invoice_number": "1",
        "invoice_date": date(2026, 11, 20),
        "subtotal": Decimal("500"),
        "vat": Decimal("75"),
        "discount": None,
        "total": total,
        "confirmed": True,
        "delivery_status": None,
        "flags": ["vat_mismatch"],
        "created_at": datetime(2026, 11, 20, 10, tzinfo=JOHANNESBURG),
        "file_url": "secret-file",
    }


def _forecast(forecast_id, shop_id, product_id, name, predicted):
    return {
        "id": forecast_id,
        "shop_id": shop_id,
        "target_date": date(2026, 11, 21),
        "product_id": product_id,
        "product_name": name,
        "predicted_qty": predicted,
        "range_low": Decimal("1"),
        "range_high": Decimal("9"),
        "method": "weekday_average",
        "confidence": "low",
        "reason": "Average",
        "expected_revenue": None,
        "is_early_estimate": True,
        "day_type": None,
        "adjustments": [],
        "created_at": datetime(2026, 11, 20, 10, tzinfo=JOHANNESBURG),
    }


def _shop_param(params, shops):
    for param in params:
        if isinstance(param, str) and param in shops:
            return param
    return None


def _page(params):
    ints = [param for param in params if isinstance(param, int) and not isinstance(param, bool)]
    if len(ints) >= 2:
        return ints[-2], ints[-1]
    return 50, 0


class Pool:
    def __init__(self, memory):
        self.memory = memory

    def connection(self):
        return _CM(self.memory)

    async def close(self):
        return None


def _app(token=TOKEN):
    return create_app(
        Settings(
            meta_app_secret="test-secret",
            webhook_verify_token="verify-me",
            database_url="",
            scheduler_enabled=False,
            llm_api_key="",
            bot_api_token=token,
        )
    )


def _headers(name="Forecast"):
    return {"Authorization": f"Bearer {TOKEN}", "X-Bot-Name": name}


def _client(token=TOKEN):
    memory = Memory()
    app = _app(token)
    client = TestClient(app)
    client.__enter__()
    app.state.db = Pool(memory)
    return client, memory


def _scoped(memory, shop_id, other):
    assert memory.statements
    for _sql, params in memory.statements:
        assert other not in params
        assert shop_id in params


def test_missing_wrong_and_correct_token():
    client, memory = _client()
    try:
        missing = client.get("/bot/v1/shops")
        assert missing.status_code == 401
        assert missing.json() == {"detail": "unauthorized"}
        assert TOKEN not in missing.text
        assert memory.statements == []

        wrong = client.get("/bot/v1/shops", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        assert wrong.json() == {"detail": "unauthorized"}
        assert "nope" not in wrong.text
        assert memory.statements == []

        ok = client.get("/bot/v1/shops", headers=_headers())
        assert ok.status_code == 200
        assert ok.headers["cache-control"] == "no-store"
        names = [shop["name"] for shop in ok.json()["shops"]]
        assert "Shop A" in names and "Shop B" in names
    finally:
        client.__exit__(None, None, None)


def test_unset_token_disables_the_api_even_with_a_bearer_header():
    client, memory = _client(token="")
    try:
        response = client.get("/bot/v1/shops", headers=_headers())
        assert response.status_code == 503
        assert response.json() == {"detail": "unavailable"}
        assert TOKEN not in response.text
        assert memory.statements == []
        still = client.post("/bot/v1/shops/shop-a/sales", headers=_headers(), json={"quantity": "1"})
        assert still.status_code == 503
    finally:
        client.__exit__(None, None, None)


def test_configured_token_without_a_database_is_unavailable():
    app = _app()
    with TestClient(app) as client:
        response = client.get("/bot/v1/shops", headers=_headers())
        assert response.status_code == 503
        assert response.json() == {"detail": "unavailable"}


def test_bot_routes_stay_out_of_openapi_and_webhook_stays_public():
    app = _app()
    with TestClient(app) as client:
        schema = client.get("/openapi.json")
        assert schema.status_code == 200
        paths = schema.json()["paths"]
        assert "/health" in paths
        assert "/webhook" in paths
        assert not any(path.startswith("/bot") for path in paths)
        assert "BOT_API_TOKEN" not in schema.text
        assert TOKEN not in schema.text


def test_audit_log_uses_the_bot_name_and_not_the_token(caplog):
    client, _memory = _client()
    try:
        with caplog.at_level(logging.INFO, logger="smartstock.bot"):
            response = client.get(
                "/bot/v1/shops",
                headers=_headers("Forecast\nINFO forged"),
            )
        assert response.status_code == 200
        messages = [record.getMessage() for record in caplog.records if record.name == "smartstock.bot"]
        assert messages
        assert all("\n" not in message for message in messages)
        assert any("bot=Forecast INFO forged" in message and "status=200" in message for message in messages)
        assert TOKEN not in caplog.text
    finally:
        client.__exit__(None, None, None)


def test_rate_limit_and_body_size(monkeypatch):
    monkeypatch.setattr("app.bot.guard.MAX_REQUESTS", 2)
    client, _memory = _client()
    try:
        assert client.get("/bot/v1/shops").status_code == 401
        assert client.get("/bot/v1/shops").status_code == 401
        blocked = client.get("/bot/v1/shops")
        assert blocked.status_code == 429
        assert blocked.json() == {"detail": "too many requests"}
    finally:
        client.__exit__(None, None, None)

    reset_limits()
    monkeypatch.setattr("app.bot.guard.MAX_BODY", 16)
    client, memory = _client()
    try:
        response = client.post(
            "/bot/v1/shops/shop-a/sales",
            headers=_headers(),
            json={"item_name": "classic kota", "quantity": "1"},
        )
        assert response.status_code == 413
        assert response.json() == {"detail": "too large"}
        assert not any("insert into sales" in sql.lower() for sql, _params in memory.statements)
    finally:
        client.__exit__(None, None, None)
    assert GUARD_MAX_BODY == 256 * 1024


READS = [
    ("/bot/v1/shops/shop-a", "products", "classic kota", "braid"),
    ("/bot/v1/shops/shop-a/messages", "messages", "sold 2 classic kota", "shop-b-secret"),
    ("/bot/v1/shops/shop-a/sales", "sales", "classic kota", "braid"),
    ("/bot/v1/shops/shop-a/stock", "stock", "bread loaf", "petrol"),
    ("/bot/v1/shops/shop-a/stock/movements", "movements", "bread loaf", "petrol"),
    ("/bot/v1/shops/shop-a/revenue", "revenue", "500", "99"),
    ("/bot/v1/shops/shop-a/invoices", "invoices", "polony", "Other Supplier"),
    ("/bot/v1/shops/shop-a/drafts", "drafts", "classic kota", "shop-b draft"),
    ("/bot/v1/shops/shop-a/forecasts", "forecasts", "classic kota", "braid"),
]


@pytest.mark.parametrize("path,key,marker,other", READS)
def test_read_endpoints_are_scoped_to_the_shop(path, key, marker, other):
    client, memory = _client()
    try:
        response = client.get(path, headers=_headers())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body[key]
        assert marker in response.text
        assert other not in response.text
        assert "should-not-leak" not in response.text
        assert "leak-raw" not in response.text
        assert "27820001111" not in response.text
        assert "27829999999" not in response.text
        assert "hidden-key" not in response.text
        assert "secret-file" not in response.text
        assert "file_url" not in response.text
        assert TOKEN not in response.text
        _scoped(memory, "shop-a", OTHER)
    finally:
        client.__exit__(None, None, None)


def test_messages_filter_by_time_channel_and_page():
    client, memory = _client()
    try:
        page = client.get(
            "/bot/v1/shops/shop-a/messages",
            headers=_headers(),
            params={"since": "2026-11-15T00:00:00+02:00", "until": "2026-11-21T00:00:00+02:00", "limit": "1"},
        )
        assert page.status_code == 200, page.text
        assert [row["body"] for row in page.json()["messages"]] == ["sold 2 classic kota"]
        sql, params = memory.statements[-1]
        assert "sent_at" in sql.lower()
        assert "shop-a" in params
        assert "shop-b" not in params

        instagram = client.get(
            "/bot/v1/shops/shop-a/messages",
            headers=_headers("Intake"),
            params={"channel": "instagram"},
        )
        assert [row["body"] for row in instagram.json()["messages"]] == ["instagram only"]
        assert "raw" not in instagram.json()["messages"][0]
    finally:
        client.__exit__(None, None, None)


def test_drafts_hide_secrets_and_default_to_pending():
    client, _memory = _client()
    try:
        response = client.get("/bot/v1/shops/shop-a/drafts", headers=_headers())
        drafts = response.json()["drafts"]
        assert response.status_code == 200
        assert all(row["status"] == "pending" for row in drafts)
        assert "already confirmed" not in response.text
        deleted = next(row for row in drafts if row["kind"] == "other")
        assert deleted["payload"] == {"action": "delete_shop_data"}
        kept = next(row for row in drafts if row["id"] == "draft-a")
        assert kept["payload"]["item_name"] == "classic kota"
        assert kept["payload"]["nested"] == {"ok": True}
        assert "phone" not in kept["payload"]
    finally:
        client.__exit__(None, None, None)


def test_unknown_shop_and_bad_shop_id_do_not_query_other_shops():
    client, memory = _client()
    try:
        missing = client.get("/bot/v1/shops/missing-shop", headers=_headers())
        assert missing.status_code == 404
        assert missing.json() == {"detail": "not found"}
        odd = client.get("/bot/v1/shops/bad shop", headers=_headers())
        assert odd.status_code == 404
        assert memory.statements == [] or all("bad shop" not in params for _sql, params in memory.statements)
        assert all("shop-b" not in params for _sql, params in memory.statements)
    finally:
        client.__exit__(None, None, None)


def test_sale_write_is_scoped_idempotent_and_refuses_demo_shops():
    client, memory = _client()
    try:
        body = {
            "item_name": "classic kota",
            "quantity": "2",
            "unit_price": "25",
            "record_date": "2026-11-22",
            "sale_hour": 11,
            "idempotency_key": "sale-1",
        }
        first = client.post("/bot/v1/shops/shop-a/sales", headers=_headers("Intake"), json=body)
        assert first.status_code == 200, first.text
        assert first.json()["idempotent"] is False
        assert first.json()["kind"] == "sale"
        assert "Sold" in first.json()["note"]
        assert "classic kota" in first.json()["note"]
        sale_inserts = [params for sql, params in memory.statements if "insert into sales_entries" in sql.lower()]
        assert len(sale_inserts) == 1
        assert sale_inserts[0][0] == "shop-a"
        assert "shop-b" not in sale_inserts[0]

        again = client.post("/bot/v1/shops/shop-a/sales", headers=_headers(), json=body)
        assert again.status_code == 200, again.text
        assert again.json()["idempotent"] is True
        sale_inserts = [params for sql, params in memory.statements if "insert into sales_entries" in sql.lower()]
        assert len(sale_inserts) == 1

        other_shop = client.post("/bot/v1/shops/shop-b/sales", headers=_headers(), json=body)
        assert other_shop.status_code == 200, other_shop.text
        shops = [params[0] for sql, params in memory.statements if "insert into sales_entries" in sql.lower()]
        assert shops == ["shop-a", "shop-b"]

        refused = client.post("/bot/v1/shops/demo-kota/sales", headers=_headers(), json=body)
        assert refused.status_code == 403
        assert refused.json() == {"detail": "forbidden"}
        prefixed = client.post("/bot/v1/shops/demo-extra/sales", headers=_headers(), json=body)
        flagged = client.post("/bot/v1/shops/flagged-demo/sales", headers=_headers(), json=body)
        assert prefixed.status_code == 403
        assert flagged.status_code == 403
        assert [params[0] for sql, params in memory.statements if "insert into sales_entries" in sql.lower()] == [
            "shop-a",
            "shop-b",
        ]

        invalid = client.post(
            "/bot/v1/shops/shop-a/sales",
            headers=_headers(),
            json={"item_name": "classic kota", "quantity": "0"},
        )
        assert invalid.status_code == 422
        assert invalid.json() == {"detail": "invalid"}
        mismatch = client.post(
            "/bot/v1/shops/shop-a/sales",
            headers=_headers(),
            json={"item_name": "classic kota", "quantity": "1", "shop_id": "shop-b"},
        )
        assert mismatch.status_code == 422
        assert [params[0] for sql, params in memory.statements if "insert into sales_entries" in sql.lower()] == [
            "shop-a",
            "shop-b",
        ]
    finally:
        client.__exit__(None, None, None)


def test_record_write_uses_the_shop_and_rejects_a_bad_expense():
    client, memory = _client()
    try:
        saved = client.post(
            "/bot/v1/shops/shop-a/records",
            headers=_headers("Orchestrator"),
            json={"kind": "expense", "amount": "50", "category": "rent", "idempotency_key": "rent-1"},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["kind"] == "expense"
        assert "rent" in saved.json()["note"].lower()
        inserts = [params for sql, params in memory.statements if "insert into expenses" in sql.lower()]
        assert len(inserts) == 1
        assert inserts[0][0] == "shop-a"
        assert "shop-b" not in inserts[0]

        replay = client.post(
            "/bot/v1/shops/shop-a/records",
            headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": "rent-1", "X-Bot-Name": "Orchestrator"},
            json={"kind": "expense", "amount": "50", "category": "rent"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent"] is True
        inserts = [params for sql, params in memory.statements if "insert into expenses" in sql.lower()]
        assert len(inserts) == 1

        bad = client.post(
            "/bot/v1/shops/shop-a/records",
            headers=_headers(),
            json={"kind": "expense", "amount": "50", "category": "made-up"},
        )
        assert bad.status_code == 422
        target = client.post(
            "/bot/v1/shops/shop-a/records",
            headers=_headers(),
            json={"kind": "set_target", "amount": "1000", "period_type": "week"},
        )
        assert target.status_code == 200, target.text
        assert target.json()["kind"] == "set_target"
        target_sql = [params for sql, params in memory.statements if "insert into sales_targets" in sql.lower()]
        assert len(target_sql) == 1
        assert target_sql[0][0] == "shop-a"
        assert "shop-b" not in target_sql[0]
        purchase = client.post(
            "/bot/v1/shops/shop-a/records",
            headers=_headers(),
            json={"kind": "purchase", "item_name": "bread", "quantity": "2"},
        )
        assert purchase.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_stock_movement_is_scoped_idempotent_and_blocked_for_demo():
    client, memory = _client()
    try:
        saved = client.post(
            "/bot/v1/shops/shop-a/stock/movements",
            headers=_headers("Inventory"),
            json={
                "ingredient_id": ING_A,
                "movement_type": "sale_deduction",
                "qty_change": "-1",
                "idempotency_key": "move-1",
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["idempotent"] is False
        assert saved.json()["movement"]["ingredient_name"] == "bread loaf"
        assert saved.json()["movement"]["stock_after"] == "9"
        assert memory.ingredients[ING_A]["stock_on_hand"] == Decimal("9.000")
        assert memory.ingredients[ING_B]["stock_on_hand"] == Decimal("4")

        replay = client.post(
            "/bot/v1/shops/shop-a/stock/movements",
            headers=_headers("Inventory"),
            json={
                "ingredient_id": ING_A,
                "movement_type": "sale_deduction",
                "qty_change": "-5",
                "idempotency_key": "move-1",
            },
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent"] is True
        assert replay.json()["movement"]["qty_change"] == "-1"
        assert memory.ingredients[ING_A]["stock_on_hand"] == Decimal("9.000")

        foreign = client.post(
            "/bot/v1/shops/shop-a/stock/movements",
            headers=_headers(),
            json={"ingredient_id": ING_B, "movement_type": "manual", "qty_change": "-1"},
        )
        assert foreign.status_code == 404
        assert memory.ingredients[ING_B]["stock_on_hand"] == Decimal("4")

        too_far = client.post(
            "/bot/v1/shops/shop-a/stock/movements",
            headers=_headers(),
            json={"item_name": "bread loaf", "movement_type": "waste", "qty_change": "-100"},
        )
        assert too_far.status_code == 422
        assert memory.ingredients[ING_A]["stock_on_hand"] == Decimal("9.000")

        demo = client.post(
            "/bot/v1/shops/demo-kota/stock/movements",
            headers=_headers(),
            json={"item_name": "bread loaf", "movement_type": "manual", "qty_change": "1"},
        )
        assert demo.status_code == 403
        assert not any(
            "insert into stock_movements" in sql.lower() and params[0] == "demo-kota"
            for sql, params in memory.statements
        )
    finally:
        client.__exit__(None, None, None)


def test_forecast_and_draft_writes_stay_on_one_shop():
    client, memory = _client()
    try:
        forecast = client.post(
            "/bot/v1/shops/shop-a/forecasts",
            headers=_headers("Forecast"),
            json={
                "product_id": PROD_A,
                "target_date": "2026-11-23",
                "predicted_qty": "12",
                "range_low": "10",
                "range_high": "14",
                "method": "weekday_average",
                "confidence": "medium",
                "is_early_estimate": False,
            },
        )
        assert forecast.status_code == 200, forecast.text
        assert forecast.json()["forecast"]["product_name"] == "classic kota"
        assert forecast.json()["forecast"]["predicted_qty"] == "12"

        replaced = client.post(
            "/bot/v1/shops/shop-a/forecasts",
            headers=_headers("Forecast"),
            json={
                "product_name": "classic kota",
                "target_date": "2026-11-23",
                "predicted_qty": "6",
                "method": "weekday_average",
            },
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["forecast"]["predicted_qty"] == "6"
        stored = [
            row
            for row in memory.forecasts
            if row["shop_id"] == "shop-a" and row["target_date"] == date(2026, 11, 23)
        ]
        assert len(stored) == 1
        assert stored[0]["predicted_qty"] == Decimal("6.000")

        other_product = client.post(
            "/bot/v1/shops/shop-a/forecasts",
            headers=_headers(),
            json={
                "product_id": PROD_B,
                "target_date": "2026-11-23",
                "predicted_qty": "1",
                "method": "weekday_average",
            },
        )
        assert other_product.status_code == 404
        assert all(row["shop_id"] != "shop-a" or row["product_id"] != PROD_B for row in memory.forecasts)

        demo = client.post(
            "/bot/v1/shops/demo-kota/forecasts",
            headers=_headers(),
            json={
                "product_name": "classic kota",
                "target_date": "2026-11-23",
                "predicted_qty": "1",
                "method": "weekday_average",
            },
        )
        assert demo.status_code == 403

        draft = client.post(
            "/bot/v1/shops/shop-a/drafts",
            headers=_headers("Intake"),
            json={
                "kind": "sale",
                "read_back": "Sold 2 classic kota.",
                "idempotency_key": "draft-1",
                "payload": {"item_name": "classic kota", "phone": "27820001111", "quantity": "2"},
            },
        )
        assert draft.status_code == 200, draft.text
        assert draft.json()["idempotent"] is False
        assert "phone" not in draft.json()["draft"]["payload"]
        assert draft.json()["draft"]["payload"]["item_name"] == "classic kota"
        assert draft.json()["draft"]["payload"]["bot_idempotency_key"] == "draft-1"

        replay = client.post(
            "/bot/v1/shops/shop-a/drafts",
            headers=_headers(),
            json={"kind": "sale", "idempotency_key": "draft-1", "payload": {"item_name": "other"}},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent"] is True
        assert replay.json()["draft"]["payload"]["item_name"] == "classic kota"
        assert sum(1 for row in memory.drafts if row.get("payload", {}).get("bot_idempotency_key") == "draft-1") == 1

        deleted = client.post(
            "/bot/v1/shops/shop-a/drafts",
            headers=_headers(),
            json={"kind": "other", "payload": {"action": "delete_shop_data"}},
        )
        assert deleted.status_code == 422
        refused = client.post(
            "/bot/v1/shops/demo-kota/drafts",
            headers=_headers(),
            json={"kind": "sale", "payload": {"item_name": "classic kota"}},
        )
        assert refused.status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_query_token_is_not_accepted():
    client, memory = _client()
    try:
        response = client.get("/bot/v1/shops", params={"access_token": TOKEN})
        assert response.status_code == 401
        assert memory.statements == []
    finally:
        client.__exit__(None, None, None)
