"""Read a kota sales sheet from WhatsApp. Nothing is written until the owner says YES."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from io import StringIO

from app.modules.parsing import canonical_ingredient, canonical_product
from app.modules.records import _ingredient, _product
from app.money import fmt_qty
from app.store import execute, fetchone

MAX_ROWS = 400

KOTA_COLUMNS = [
    "date",
    "day",
    "week",
    "classic_kotas_sold",
    "russian_cheese_sold",
    "full_house_sold",
    "total_kotas_sold",
    "bread_loaves_used",
    "bread_spoilage_loaves",
    "polony_used_kg",
    "russians_used_pcs",
    "cheese_slices_used",
    "potatoes_used_kg",
    "atchar_used_liters",
    "cooking_oil_used_liters",
    "cooking_oil_unit_price_zar",
]

SALE_COLUMNS = {
    "classic_kotas_sold": ("classic kota", "each"),
    "classic_kota_sold": ("classic kota", "each"),
    "russian_cheese_sold": ("russian cheese kota", "each"),
    "russian_and_cheese_sold": ("russian cheese kota", "each"),
    "full_house_sold": ("full house kota", "each"),
    "full_house_kota_sold": ("full house kota", "each"),
}

USAGE_COLUMNS = {
    "bread_loaves_used": ("bread loaf", "loaf"),
    "polony_used_kg": ("polony", "kg"),
    "russians_used_pcs": ("russian sausage", "piece"),
    "cheese_slices_used": ("cheese slice", "slice"),
    "potatoes_used_kg": ("potato", "kg"),
    "atchar_used_liters": ("atchar", "litre"),
    "atchar_used_litres": ("atchar", "litre"),
    "cooking_oil_used_liters": ("cooking oil", "litre"),
    "cooking_oil_used_litres": ("cooking oil", "litre"),
}

WASTE_COLUMNS = {
    "bread_spoilage_loaves": ("bread loaf", "loaf"),
}

PRICE_COLUMNS = {
    "cooking_oil_unit_price_zar": ("cooking oil", "litre"),
}

WEEKDAYS = {
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
}


def csv_help_text() -> str:
    return (
        "Yes. Paste the rows here, or attach the CSV file.\n"
        "I can read a kota sheet: date, classic, russian and cheese, full house, "
        "bread, spoilage, polony, and russians.\n"
        "I show the totals first. I save them on your shop only after you reply YES."
    )


def is_csv_question(text: str) -> bool:
    lowered = re.sub(r"\s+", " ", (text or "").strip().lower())
    if "csv" not in lowered and "spreadsheet" not in lowered:
        return False
    return not looks_like_csv(text)


def looks_like_csv(text: str) -> bool:
    rows = stitch_rows(text or "")
    if not rows:
        return False
    for row in rows:
        if re.match(r"\d{4}-\d{2}-\d{2}\s*,", row):
            return True
        first = row.split(",")[0].strip().lower()
        if first == "date":
            return True
    return False


def stitch_rows(text: str) -> list[str]:
    """Join WhatsApp line-wraps. A new row starts at a date or a Date header."""
    lines = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        piece = raw.strip()
        if piece:
            lines.append(piece)
    rows: list[str] = []
    buf = ""
    for line in lines:
        if _starts_row(line) and buf:
            rows.append(buf)
            buf = line
            continue
        if not buf:
            buf = line
            continue
        if buf.endswith(",") and line.startswith(","):
            buf = buf[:-1] + line
        elif buf.endswith(",") or line.startswith(",") or re.match(r"^[\d.]", line):
            buf = buf + line
        else:
            buf = buf + " " + line
    if buf:
        rows.append(buf)
    return rows


def _starts_row(line: str) -> bool:
    if re.match(r"\d{4}-\d{2}-\d{2}\s*,", line):
        return True
    first = line.split(",")[0].strip().lower()
    return first == "date"


def document_is_csv(filename: str | None, mime: str | None, content: bytes | None) -> bool:
    name = (filename or "").lower()
    kind = (mime or "").split(";")[0].strip().lower()
    if name.endswith(".csv") or kind in {"text/csv", "application/csv", "text/comma-separated-values"}:
        return True
    if not content or content[:2] == b"PK":
        return False
    sample = content[:8000].decode("utf-8-sig", errors="replace")
    return looks_like_csv(sample)


def decode_sheet(content: bytes) -> str:
    return content.decode("utf-8-sig", errors="replace")


@dataclass
class ParsedSheet:
    error: str | None = None
    sales: list[dict] = field(default_factory=list)
    usage: list[dict] = field(default_factory=list)
    waste: list[dict] = field(default_factory=list)
    prices: list[dict] = field(default_factory=list)
    row_count: int = 0
    start: date | None = None
    end: date | None = None
    warnings: list[str] = field(default_factory=list)


def parse_sheet_text(text: str) -> ParsedSheet | None:
    if not looks_like_csv(text):
        return None
    table = [_cells(row) for row in stitch_rows(text)]
    table = [cells for cells in table if any(cell.strip() for cell in cells)]
    if not table:
        return ParsedSheet(error=_unreadable())
    header, data = _split_header(table)
    if len(data) > MAX_ROWS:
        return ParsedSheet(
            error=f"That sheet has more than {MAX_ROWS} rows. Send a shorter one and I will read it."
        )
    parsed = ParsedSheet()
    mismatches = 0
    skipped = 0
    for cells in data:
        row = _interpret(cells, header)
        if row is None:
            skipped += 1
            continue
        parsed.row_count += 1
        parsed.sales.extend(row["sales"])
        parsed.usage.extend(row["usage"])
        parsed.waste.extend(row["waste"])
        parsed.prices.extend(row["prices"])
        if row["mismatch"]:
            mismatches += 1
        day = row["day"]
        parsed.start = day if parsed.start is None or day < parsed.start else parsed.start
        parsed.end = day if parsed.end is None or day > parsed.end else parsed.end
    if parsed.row_count == 0 or (not parsed.sales and not parsed.usage and not parsed.waste):
        return ParsedSheet(error=_unreadable())
    if mismatches:
        parsed.warnings.append(
            f"On {mismatches} days the total column did not match the three kotas. "
            "I kept the three product counts."
        )
    if skipped:
        parsed.warnings.append(f"I skipped {skipped} lines I could not read.")
    return parsed


class SheetDraft:
    """Pending confirmation. confirm() writes it only after YES."""

    def __init__(self, shop_id: str, parsed: ParsedSheet):
        self.kind = "other"
        self.read_back = sheet_read_back(parsed)
        self.shop_id = shop_id
        self.parsed = parsed

    def as_payload(self) -> dict:
        return {
            "action": "import_sheet",
            "confirm_allowed": True,
            "shop_id": self.shop_id,
            "rows": self.parsed.row_count,
            "start": self.parsed.start.isoformat() if self.parsed.start else None,
            "end": self.parsed.end.isoformat() if self.parsed.end else None,
            "sales": self.parsed.sales,
            "usage": self.parsed.usage,
            "waste": self.parsed.waste,
            "prices": self.parsed.prices,
            "records": [],
        }


def sheet_read_back(parsed: ParsedSheet) -> str:
    totals: dict[str, Decimal] = {}
    for sale in parsed.sales:
        totals[sale["product"]] = totals.get(sale["product"], Decimal("0")) + Decimal(sale["quantity"])
    lines = [
        f"I read {parsed.row_count} rows, {parsed.start.isoformat()} to {parsed.end.isoformat()}.",
        "Totals:",
    ]
    for name, qty in totals.items():
        lines.append(f"• {name}: {fmt_qty(qty)}")
    if parsed.waste:
        waste_totals: dict[str, Decimal] = {}
        for item in parsed.waste:
            waste_totals[item["ingredient"]] = waste_totals.get(item["ingredient"], Decimal("0")) + Decimal(
                item["quantity"]
            )
        lines.append("Spoilage:")
        for name, qty in waste_totals.items():
            lines.append(f"• {name}: {fmt_qty(qty)}")
    if parsed.usage:
        names = list(dict.fromkeys(item["ingredient"] for item in parsed.usage))
        lines.append("Usage for those days: " + ", ".join(names) + ".")
    for warning in parsed.warnings:
        lines.append(warning)
    lines.append("Reply YES to save this on your shop, or NO to cancel.")
    lines.append("Sending the same sheet again replaces these days. It will not double them.")
    return "\n".join(lines)


@dataclass
class ImportResult:
    saved: bool
    message: str


async def apply_sheet(conn, ctx, payload: dict) -> ImportResult:
    """Write one confirmed sheet onto ctx.shop_id. Re-sending the same days replaces them."""
    shop_id = ctx.shop_id
    if payload.get("shop_id") and payload["shop_id"] != shop_id:
        return ImportResult(False, "I can only save a sheet on your own shop.")
    row = await fetchone(conn, "SELECT is_demo FROM shops WHERE id = %s", (shop_id,))
    if row is None:
        return ImportResult(False, "I could not find your shop.")
    if row["is_demo"]:
        return ImportResult(
            False,
            "This is a demo shop, so I won't load a sheet onto it. Send the CSV from your own WhatsApp.",
        )
    for sale in payload.get("sales") or []:
        qty = _positive(sale.get("quantity"))
        if qty is None:
            continue
        product = await _product(conn, shop_id, sale["product"], sale.get("unit") or "each")
        await execute(
            conn,
            """
            DELETE FROM sales_entries
            WHERE shop_id = %s AND sale_date = %s AND product_id = %s
            """,
            (shop_id, sale["date"], product["id"]),
        )
        await execute(
            conn,
            """
            INSERT INTO sales_entries
                (shop_id, sale_date, product_id, quantity, unit_price, source_message_id)
            VALUES (%s, %s, %s, %s, NULL, %s)
            """,
            (shop_id, sale["date"], product["id"], qty, ctx.message_id),
        )
    for item in payload.get("usage") or []:
        qty = _positive(item.get("quantity"))
        if qty is None:
            continue
        ingredient = await _ingredient(conn, shop_id, item["ingredient"], item.get("unit") or "each")
        cost = item.get("unit_cost")
        await execute(
            conn,
            """
            INSERT INTO usage_entries
                (shop_id, usage_date, ingredient_id, quantity, unit, unit_cost)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (shop_id, usage_date, ingredient_id)
            DO UPDATE SET quantity = EXCLUDED.quantity, unit = EXCLUDED.unit, unit_cost = EXCLUDED.unit_cost
            """,
            (shop_id, item["date"], ingredient["id"], qty, item.get("unit") or ingredient["unit"], cost),
        )
    for item in payload.get("waste") or []:
        qty = _positive(item.get("quantity"))
        if qty is None:
            continue
        ingredient = await _ingredient(conn, shop_id, item["ingredient"], item.get("unit") or "loaf")
        await execute(
            conn,
            """
            DELETE FROM waste
            WHERE shop_id = %s AND waste_date = %s AND ingredient_id = %s AND reason = 'spoiled'
            """,
            (shop_id, item["date"], ingredient["id"]),
        )
        await execute(
            conn,
            """
            INSERT INTO waste
                (shop_id, waste_date, ingredient_id, quantity, unit, reason, estimated_cost, source_message_id)
            VALUES (%s, %s, %s, %s, %s, 'spoiled', NULL, %s)
            """,
            (
                shop_id,
                item["date"],
                ingredient["id"],
                qty,
                item.get("unit") or "loaf",
                ctx.message_id,
            ),
        )
    for item in payload.get("prices") or []:
        price = _positive(item.get("unit_price"))
        if price is None:
            continue
        await execute(
            conn,
            """
            INSERT INTO price_observations (shop_id, observed_on, item_name, unit, unit_price)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (shop_id, observed_on, item_name)
            DO UPDATE SET unit = EXCLUDED.unit, unit_price = EXCLUDED.unit_price
            """,
            (shop_id, item["date"], item["item"], item.get("unit") or "each", price),
        )
    return ImportResult(
        True,
        (
            f"Saved {payload.get('rows')} rows on your shop ({payload.get('start')} to {payload.get('end')}). "
            "If you send the same sheet again, I replace those days instead of doubling them."
        ),
    )


def _unreadable() -> str:
    return (
        "I see a sheet, but I could not read the columns. "
        "Use a header that starts with Date, or the kota layout: "
        "Date, Day, Week, Classic_Kotas_Sold, Russian_Cheese_Sold, Full_House_Sold."
    )


def _cells(line: str) -> list[str]:
    return next(csv.reader(StringIO(line)))


def _split_header(table: list[list[str]]) -> tuple[list[str] | None, list[list[str]]]:
    keys = [_header_key(cell) for cell in table[0]]
    if keys and keys[0] == "date":
        return keys, table[1:]
    if table and _positional_kota(table[0]):
        return None, table
    return None, table


def _positional_kota(cells: list[str]) -> bool:
    if len(cells) < 4:
        return False
    if _parse_day(cells[0]) is None:
        return False
    if cells[1].strip().lower() not in WEEKDAYS:
        return False
    return bool(re.fullmatch(r"\d+", cells[2].strip()))


def _header_key(cell: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", cell.strip().lower()).strip("_")


def _interpret(cells: list[str], header: list[str] | None) -> dict | None:
    if header:
        paired = list(zip(header, cells))
    elif _positional_kota(cells):
        paired = list(zip(KOTA_COLUMNS, cells))
    else:
        return None
    data = {key: value.strip() for key, value in paired if key}
    day = _parse_day(data.get("date", ""))
    if day is None:
        return None
    sales = []
    usage = []
    waste = []
    prices = []
    seen_sale = set()
    seen_usage = set()
    seen_waste = set()
    for key, raw in data.items():
        if key in {"date", "day", "week"} or key.startswith("total"):
            continue
        if key in SALE_COLUMNS:
            _add_qty(sales, seen_sale, day, SALE_COLUMNS[key], raw, "product")
            continue
        if key in USAGE_COLUMNS:
            _add_qty(usage, seen_usage, day, USAGE_COLUMNS[key], raw, "ingredient")
            continue
        if key in WASTE_COLUMNS:
            _add_qty(waste, seen_waste, day, WASTE_COLUMNS[key], raw, "ingredient")
            continue
        if key in PRICE_COLUMNS:
            price = _positive(raw)
            if price is not None:
                item, unit = PRICE_COLUMNS[key]
                prices.append(
                    {"date": day.isoformat(), "item": item, "unit": unit, "unit_price": format(price, "f")}
                )
            continue
        generic_sale = _generic_sale(key)
        if generic_sale:
            _add_qty(sales, seen_sale, day, generic_sale, raw, "product")
            continue
        generic_usage = _generic_usage(key)
        if generic_usage:
            bucket = waste if generic_usage[2] == "waste" else usage
            seen = seen_waste if generic_usage[2] == "waste" else seen_usage
            _add_qty(bucket, seen, day, (generic_usage[0], generic_usage[1]), raw, "ingredient")
    oil_cost = _positive(data.get("cooking_oil_unit_price_zar", ""))
    if oil_cost is not None:
        for item in usage:
            if item["ingredient"] == "cooking oil" and item["date"] == day.isoformat():
                item["unit_cost"] = format(oil_cost, "f")
    mismatch = _total_mismatch(data)
    return {"day": day, "sales": sales, "usage": usage, "waste": waste, "prices": prices, "mismatch": mismatch}


def _add_qty(bucket, seen, day, spec, raw, label_key):
    qty = _positive(raw)
    if qty is None:
        return
    name, unit = spec
    key = (day.isoformat(), name)
    if key in seen:
        return
    seen.add(key)
    row = {"date": day.isoformat(), label_key: name, "quantity": format(qty, "f"), "unit": unit}
    if label_key == "ingredient":
        row["unit_cost"] = None
    bucket.append(row)


def _generic_sale(key: str):
    if not key.endswith("_sold") or key.startswith("total"):
        return None
    label = key[: -len("_sold")].replace("_", " ").strip()
    if not label:
        return None
    return (canonical_product(label) or label, "each")


def _generic_usage(key: str):
    if "spoil" in key:
        kind = "waste"
    elif "used" in key:
        kind = "usage"
    else:
        return None
    unit = "each"
    if key.endswith("_kg"):
        unit = "kg"
    elif key.endswith("_pcs") or key.endswith("_pieces"):
        unit = "piece"
    elif "liter" in key or "litre" in key:
        unit = "litre"
    elif "loaf" in key or "loaves" in key:
        unit = "loaf"
    name = key
    for suffix in (
        "_used_kg",
        "_used_pcs",
        "_used_pieces",
        "_used_liters",
        "_used_litres",
        "_used_loaves",
        "_used",
        "_spoilage_loaves",
        "_spoilage",
    ):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    label = name.replace("_", " ").strip()
    mapped = canonical_ingredient(label)
    if mapped:
        return mapped[0], mapped[1], kind
    if not label:
        return None
    return label, unit, kind


def _total_mismatch(data: dict) -> bool:
    needed = ("classic_kotas_sold", "russian_cheese_sold", "full_house_sold", "total_kotas_sold")
    if not all(data.get(key, "").strip() for key in needed):
        return False
    numbers = [_number(data[key]) for key in needed]
    if any(number is None for number in numbers):
        return False
    classic, russian, full_house, stated = numbers
    return classic + russian + full_house != stated


def _parse_day(cell: str) -> date | None:
    raw = (cell or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _number(cell: str) -> Decimal | None:
    raw = (cell or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw):
        raw = raw.replace(",", "")
    elif raw.count(",") == 1 and "." not in raw:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return Decimal(raw)
    except Exception:
        return None


def _positive(cell) -> Decimal | None:
    if isinstance(cell, Decimal):
        number = cell
    else:
        number = _number("" if cell is None else str(cell))
    if number is None or number <= 0:
        return None
    return number
