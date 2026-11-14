"""Load the four hackathon sheets. Running it again replaces those shops only."""

import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "demo"


@dataclass
class ShopPlan:
    shop_id: str
    name: str
    business_type: str
    as_of: date
    products: list[tuple[str, str]] = field(default_factory=list)
    ingredients: list[tuple[str, str]] = field(default_factory=list)
    sales: list[tuple[date, str, Decimal]] = field(default_factory=list)
    usage: list[tuple[date, str, Decimal, Decimal | None]] = field(default_factory=list)
    waste: list[tuple[date, str, Decimal]] = field(default_factory=list)
    expenses: list[tuple[date, Decimal, str]] = field(default_factory=list)
    prices: list[tuple[date, str, str, Decimal]] = field(default_factory=list)
    revenue: list[tuple[date, Decimal]] = field(default_factory=list)
    metrics: list[tuple[date, str, Decimal, str]] = field(default_factory=list)


def _dec(value: str) -> Decimal:
    return Decimal(str(value).strip())


def _day(value: str) -> date:
    return date.fromisoformat(value.strip())


def _qty(value: str) -> Decimal | None:
    number = _dec(value)
    if number <= 0:
        return None
    return number


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _add_sale(plan: ShopPlan, day: date, product: str, raw: str) -> None:
    quantity = _qty(raw)
    if quantity is not None:
        plan.sales.append((day, product, quantity))


def _add_usage(plan: ShopPlan, day: date, ingredient: str, raw: str, unit_cost: Decimal | None = None) -> None:
    quantity = _qty(raw)
    if quantity is not None:
        plan.usage.append((day, ingredient, quantity, unit_cost))


def build_kota(rows: list[dict[str, str]]) -> ShopPlan:
    plan = ShopPlan("demo-kota", "Demo Kota Shop", "kota", _day(rows[-1]["Date"]))
    plan.products = [
        ("classic kota", "each"),
        ("russian cheese kota", "each"),
        ("full house kota", "each"),
    ]
    plan.ingredients = [
        ("bread loaf", "loaf"),
        ("polony", "kg"),
        ("russian sausage", "piece"),
        ("cheese slice", "slice"),
        ("potato", "kg"),
        ("atchar", "litre"),
        ("cooking oil", "litre"),
    ]
    for row in rows:
        day = _day(row["Date"])
        classic = _dec(row["Classic_Kotas_Sold"])
        russian = _dec(row["Russian_Cheese_Sold"])
        full_house = _dec(row["Full_House_Sold"])
        stated = _dec(row["Total_Kotas_Sold"])
        if classic + russian + full_house != stated:
            raise ValueError(f"kota total mismatch on {day}")
        _add_sale(plan, day, "classic kota", row["Classic_Kotas_Sold"])
        _add_sale(plan, day, "russian cheese kota", row["Russian_Cheese_Sold"])
        _add_sale(plan, day, "full house kota", row["Full_House_Sold"])
        _add_usage(plan, day, "bread loaf", row["Bread_Loaves_Used"])
        _add_usage(plan, day, "polony", row["Polony_Used_Kg"])
        _add_usage(plan, day, "russian sausage", row["Russians_Used_Pcs"])
        _add_usage(plan, day, "cheese slice", row["Cheese_Slices_Used"])
        _add_usage(plan, day, "potato", row["Potatoes_Used_Kg"])
        _add_usage(plan, day, "atchar", row["Atchar_Used_Liters"])
        oil_price = _dec(row["Cooking_Oil_Unit_Price_ZAR"])
        _add_usage(plan, day, "cooking oil", row["Cooking_Oil_Used_Liters"], oil_price)
        spoilage = _qty(row["Bread_Spoilage_Loaves"])
        if spoilage is not None:
            plan.waste.append((day, "bread loaf", spoilage))
        plan.prices.append((day, "cooking oil", "litre", oil_price))
    return plan


def build_beauty(rows: list[dict[str, str]]) -> ShopPlan:
    plan = ShopPlan("demo-beauty", "Demo Mobile Beauty", "beauty", _day(rows[-1]["Date"]))
    plan.products = [("braiding", "appointment"), ("gel nails", "appointment"), ("haircut", "appointment")]
    plan.ingredients = [("braid hair pack", "pack"), ("nail gel", "unit")]
    for row in rows:
        day = _day(row["Date"])
        _add_sale(plan, day, "braiding", row["Braiding_Appointments"])
        _add_sale(plan, day, "gel nails", row["Gel_Nail_Appointments"])
        _add_sale(plan, day, "haircut", row["Haircut_Appointments"])
        _add_usage(plan, day, "braid hair pack", row["Braid_Hair_Packs_Used"])
        _add_usage(plan, day, "nail gel", row["Nail_Gel_Used_Units"])
        fuel = _qty(row["Fuel_Cost_ZAR"])
        if fuel is not None:
            plan.expenses.append((day, fuel, "mobile fuel"))
        plan.revenue.append((day, _dec(row["Total_Revenue_ZAR"])))
        plan.metrics.append((day, "mobile_distance", _dec(row["Mobile_Distance_Km"]), "km"))
    return plan


def build_courier(rows: list[dict[str, str]]) -> ShopPlan:
    plan = ShopPlan("demo-courier", "Demo Courier Errands", "courier", _day(rows[-1]["Date"]))
    plan.products = [
        ("parcel drop", "trip"),
        ("grocery errand", "trip"),
        ("urgent doc delivery", "trip"),
    ]
    for row in rows:
        day = _day(row["Date"])
        parcels = _dec(row["Parcel_Drops"])
        grocery = _dec(row["Grocery_Errands"])
        docs = _dec(row["Urgent_Doc_Deliveries"])
        stated = _dec(row["Total_Trips"])
        if parcels + grocery + docs != stated:
            raise ValueError(f"courier trip mismatch on {day}")
        _add_sale(plan, day, "parcel drop", row["Parcel_Drops"])
        _add_sale(plan, day, "grocery errand", row["Grocery_Errands"])
        _add_sale(plan, day, "urgent doc delivery", row["Urgent_Doc_Deliveries"])
        fuel = _qty(row["Fuel_Expense_ZAR"])
        if fuel is not None:
            plan.expenses.append((day, fuel, "petrol"))
        petrol = _dec(row["Petrol_Price_Per_Liter_ZAR"])
        plan.prices.append((day, "petrol", "litre", petrol))
        plan.revenue.append((day, _dec(row["Total_Revenue_ZAR"])))
        plan.metrics.append((day, "distance", _dec(row["Total_Distance_Km"]), "km"))
    return plan


def build_catering(rows: list[dict[str, str]]) -> ShopPlan:
    plan = ShopPlan("demo-catering", "Demo Catering Bakery", "catering", _day(rows[-1]["Date"]))
    plan.products = [
        ("corporate office platter", "each"),
        ("celebration cake", "each"),
        ("vetkoek box", "box"),
    ]
    plan.ingredients = [("flour", "kg"), ("meat", "kg"), ("packaging box", "box")]
    for row in rows:
        day = _day(row["Date"])
        _add_sale(plan, day, "corporate office platter", row["Corporate_Office_Platters"])
        _add_sale(plan, day, "celebration cake", row["Celebration_Cakes"])
        _add_sale(plan, day, "vetkoek box", row["Vetkoek_Boxes_Sold"])
        _add_usage(plan, day, "flour", row["Flour_Used_Kg"])
        _add_usage(plan, day, "meat", row["Meat_Used_Kg"])
        _add_usage(plan, day, "packaging box", row["Packaging_Boxes_Used"])
        plan.revenue.append((day, _dec(row["Total_Revenue_ZAR"])))
    return plan


def load_plans(data_dir: Path | None = None) -> list[ShopPlan]:
    folder = data_dir or DATA_DIR
    return [
        build_kota(read_csv(folder / "kota_shop_feb2026.csv")),
        build_beauty(read_csv(folder / "mobile_beauty_nov2026.csv")),
        build_courier(read_csv(folder / "courier_errands_nov2026.csv")),
        build_catering(read_csv(folder / "catering_bakery_nov2026.csv")),
    ]


def _replace_shop(conn, plan: ShopPlan) -> None:
    conn.execute("DELETE FROM shops WHERE id = %s", (plan.shop_id,))
    conn.execute(
        """
        INSERT INTO shops (id, name, business_type, is_demo, demo_as_of)
        VALUES (%s, %s, %s, TRUE, %s)
        """,
        (plan.shop_id, plan.name, plan.business_type, plan.as_of),
    )
    phone = plan.shop_id
    conn.execute(
        """
        INSERT INTO users (shop_id, name, phone, role, consent_at, whatsapp_wa_id)
        VALUES (%s, 'Demo judge', %s, 'owner', now(), %s)
        """,
        (plan.shop_id, phone, phone),
    )
    for name, unit in plan.products:
        conn.execute(
            "INSERT INTO products (shop_id, name, unit) VALUES (%s, %s, %s)",
            (plan.shop_id, name, unit),
        )
    for name, unit in plan.ingredients:
        conn.execute(
            "INSERT INTO ingredients (shop_id, name, unit) VALUES (%s, %s, %s)",
            (plan.shop_id, name, unit),
        )
    products = {
        row[0]: row[1]
        for row in conn.execute("SELECT name, id FROM products WHERE shop_id = %s", (plan.shop_id,)).fetchall()
    }
    ingredients = {
        row[0]: row[1]
        for row in conn.execute("SELECT name, id FROM ingredients WHERE shop_id = %s", (plan.shop_id,)).fetchall()
    }
    for day, name, quantity in plan.sales:
        conn.execute(
            """
            INSERT INTO sales_entries (shop_id, sale_date, product_id, quantity, unit_price)
            VALUES (%s, %s, %s, %s, NULL)
            """,
            (plan.shop_id, day, products[name], quantity),
        )
    for day, name, quantity, unit_cost in plan.usage:
        unit = dict(plan.ingredients)[name]
        conn.execute(
            """
            INSERT INTO usage_entries (shop_id, usage_date, ingredient_id, quantity, unit, unit_cost)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (plan.shop_id, day, ingredients[name], quantity, unit, unit_cost),
        )
    for day, name, quantity in plan.waste:
        unit = dict(plan.ingredients)[name]
        conn.execute(
            """
            INSERT INTO waste (shop_id, waste_date, ingredient_id, quantity, unit, reason, estimated_cost)
            VALUES (%s, %s, %s, %s, %s, 'spoiled', NULL)
            """,
            (plan.shop_id, day, ingredients[name], quantity, unit),
        )
    for day, amount, note in plan.expenses:
        conn.execute(
            """
            INSERT INTO expenses (shop_id, expense_date, amount, category, note)
            VALUES (%s, %s, %s, 'fuel', %s)
            """,
            (plan.shop_id, day, amount, note),
        )
    for day, item, unit, price in plan.prices:
        conn.execute(
            """
            INSERT INTO price_observations (shop_id, observed_on, item_name, unit, unit_price)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (plan.shop_id, day, item, unit, price),
        )
    for day, amount in plan.revenue:
        conn.execute(
            "INSERT INTO daily_revenue (shop_id, revenue_date, amount) VALUES (%s, %s, %s)",
            (plan.shop_id, day, amount),
        )
    for day, metric, quantity, unit in plan.metrics:
        conn.execute(
            """
            INSERT INTO daily_metrics (shop_id, metric_date, metric, quantity, unit)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (plan.shop_id, day, metric, quantity, unit),
        )


def seed_demo(database_url: str, data_dir: Path | None = None) -> dict:
    plans = load_plans(data_dir)
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        with conn.transaction():
            for plan in plans:
                _replace_shop(conn, plan)
    return {
        "shops": [plan.shop_id for plan in plans],
        "sales": sum(len(plan.sales) for plan in plans),
        "revenue_rows": sum(len(plan.revenue) for plan in plans),
    }
