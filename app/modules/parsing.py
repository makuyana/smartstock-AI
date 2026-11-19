"""Intake parser.

Turns a messy shop message into a draft. Numbers that are not in the message
are dropped. Nothing here writes to the database. The owner must reply YES
before a draft becomes a saved record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.money import D, fmt_money, fmt_qty, q2, q3, q4

ALLOWED_KINDS = {
    "sale",
    "purchase",
    "expense",
    "waste",
    "usage",
    "invoice",
    "stock_count",
    "set_price",
    "set_target",
}
RECORD_KINDS = {"sale", "purchase", "expense", "waste", "invoice", "stock_count"}
PENDING_KIND = {
    "sale": "sale",
    "purchase": "purchase",
    "expense": "expense",
    "waste": "waste",
    "invoice": "invoice",
    "stock_count": "stock_count",
}

WORD_MAP = {
    "chkn": "chicken",
    "chiken": "chicken",
    "chickn": "chicken",
    "chk": "chicken",
    "rus": "russian",
    "russ": "russian",
    "rusian": "russian",
    "russians": "russian",
    "kotas": "kota",
    "2day": "today",
    "tdy": "today",
    "yday": "yesterday",
    "tmrw": "tomorrow",
    "tmr": "tomorrow",
    "2moro": "tomorrow",
    "verkoop": "sold",
    "gekoop": "bought",
    "weggegooi": "wasted",
    "huur": "rent",
}

REASON_MAP = {
    "burnt": "burnt",
    "burned": "burnt",
    "expired": "expired",
    "spoiled": "spoiled",
    "spoilt": "spoiled",
    "rotten": "spoiled",
    "off": "spoiled",
    "stale": "spoiled",
    "unsold": "unsold",
    "other": "other",
}

WORD_TO_CATEGORY = {
    "rent": "rent",
    "huur": "rent",
    "gas": "utilities_gas",
    "electricity": "utilities_electricity",
    "power": "utilities_electricity",
    "lights": "utilities_electricity",
    "fridge": "equipment_fridge",
    "appliance": "equipment_appliances",
    "appliances": "equipment_appliances",
    "wages": "general_ops",
    "wage": "general_ops",
    "salary": "general_ops",
    "salaries": "general_ops",
    "transport": "general_ops",
    "taxi": "general_ops",
    "airtime": "other",
    "data": "other",
    "fuel": "fuel",
    "petrol": "fuel",
}

CATEGORY_LABEL = {
    "rent": "rent",
    "utilities_gas": "gas",
    "utilities_electricity": "electricity",
    "equipment_fridge": "fridge",
    "equipment_appliances": "appliances",
    "general_ops": "general running costs",
    "fuel": "fuel",
    "other": "other",
}

CATEGORY_WORDS = {
    "rent": ["rent", "huur"],
    "utilities_gas": ["gas"],
    "utilities_electricity": ["electricity", "power", "lights"],
    "equipment_fridge": ["fridge"],
    "equipment_appliances": ["appliance", "appliances"],
    "general_ops": ["wages", "wage", "salary", "salaries", "transport", "taxi", "general"],
    "other": ["airtime", "data", "other", "paid", "expense"],
    "fuel": ["fuel", "petrol"],
}

YES_WORDS = {"yes", "y", "yeah", "yep", "ok", "okay", "confirm", "save", "correct", "sure", "ja", "yebo", "ewe"}
NO_WORDS = {"no", "nope", "cancel", "stop", "nee", "hayi", "wrong", "dont", "don't"}
YES_TAIL = YES_WORDS | {"it", "that", "please", "sharp", "bra", "boet", "thanks"}
NO_TAIL = NO_WORDS | {"it", "that", "please", "thanks"}

INTAKE_SYSTEM = (
    "You extract shop events from one message sent by a kota shop owner in South Africa. "
    "Call extract_shop_events. Copy only numbers that appear in the message. "
    "Do not guess prices, quantities, or stock. If a quantity or item is missing, "
    "set needs_clarification true and ask a short question. "
    "Kinds: sale, purchase, expense, waste, invoice, stock_count, set_price, set_target. "
    "Map slang: chkn is chicken, rus is russian. Dates: today, yesterday, tomorrow only when said."
)

EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_shop_events",
        "description": "Structured shop events found in the owner's message.",
        "parameters": {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string"},
                            "item_name": {"type": ["string", "null"]},
                            "quantity": {"type": ["number", "string", "null"]},
                            "unit": {"type": ["string", "null"]},
                            "unit_price": {"type": ["number", "string", "null"]},
                            "total": {"type": ["number", "string", "null"]},
                            "amount": {"type": ["number", "string", "null"]},
                            "category": {"type": ["string", "null"]},
                            "reason": {"type": ["string", "null"]},
                            "supplier": {"type": ["string", "null"]},
                            "subtotal": {"type": ["number", "string", "null"]},
                            "vat": {"type": ["number", "string", "null"]},
                            "confidence": {"type": "string"},
                        },
                    },
                },
                "needs_clarification": {"type": "boolean"},
                "clarification_question": {"type": ["string", "null"]},
            },
            "required": ["events"],
        },
    },
}


@dataclass
class DraftRecord:
    kind: str
    item_name: str | None = None
    spoken_name: str | None = None
    item_kind: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_assumed: bool = False
    unit_price: Decimal | None = None
    unit_cost: Decimal | None = None
    unit_cost_calculated: bool = False
    total: Decimal | None = None
    amount: Decimal | None = None
    category: str | None = None
    spoken_category: str | None = None
    reason: str | None = None
    supplier: str | None = None
    subtotal: Decimal | None = None
    vat: Decimal | None = None
    discount: Decimal | None = None
    record_date: date | None = None
    sale_hour: int | None = None
    create_item: bool = False
    blocked: bool = False
    block_reason: str | None = None
    notes: list[str] = field(default_factory=list)
    action: str | None = None
    period_type: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    lines: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def as_payload(self) -> dict:
        def num(value: Decimal | None) -> str | None:
            if value is None:
                return None
            return format(value, "f")

        return {
            "kind": self.kind,
            "action": self.action,
            "item_name": self.item_name,
            "spoken_name": self.spoken_name,
            "item_kind": self.item_kind,
            "quantity": num(self.quantity),
            "unit": self.unit,
            "unit_price": num(self.unit_price),
            "unit_cost": num(self.unit_cost),
            "unit_cost_calculated": self.unit_cost_calculated,
            "total": num(self.total),
            "amount": num(self.amount),
            "category": self.category,
            "spoken_category": self.spoken_category,
            "reason": self.reason,
            "supplier": self.supplier,
            "subtotal": num(self.subtotal),
            "vat": num(self.vat),
            "discount": num(self.discount),
            "record_date": self.record_date.isoformat() if self.record_date else None,
            "sale_hour": self.sale_hour,
            "create_item": self.create_item,
            "period_type": self.period_type,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "lines": self.lines,
            "flags": self.flags,
            "notes": self.notes,
        }


@dataclass
class Outcome:
    records: list[DraftRecord]
    confirm_allowed: bool
    ambiguous: bool
    read_back: str
    kind: str
    is_record: bool

    def as_payload(self) -> dict:
        return {
            "confirm_allowed": self.confirm_allowed,
            "records": [record.as_payload() for record in self.records],
        }


@dataclass
class NameMatch:
    name: str | None
    confidence: str
    options: list[str]


def normalize_words(text: str) -> str:
    cleaned = text.strip().lower().replace("'", "'").replace("`", "'")
    cleaned = re.sub(r"\br\s*(?=\d)", "r ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    mapped = []
    for token in cleaned.split(" "):
        key = re.sub(r"[^a-z0-9']", "", token)
        mapped.append(WORD_MAP.get(key, token))
    return re.sub(r"\s+", " ", " ".join(mapped)).strip()


def norm_name(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", value.lower())).strip()


def text_numbers(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for raw in re.findall(r"\d+(?:[.,]\d+)?", text):
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw):
            token = raw.replace(",", "")
        else:
            token = raw.replace(",", ".")
        try:
            found.add(Decimal(token))
        except Exception:
            continue
    return found


def number_grounded(value: Decimal | None, numbers: set[Decimal]) -> bool:
    if value is None:
        return False
    return any(value == found for found in numbers)


def name_grounded(name: str, text: str) -> bool:
    target = norm_name(name)
    source = norm_name(text)
    if not target:
        return False
    tokens = [token for token in target.split() if len(token) > 2]
    if not tokens:
        return target in source
    return all(token in source for token in tokens)


def mentioned_date(text: str, today: date) -> date:
    if re.search(r"\byesterday\b", text):
        return today - timedelta(days=1)
    if re.search(r"\btomorrow\b", text):
        return today + timedelta(days=1)
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return today
    local = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text)
    if local:
        try:
            return date(int(local.group(3)), int(local.group(2)), int(local.group(1)))
        except ValueError:
            return today
    return today


def mentioned_hour(text: str) -> int | None:
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if not match:
        return None
    hour = int(match.group(1))
    if hour > 12:
        return None
    marker = match.group(3)
    if marker == "am":
        return 0 if hour == 12 else hour
    if hour == 12:
        return 12
    return hour + 12


def money_amounts(text: str) -> list[Decimal]:
    amounts = []
    for raw in re.findall(r"\br\s*(\d+(?:[.,]\d{1,2})?)\b", text):
        try:
            amounts.append(Decimal(raw.replace(",", ".")))
        except Exception:
            continue
    return amounts


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z'\s]", "", text.lower()).split()


def is_yes(text: str) -> bool:
    words = [word for word in _words(text) if word not in {"please", "sharp", "bra", "boet", "thanks"}]
    if not words or len(words) > 3:
        return False
    return words[0] in YES_WORDS and all(word in YES_TAIL for word in words)


def is_no(text: str) -> bool:
    words = [word for word in _words(text) if word not in {"please", "thanks"}]
    if not words or len(words) > 4:
        return False
    return words[0] in NO_WORDS and all(word in NO_TAIL or word in {"save", "this"} for word in words)


def is_delete_request(text: str) -> bool:
    return bool(re.search(r"\bdelete my (data|records|shop)\b", text.lower()))


def canonical_product(name: str) -> str | None:
    """Map a kota menu phrase onto the sheet's product name. None if it is not one."""
    normalized = norm_name(name)
    normalized = re.sub(r"\bkotas\b", "kota", normalized)
    if normalized in {"classic kota", "classic"}:
        return "classic kota"
    if normalized in {"russian and cheese", "russian cheese", "russian cheese kota"}:
        return "russian cheese kota"
    if normalized in {"full house", "full house kota"}:
        return "full house kota"
    return None


def canonical_ingredient(name: str) -> tuple[str, str] | None:
    """Map a kota sheet ingredient phrase to (name, unit). None if it is not one."""
    normalized = norm_name(name)
    tokens = set(normalized.split())
    if "polony" in tokens:
        return "polony", "kg"
    if tokens & {"bread", "loaf", "loaves"}:
        return "bread loaf", "loaf"
    if normalized in {"russian", "russians", "russian sausage"}:
        return "russian sausage", "piece"
    if "cheese" in tokens and "slice" in tokens:
        return "cheese slice", "slice"
    if tokens & {"potato", "potatoes"}:
        return "potato", "kg"
    if "atchar" in tokens:
        return "atchar", "litre"
    if "oil" in tokens:
        return "cooking oil", "litre"
    return None


def match_item(spoken: str, names: list[str]) -> NameMatch:
    target = norm_name(spoken)
    if not target or not names:
        return NameMatch(None, "none", [])
    exact = [name for name in names if norm_name(name) == target]
    if len(exact) == 1:
        return NameMatch(exact[0], "high", exact)
    if len(exact) > 1:
        return NameMatch(None, "ambiguous", exact)
    tokens = set(target.split())
    partial: list[str] = []
    for name in names:
        catalog_tokens = set(norm_name(name).split())
        if not catalog_tokens:
            continue
        if tokens <= catalog_tokens or catalog_tokens <= tokens or target in norm_name(name):
            partial.append(name)
    unique = list(dict.fromkeys(partial))
    if len(unique) == 1:
        return NameMatch(unique[0], "medium", unique)
    if len(unique) > 1:
        return NameMatch(None, "ambiguous", unique)
    return NameMatch(None, "none", [])


def classify_question(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(prep|forecast|how many should|what should i make|what to prep|what must i)\b", lowered):
        return "forecast"
    if re.search(r"\b(losing|loss|waste|spoil|spike|petrol|oil)\b", lowered) or "losing money" in lowered:
        return "losses"
    if re.search(r"\b(shopping|what do i (need|buy)|low stock|running out|stock)\b", lowered):
        return "stock"
    if re.search(r"\b(profit|p&l|pnl|summary|how much did|report|made this|this month|this week)\b", lowered):
        return "insights"
    if re.search(r"\b(invoice|vat|supplier bill)\b", lowered):
        return "invoice"
    if re.search(r"\b(promo|promotion|special|funding|loan|investor|capital)\b", lowered):
        return "growth"
    return "help"


def _clean_fragment(fragment: str) -> str:
    cleaned = re.sub(r"\br\s*\d+(?:[.,]\d{1,2})?\b", " ", fragment)
    cleaned = re.sub(
        r"\b(today|yesterday|tomorrow|please|sharp|each|apiece|at|for|total|the|a|an)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"\b\d{1,2}(?::\d{2})?\s*(am|pm)\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _pairs(fragment: str) -> list[tuple[Decimal, str]]:
    pattern = re.compile(
        r"(\d+(?:[.,]\d+)?)\s+([a-z][a-z'& ]*?)(?=\s+\d+(?:[.,]\d+)?\s+[a-z]|$)",
        re.I,
    )
    cleaned = _clean_fragment(fragment)
    cleaned = re.sub(r"\([^)]*\btotal\b[^)]*\)", " ", cleaned, flags=re.I)
    found = []
    for chunk in cleaned.split(","):
        chunk = re.sub(r"\s+", " ", chunk).strip(" .")
        if not chunk:
            continue
        for qty_raw, name in pattern.findall(chunk):
            name = re.sub(r"\s+", " ", name).strip(" .,-&")
            name = re.sub(r"\b(and|n)$", "", name).strip()
            name = re.sub(r"^(of|the|a|an)\s+", "", name)
            if not name:
                continue
            found.append((Decimal(qty_raw.replace(",", ".")), name))
    return found


def _peel_reason(name: str) -> tuple[str, str | None]:
    parts = name.split()
    if parts and parts[-1] in REASON_MAP:
        return " ".join(parts[:-1]).strip(), REASON_MAP[parts[-1]]
    return name, None


def _money_role(text: str, kind: str) -> str:
    has_each = bool(re.search(r"\b(each|apiece|per|at|@)\b", text))
    has_total = bool(re.search(r"\b(total|for)\b", text))
    if has_each and has_total:
        return "unclear"
    if has_each:
        return "unit"
    if has_total:
        return "total"
    if kind == "purchase":
        return "total"
    if kind == "expense":
        return "amount"
    if kind == "sale":
        return "unclear"
    return "unclear"


def _split_clauses(text: str) -> list[str]:
    parts = re.split(r"(?:;|\n|\bthen\b)", text)
    refined: list[str] = []
    for part in parts:
        refined.extend(
            re.split(
                r"\s+(?=(?:sold|bought|purchased|threw away|wasted|paid|counted|invoice)\b)",
                part,
            )
        )
    cleaned = []
    for part in refined:
        piece = re.sub(r"\band$", "", part.strip()).strip(" ,.")
        if piece:
            cleaned.append(piece)
    return cleaned or [text]


def _expense_event(text: str) -> dict | None:
    spoken = None
    category = None
    for word, mapped in WORD_TO_CATEGORY.items():
        if re.search(rf"\b{word}\b", text):
            spoken = word
            category = mapped
            break
    if category is None and re.search(r"\b(paid|expense)\b", text):
        spoken = "other"
        category = "other"
    if category is None:
        return None
    amounts = money_amounts(text)
    return {
        "kind": "expense",
        "item_name": spoken,
        "spoken_category": spoken,
        "category": category,
        "amount": format(amounts[0], "f") if amounts else None,
        "confidence": "high" if amounts else "low",
    }


def _invoice_event(text: str) -> dict | None:
    if not re.search(r"\binvoice\b", text):
        return None
    supplier_match = re.search(
        r"\binvoice(?:\s+from)?\s+([a-z][a-z ]{1,40}?)(?=\s+(?:total|vat|subtotal|r\s*\d|for)\b|$)",
        text,
    )
    supplier = supplier_match.group(1).strip() if supplier_match else None
    if supplier in {"from", "for", "total"}:
        supplier = None

    def after(label: str) -> str | None:
        match = re.search(rf"\b{label}\b\s*r\s*(\d+(?:[.,]\d{{1,2}})?)", text)
        if not match:
            return None
        return str(Decimal(match.group(1).replace(",", ".")))

    return {
        "kind": "invoice",
        "supplier": supplier,
        "subtotal": after("subtotal"),
        "vat": after("vat"),
        "total": after("total"),
        "confidence": "medium",
    }


def _usual_price_event(text: str) -> dict | None:
    match = re.search(
        r"\busual price(?: for)?\s+([a-z][a-z ]+?)\s+(?:is\s+)?r\s*(\d+(?:[.,]\d{1,2})?)\b",
        text,
    )
    if not match:
        return None
    return {
        "kind": "set_price",
        "item_name": match.group(1).strip(),
        "unit_price": str(Decimal(match.group(2).replace(",", "."))),
        "confidence": "high",
    }


def _target_event(text: str, today: date) -> dict | None:
    if not re.search(r"\btarget\b", text):
        return None
    amounts = money_amounts(text)
    pairs = _pairs(text)
    if not amounts and not pairs:
        return None
    if "month" in text:
        period = "month"
        start = today.replace(day=1)
        if start.month == 12:
            end = date(start.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    elif "week" in text:
        period = "week"
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    else:
        period = "day"
        start = end = today
    event = {
        "kind": "set_target",
        "period_type": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "confidence": "high",
    }
    if amounts:
        event["amount"] = format(amounts[0], "f")
    if pairs:
        event["quantity"] = format(pairs[0][0], "f")
        event["item_name"] = pairs[0][1]
    return event


def _events_from_clause(clause: str, today: date) -> tuple[list[dict], str | None]:
    price = _usual_price_event(clause)
    if price:
        return [price], None
    target = _target_event(clause, today)
    if target:
        return [target], None
    if re.search(r"\b(sold|sale|sales|selled)\b", clause):
        fragment = re.split(r"\b(?:sold|sale|sales|selled)\b", clause, maxsplit=1)[-1]
        pairs = _pairs(fragment)
        if not pairs:
            return [], "I saw a sale but not the items. Say it like: sold 12 chicken kota."
        role = _money_role(clause, "sale")
        amounts = money_amounts(clause)
        events = []
        for qty, name in pairs:
            event = {"kind": "sale", "item_name": name, "quantity": format(qty, "f"), "confidence": "high"}
            if len(pairs) == 1 and len(amounts) == 1:
                if role == "unit":
                    event["unit_price"] = format(amounts[0], "f")
                elif role == "total":
                    event["total"] = format(amounts[0], "f")
                else:
                    event["money_unclear"] = format(amounts[0], "f")
            events.append(event)
        if len(pairs) > 1 and amounts:
            return events, "I have the items. Which amount belongs to which item?"
        return events, None
    if re.search(r"\b(bought|buy|purchased|purchase)\b", clause):
        fragment = re.split(r"\b(?:bought|buy|purchased|purchase)\b", clause, maxsplit=1)[-1]
        pairs = _pairs(fragment)
        if not pairs:
            return [], "I saw a purchase but not the item. Say it like: bought 20 bread R180."
        role = _money_role(clause, "purchase")
        amounts = money_amounts(clause)
        events = []
        for qty, name in pairs:
            event = {
                "kind": "purchase",
                "item_name": name,
                "quantity": format(qty, "f"),
                "confidence": "high",
            }
            if len(pairs) == 1 and len(amounts) == 1:
                if role == "unit":
                    event["unit_price"] = format(amounts[0], "f")
                else:
                    event["total"] = format(amounts[0], "f")
            events.append(event)
        return events, None
    if re.search(r"\b(threw away|thrown away|wasted|waste|dumped)\b", clause):
        fragment = re.split(r"\b(?:threw away|thrown away|wasted|waste|dumped)\b", clause, maxsplit=1)[-1]
        pairs = _pairs(fragment)
        if not pairs:
            return [], "I saw waste but not the amount. Say it like: threw away 5 chips burnt."
        events = []
        for qty, name in pairs:
            item, reason = _peel_reason(name)
            if not item:
                continue
            events.append(
                {
                    "kind": "waste",
                    "item_name": item,
                    "quantity": format(qty, "f"),
                    "reason": reason,
                    "confidence": "high",
                }
            )
        return events, None
    if re.search(r"\b(counted|stock count)\b", clause):
        fragment = re.split(r"\b(?:counted|stock count)\b", clause, maxsplit=1)[-1]
        pairs = _pairs(fragment)
        if not pairs:
            return [], "Say a stock count like: counted 10 bread."
        return [
            {"kind": "stock_count", "item_name": name, "quantity": format(qty, "f"), "confidence": "high"}
            for qty, name in pairs
        ], None
    invoice = _invoice_event(clause)
    if invoice:
        return [invoice], None
    expense = _expense_event(clause)
    if expense:
        if expense.get("amount") is None:
            return [expense], "How much was that expense? Put the rand amount in the message."
        return [expense], None
    return [], None


def is_summary_line(text: str) -> bool:
    return bool(re.search(r"\bsold\s*:", text or "", re.I))


def _event_date(event: dict, text: str, normalized: str) -> date | None:
    raw = event.get("record_date")
    if not raw:
        return None
    raw_day = str(raw)[:10]
    if raw_day not in text and raw_day not in normalized:
        return None
    try:
        return date.fromisoformat(raw_day)
    except ValueError:
        return None


def _canonical_for(kind: str, spoken: str | None) -> tuple[str | None, str | None]:
    if not spoken:
        return None, None
    if kind == "sale":
        return canonical_product(spoken), None
    if kind in {"usage", "waste"}:
        mapped = canonical_ingredient(spoken)
        if mapped:
            return mapped
    return None, None


def _summary_events(text: str) -> list[dict] | None:
    """A read-back line: Date: ... | Sold: 44 Classic Kotas, ... | Used: ..."""
    if not is_summary_line(text):
        return None
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    record_date = date_match.group(1) if date_match else None
    sold_match = re.search(
        r"\bsold\s*:\s*(.+?)(?=(?:\|\s*)?\bused\s*:|$)",
        text,
        re.I | re.S,
    )
    if not sold_match:
        return None
    sold_blob = re.sub(r"\([^)]*\btotal\b[^)]*\)", " ", sold_match.group(1), flags=re.I)
    events = []
    for qty, name in _comma_items(sold_blob):
        event = {
            "kind": "sale",
            "item_name": name,
            "quantity": format(qty, "f"),
            "confidence": "high",
        }
        if record_date:
            event["record_date"] = record_date
        events.append(event)
    if not events:
        return None
    used_match = re.search(r"\bused\s*:\s*(.+)$", text, re.I | re.S)
    if used_match:
        events.extend(_used_events(used_match.group(1), record_date))
    return events


def _comma_items(blob: str) -> list[tuple[Decimal, str]]:
    found = []
    pattern = re.compile(
        r"(\d+(?:[.,]\d+)?)\s+([a-z][a-z'& ]*?)(?=\s+\d+(?:[.,]\d+)?\s+[a-z]|$)",
        re.I,
    )
    for part in blob.split(","):
        part = re.sub(r"\([^)]*\)", " ", part)
        part = re.sub(r"\s+", " ", part).strip(" .|")
        if not part:
            continue
        for qty_raw, name in pattern.findall(part):
            name = re.sub(r"\s+", " ", name).strip(" .,-&")
            name = re.sub(r"\b(and|n)$", "", name).strip()
            if name:
                found.append((Decimal(qty_raw.replace(",", ".")), name))
    return found


def _used_events(blob: str, record_date: str | None) -> list[dict]:
    events = []
    for part in blob.split(","):
        spoilage = None
        paren = re.search(r"\(([^)]*)\)", part)
        body = part
        if paren:
            spoil = re.search(r"(\d+(?:[.,]\d+)?)\s*spoil", paren.group(1), re.I)
            if spoil:
                spoilage = Decimal(spoil.group(1).replace(",", "."))
            body = part[: paren.start()] + " " + part[paren.end() :]
        body = re.sub(r"\s+", " ", body).strip(" .|")
        match = re.match(
            r"^(\d+(?:[.,]\d+)?)(?:\s*(kg|g|loaves|loaf|pcs|pieces|litres|liters))?\s*([a-z][a-z'& ]+)$",
            body,
            re.I,
        )
        if not match:
            continue
        qty = Decimal(match.group(1).replace(",", "."))
        unit_word = (match.group(2) or "").lower()
        name = re.sub(r"\s+", " ", match.group(3)).strip()
        if unit_word in {"loaves", "loaf"}:
            spoken = f"{unit_word} {name}".strip()
        else:
            spoken = name
        if not spoken:
            continue
        event = {
            "kind": "usage",
            "item_name": spoken,
            "quantity": format(qty, "f"),
            "confidence": "high",
        }
        if record_date:
            event["record_date"] = record_date
        events.append(event)
        if spoilage is not None and spoilage > 0:
            waste = {
                "kind": "waste",
                "item_name": spoken,
                "quantity": format(spoilage, "f"),
                "reason": "spoiled",
                "confidence": "high",
            }
            if record_date:
                waste["record_date"] = record_date
            events.append(waste)
    return events


def heuristic_payload(text: str, today: date) -> dict:
    normalized = normalize_words(text)
    summary = _summary_events(normalized)
    if summary:
        return {
            "events": summary[:20],
            "needs_clarification": False,
            "clarification_question": None,
            "_saw_verb": True,
        }
    events: list[dict] = []
    question = None
    saw_verb = False
    for clause in _split_clauses(normalized):
        if re.search(
            r"\b(sold|sale|bought|purchased|threw away|wasted|counted|invoice|paid|rent|target|usual price)\b",
            clause,
        ):
            saw_verb = True
        clause_events, clause_question = _events_from_clause(clause, today)
        events.extend(clause_events)
        if clause_question and question is None:
            question = clause_question
    return {
        "events": events[:20],
        "needs_clarification": bool(question) or (saw_verb and not events),
        "clarification_question": question,
        "_saw_verb": saw_verb,
    }


def _ground_money(raw_value, numbers: set[Decimal]) -> Decimal | None:
    number = D(raw_value)
    if number is None:
        return None
    if not number_grounded(number, numbers):
        return None
    if number < 0:
        return None
    return number


def _category_allowed(category: str | None, text: str) -> str | None:
    if not category:
        return None
    mapped = WORD_TO_CATEGORY.get(category, category)
    if mapped not in CATEGORY_LABEL:
        mapped = "other"
    words = CATEGORY_WORDS.get(mapped, [])
    lowered = text.lower()
    if any(re.search(rf"\b{word}\b", lowered) for word in words):
        return mapped
    if mapped == "other" and re.search(r"\b(paid|expense)\b", lowered):
        return "other"
    return None


def _match_for_kind(kind: str, spoken: str, products: list[str], ingredients: list[str]) -> tuple[NameMatch, str]:
    if kind in {"sale", "set_price", "set_target"}:
        return match_item(spoken, products), "product"
    if kind in {"purchase", "stock_count", "usage"}:
        return match_item(spoken, ingredients), "ingredient"
    if kind == "waste":
        ingredient_match = match_item(spoken, ingredients)
        if ingredient_match.confidence in {"high", "medium"}:
            return ingredient_match, "ingredient"
        if ingredient_match.confidence == "ambiguous":
            return ingredient_match, "ingredient"
        return match_item(spoken, products), "product"
    return NameMatch(None, "none", []), "ingredient"


def normalize_extraction(
    text: str,
    raw: dict,
    today: date,
    products: list[str] | None = None,
    ingredients: list[dict] | None = None,
) -> Outcome:
    products = products or []
    ingredients = ingredients or []
    ingredient_names = [item["name"] if isinstance(item, dict) else item for item in ingredients]
    units = {
        (item["name"] if isinstance(item, dict) else item): (item.get("unit") if isinstance(item, dict) else None)
        for item in ingredients
    }
    normalized_text = normalize_words(text)
    numbers = text_numbers(normalized_text) | text_numbers(text)
    record_date = mentioned_date(normalized_text, today)
    hour = mentioned_hour(normalized_text)
    assumed_today = not bool(
        re.search(
            r"\b(yesterday|tomorrow|\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})\b",
            normalized_text,
        )
    )
    events = raw.get("events") if isinstance(raw, dict) else None
    if not isinstance(events, list):
        events = []
    question = raw.get("clarification_question") if isinstance(raw, dict) else None
    if question is not None:
        question = str(question).strip() or None
    saw_verb = bool(raw.get("_saw_verb")) if isinstance(raw, dict) else False

    records: list[DraftRecord] = []
    for event in events[:20]:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "").lower().strip()
        if kind not in ALLOWED_KINDS:
            continue
        spoken = event.get("item_name") or event.get("spoken_name")
        spoken = str(spoken).strip().lower() if spoken else None
        if spoken and not name_grounded(spoken, normalized_text):
            continue
        quantity = _ground_money(event.get("quantity"), numbers)
        if quantity is not None and quantity <= 0:
            quantity = None
        unit_price = _ground_money(event.get("unit_price"), numbers)
        total = _ground_money(event.get("total"), numbers)
        amount = _ground_money(event.get("amount"), numbers)
        subtotal = _ground_money(event.get("subtotal"), numbers)
        vat = _ground_money(event.get("vat"), numbers)
        unclear = _ground_money(event.get("money_unclear"), numbers)
        supplier = event.get("supplier")
        supplier = str(supplier).strip() if supplier else None
        if supplier and not name_grounded(supplier, normalized_text):
            supplier = None
        reason = event.get("reason")
        reason = REASON_MAP.get(str(reason).lower(), None) if reason else None
        if reason is None:
            _, peeled = _peel_reason(spoken or "")
            reason = peeled
        category = _category_allowed(event.get("category") or event.get("spoken_category"), normalized_text)
        spoken_category = event.get("spoken_category") or event.get("item_name")
        if kind == "expense" and category is None and spoken:
            category = _category_allowed(spoken, normalized_text)
        explicit_day = _event_date(event, text, normalized_text)
        record = DraftRecord(
            kind=kind,
            spoken_name=spoken,
            item_name=spoken,
            quantity=q3(quantity) if quantity is not None else None,
            unit_price=q2(unit_price) if unit_price is not None else None,
            total=q2(total) if total is not None else None,
            amount=q2(amount) if amount is not None else None,
            subtotal=q2(subtotal) if subtotal is not None else None,
            vat=q2(vat) if vat is not None else None,
            supplier=supplier,
            reason=reason,
            category=category,
            spoken_category=str(spoken_category) if spoken_category else None,
            record_date=explicit_day or record_date,
            sale_hour=hour,
            action=kind if kind in {"set_price", "set_target"} else None,
        )
        if assumed_today and explicit_day is None:
            record.notes.append("assumed today")
        if event.get("period_type") in {"day", "week", "month"}:
            record.period_type = event["period_type"]
            try:
                record.period_start = date.fromisoformat(event["period_start"])
                record.period_end = date.fromisoformat(event["period_end"])
            except (KeyError, TypeError, ValueError):
                record.period_start = record_date
                record.period_end = record_date
                record.period_type = "day"
        if unclear is not None and record.unit_price is None and record.total is None:
            record.blocked = True
            record.block_reason = (
                f"Is {fmt_money(unclear)} the price each, or the total? I have not saved this."
            )
        if spoken and kind in {"sale", "purchase", "waste", "stock_count", "set_price", "set_target", "usage"}:
            matched, item_kind = _match_for_kind(kind, spoken, products, ingredient_names)
            record.item_kind = item_kind
            if matched.confidence == "ambiguous":
                record.blocked = True
                options = ", ".join(matched.options)
                record.block_reason = f"Which one did you mean: {options}?"
            elif matched.name:
                record.item_name = matched.name
                record.create_item = False
                if item_kind == "ingredient" and units.get(matched.name):
                    record.unit = units[matched.name]
            else:
                record.create_item = True
                record.item_name = spoken
            canon_name, canon_unit = _canonical_for(kind, spoken)
            if record.create_item and not record.blocked and canon_name:
                record.item_name = canon_name
            if record.unit is None and canon_unit:
                record.unit = canon_unit
                record.unit_assumed = False
        if kind == "sale":
            record.item_kind = record.item_kind or "product"
            record.unit = record.unit or "each"
            if record.quantity is None:
                record.blocked = True
                record.block_reason = record.block_reason or f"How many {record.item_name or 'items'}?"
            if record.unit_price is not None and record.quantity is not None and record.total is None:
                record.total = q2(record.unit_price * record.quantity)
                record.notes.append("line total calculated")
        elif kind == "purchase":
            record.item_kind = "ingredient"
            if record.unit is None:
                record.unit = "each"
                record.unit_assumed = True
            if record.quantity is None or record.item_name is None:
                record.blocked = True
                record.block_reason = record.block_reason or "I need the item and how many you bought."
            if record.total is None and record.unit_price is None:
                record.blocked = True
                record.block_reason = record.block_reason or "What did it cost? Put the rand amount in the message."
            if record.total is not None and record.quantity is not None and record.unit_price is None:
                record.unit_cost = q4(record.total / record.quantity)
                record.unit_cost_calculated = True
            elif record.unit_price is not None:
                record.unit_cost = q4(record.unit_price)
                if record.quantity is not None and record.total is None:
                    record.total = q2(record.unit_price * record.quantity)
                    record.notes.append("total calculated")
        elif kind == "waste":
            if record.unit is None:
                record.unit = "each"
                record.unit_assumed = True
            if record.quantity is None or not record.item_name:
                record.blocked = True
                record.block_reason = record.block_reason or "What was thrown away, and how much?"
        elif kind == "usage":
            record.item_kind = record.item_kind or "ingredient"
            if record.unit is None:
                record.unit = "each"
                record.unit_assumed = True
            if record.quantity is None or not record.item_name:
                record.blocked = True
                record.block_reason = record.block_reason or "What was used, and how much?"
        elif kind == "expense":
            if record.amount is None or record.category is None:
                record.blocked = True
                record.block_reason = record.block_reason or "I need the kind of expense and the rand amount."
        elif kind == "stock_count":
            if record.unit is None:
                record.unit = "each"
                record.unit_assumed = True
            if record.quantity is None or not record.item_name:
                record.blocked = True
                record.block_reason = record.block_reason or "What did you count, and how many?"
        elif kind == "invoice":
            from app.modules.invoice import check_vat

            record.flags = check_vat(record.subtotal, record.vat, record.total, record.discount)
            if record.total is None and record.subtotal is None and record.vat is None:
                record.blocked = True
                record.block_reason = "I need at least one amount from the invoice."
        elif kind == "set_price":
            record.action = "set_price"
            if record.unit_price is None or not record.item_name:
                record.blocked = True
                record.block_reason = "Say it like: usual price chicken kota R25."
        elif kind == "set_target":
            record.action = "set_target"
            record.amount = record.amount
            if record.amount is None and record.quantity is None:
                record.blocked = True
                record.block_reason = "Say the target units or the target rand amount."
        records.append(record)

    needs_question = bool(raw.get("needs_clarification")) if isinstance(raw, dict) else False
    if not records and (needs_question or saw_verb or question):
        ask = question or "Tell me the item and the number. I have not saved anything."
        return Outcome(
            records=[],
            confirm_allowed=False,
            ambiguous=True,
            read_back=ask,
            kind="other",
            is_record=True,
        )
    if not records:
        return Outcome([], False, False, "", "other", False)

    blocked = [record for record in records if record.blocked]
    confirm_allowed = not blocked and all(_complete(record) for record in records)
    kinds = {PENDING_KIND.get(record.kind, "other") for record in records}
    pending_kind = kinds.pop() if len(kinds) == 1 else "other"
    if any(record.kind in {"set_price", "set_target"} for record in records):
        pending_kind = "other"
    read_back = _read_back(records, confirm_allowed, question)
    return Outcome(
        records=records,
        confirm_allowed=confirm_allowed,
        ambiguous=not confirm_allowed,
        read_back=read_back,
        kind=pending_kind,
        is_record=True,
    )


def _complete(record: DraftRecord) -> bool:
    if record.blocked:
        return False
    if record.kind == "sale":
        return record.quantity is not None and bool(record.item_name)
    if record.kind == "purchase":
        return record.quantity is not None and bool(record.item_name) and (
            record.total is not None or record.unit_cost is not None
        )
    if record.kind == "waste":
        return record.quantity is not None and bool(record.item_name) and bool(record.unit)
    if record.kind == "usage":
        return record.quantity is not None and bool(record.item_name) and bool(record.unit)
    if record.kind == "expense":
        return record.amount is not None and bool(record.category)
    if record.kind == "stock_count":
        return record.quantity is not None and bool(record.item_name)
    if record.kind == "invoice":
        return record.total is not None or record.subtotal is not None or record.vat is not None
    if record.kind == "set_price":
        return record.unit_price is not None and bool(record.item_name)
    if record.kind == "set_target":
        return record.amount is not None or record.quantity is not None
    return False


def _date_phrase(record: DraftRecord, today: date | None = None) -> str:
    if record.record_date is None:
        return ""
    if today is not None and record.record_date == today:
        return " today"
    return f" on {record.record_date.isoformat()}"


def _when(record: DraftRecord) -> str:
    if record.record_date is None:
        return ""
    if "assumed today" in record.notes:
        return " today"
    return f" on {record.record_date.isoformat()}"


def _qty_unit(record: DraftRecord) -> str:
    unit = record.unit or ""
    if unit in {"", "each", "piece"}:
        return ""
    if unit in (record.item_name or ""):
        return ""
    return f" {unit}"


def _line(record: DraftRecord) -> str:
    when = _when(record)
    if record.kind == "sale":
        price = ""
        if record.unit_price is not None:
            price = f" at {fmt_money(record.unit_price)} each"
            if record.total is not None:
                price += f" ({fmt_money(record.total)})"
        return f"Sold {fmt_qty(record.quantity)} {record.item_name}{price}{when}"
    if record.kind == "purchase":
        cost = ""
        if record.total is not None:
            cost = f" for {fmt_money(record.total)}"
        if record.unit_cost is not None:
            label = "calculated" if record.unit_cost_calculated else "given"
            per = "each" if not record.unit or record.unit == "each" else f"per {record.unit}"
            cost += f" ({fmt_money(record.unit_cost)} {per}, {label})"
        unit_prefix = f"{record.unit} " if record.unit and record.unit != "each" else ""
        assumed = " I counted the unit as each." if record.unit_assumed else ""
        return f"Bought {fmt_qty(record.quantity)} {unit_prefix}{record.item_name}{cost}{when}.{assumed}"
    if record.kind == "waste":
        reason = f" ({record.reason})" if record.reason else ""
        return f"Threw away {fmt_qty(record.quantity)}{_qty_unit(record)} {record.item_name}{reason}{when}"
    if record.kind == "usage":
        return f"Used {fmt_qty(record.quantity)}{_qty_unit(record)} {record.item_name}{when}"
    if record.kind == "expense":
        spoken = record.spoken_category or CATEGORY_LABEL.get(record.category or "", "expense")
        filed = CATEGORY_LABEL.get(record.category or "", record.category or "")
        extra = ""
        if spoken and filed and spoken not in {filed, record.category}:
            extra = f" I file {spoken} under {filed}."
        return f"Expense {spoken} {fmt_money(record.amount)}{when}.{extra}"
    if record.kind == "stock_count":
        unit_bit = f" {record.unit}" if record.unit else ""
        return f"Stock count {fmt_qty(record.quantity)}{unit_bit} {record.item_name}"
    if record.kind == "invoice":
        bits = []
        if record.supplier:
            bits.append(f"from {record.supplier}")
        if record.subtotal is not None:
            bits.append(f"subtotal {fmt_money(record.subtotal)}")
        if record.vat is not None:
            bits.append(f"VAT {fmt_money(record.vat)}")
        if record.total is not None:
            bits.append(f"total {fmt_money(record.total)}")
        note = _vat_sentence(record.flags)
        return "Invoice " + ", ".join(bits) + (". " + note if note else "")
    if record.kind == "set_price":
        return f"Usual price for {record.item_name} is {fmt_money(record.unit_price)}"
    if record.kind == "set_target":
        bits = [f"{record.period_type} target"]
        if record.item_name:
            bits.append(record.item_name)
        if record.quantity is not None:
            bits.append(f"{fmt_qty(record.quantity)} units")
        if record.amount is not None:
            bits.append(fmt_money(record.amount))
        return " ".join(bits)
    return record.kind


def _vat_sentence(flags: list[str]) -> str:
    if "vat_mismatch" in flags:
        return "VAT does not match 15% of the subtotal. Check the invoice before you say YES."
    if "total_mismatch" in flags:
        return "The total does not match subtotal plus VAT. I have not changed the numbers."
    if "vat_missing" in flags:
        return "VAT is missing, so I did not calculate it."
    if "subtotal_missing" in flags:
        return "I can't check the 15% VAT because there is no subtotal."
    if flags:
        return "VAT check: " + ", ".join(flags) + "."
    if not flags:
        return "VAT check passed."
    return ""


def _read_back(records: list[DraftRecord], confirm_allowed: bool, question: str | None) -> str:
    if not confirm_allowed:
        reason = next((record.block_reason for record in records if record.block_reason), None)
        lines = [reason or question or "I need a clearer message before I can save this."]
        clear = [record for record in records if not record.blocked]
        if clear:
            lines.append("I also heard:")
            lines.extend(f"• {_line(record)}" for record in clear)
        lines.append("I have not saved this.")
        return "\n".join(lines)
    lines = ["Save this?"]
    if question:
        lines.append(question)
    lines.extend(f"• {_line(record)}" for record in records)
    lines.append("Reply YES to save, or tell me what to change.")
    return "\n".join(lines)


def extract_heuristic(
    text: str,
    today: date,
    products: list[str] | None = None,
    ingredients: list[dict] | None = None,
) -> Outcome:
    payload = heuristic_payload(text, today)
    return normalize_extraction(text, payload, today, products, ingredients)


async def parse_with_llm(text, llm, today, products=None, ingredients=None) -> Outcome:
    """Ask the LLM to call extract_shop_events, then ground the result."""
    response = await llm.chat(
        messages=[
            {"role": "system", "content": INTAKE_SYSTEM},
            {"role": "user", "content": text},
        ],
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "function", "function": {"name": "extract_shop_events"}},
    )
    if not response.tool_calls:
        return extract_heuristic(text, today, products, ingredients)
    arguments = response.tool_calls[0].arguments
    if not isinstance(arguments, dict):
        return extract_heuristic(text, today, products, ingredients)
    return normalize_extraction(text, arguments, today, products, ingredients)
