from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.config import Settings
from app.demo.briefing import price_changes, render_losses, render_peaks, render_price_changes, render_waste
from app.demo.seed import load_plans
from app.main import create_app
from app.modules.parsing import classify_question, extract_heuristic

TODAY = date(2026, 11, 13)


def test_plans_keep_sheet_numbers_and_do_not_invent_kota_prices():
    plans = {plan.shop_id: plan for plan in load_plans()}
    kota = plans["demo-kota"]
    assert len(kota.sales) == 28 * 3
    assert kota.revenue == []
    assert all(len(sale) == 3 for sale in kota.sales)
    monday = date(2026, 2, 2)
    assert (monday, "bread loaf", Decimal("4.5")) in kota.waste
    oil = [price for price in kota.prices if price[0] == date(2026, 2, 22)]
    assert oil == [(date(2026, 2, 22), "cooking oil", "litre", Decimal("39.2"))]
    before = [price for price in kota.prices if price[0] == date(2026, 2, 21)][0][3]
    assert before == Decimal("35.0")

    courier = plans["demo-courier"]
    assert courier.revenue[0] == (date(2026, 11, 1), Decimal("680"))
    petrol_before = [row for row in courier.prices if row[0] == date(2026, 11, 15)][0]
    petrol_after = [row for row in courier.prices if row[0] == date(2026, 11, 16)][0]
    assert petrol_before[3] == Decimal("22.1")
    assert petrol_after[3] == Decimal("23.8")
    assert plans["demo-beauty"].revenue
    assert plans["demo-catering"].revenue


def test_cited_price_rises_and_monday_spoilage():
    kota = load_plans()[0]
    courier = [plan for plan in load_plans() if plan.shop_id == "demo-courier"][0]
    oil_rows = [
        {"observed_on": day, "item_name": item, "unit": unit, "unit_price": price}
        for day, item, unit, price in kota.prices
    ]
    petrol_rows = [
        {"observed_on": day, "item_name": item, "unit": unit, "unit_price": price}
        for day, item, unit, price in courier.prices
    ]
    oil_text = " ".join(render_price_changes(price_changes(oil_rows)))
    petrol_text = " ".join(render_price_changes(price_changes(petrol_rows)))
    assert "R35" in oil_text and "R39.20" in oil_text and "22 Feb" in oil_text
    assert "R22.10" in petrol_text and "R23.80" in petrol_text and "16 Nov" in petrol_text

    waste_rows = [
        {"waste_date": day, "quantity": qty, "name": name, "unit": "loaf"}
        for day, name, qty in kota.waste
    ]
    waste_text = " ".join(render_waste(waste_rows))
    assert "Monday" in waste_text
    assert "4.5" in waste_text
    losses = render_losses(waste_rows, price_changes(oil_rows), [], True, [])
    assert "R39.20" in losses
    assert "profit" in losses.lower()
    assert "R" in losses.split("Recorded takings")[0] or "cannot give a rand" in losses or "No daily takings" in losses


def test_friday_and_saturday_are_the_kota_peaks():
    kota = load_plans()[0]
    daily: dict[date, Decimal] = {}
    for day, _name, qty in kota.sales:
        daily[day] = daily.get(day, Decimal("0")) + qty
    text = render_peaks(list(daily.items()))
    assert text.startswith("Friday and Saturday") or text.startswith("Saturday and Friday")


def test_demo_questions_use_the_deterministic_router():
    assert classify_question("How did we do this week?") == "insights"
    assert classify_question("What must I prep tomorrow?") == "forecast"
    assert classify_question("Where am I losing money?") == "losses"
    assert classify_question("Show me the monthly report") == "insights"
    outcome = extract_heuristic(
        "sold 40 classic kota",
        TODAY,
        ["classic kota", "russian cheese kota", "full house kota"],
        [],
    )
    assert outcome.confirm_allowed
    assert outcome.records[0].item_name == "classic kota"
    assert outcome.records[0].quantity == Decimal("40")
    assert outcome.records[0].unit_price is None


def test_demo_page_is_public():
    app = create_app(
        Settings(
            meta_app_secret="test-secret",
            webhook_verify_token="verify-me",
            database_url="",
            scheduler_enabled=False,
            llm_api_key="",
            seed_demo=False,
        )
    )
    with TestClient(app) as client:
        page = client.get("/demo")
        assert page.status_code == 200
        assert "SmartStock" in page.text
        assert "Demo chat" in page.text
        shops = client.get("/demo/shops")
        assert shops.json()["shops"] == []
