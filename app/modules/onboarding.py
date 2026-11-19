"""Shop name and menu from a WhatsApp message. The server saves them, not the model."""

import re

from app.modules.parsing import canonical_product, name_grounded
from app.store import execute, fetchone


def _clean_name(name: str) -> str | None:
    cleaned = name.strip().strip("\"'“”").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).rstrip(".").strip()
    if not (2 <= len(cleaned) <= 80):
        return None
    if re.search(r"\d{4}-\d{2}-\d{2}", cleaned):
        return None
    if cleaned.lower() in {"yes", "no", "shop", "business", "my shop", "my business"}:
        return None
    return cleaned


def business_name_from(text: str) -> str | None:
    """Pull a shop name from a clear naming sentence. None if this is not one."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    patterns = (
        r"^(?P<name>.+?)\s+is my (?:business|shop) name\.?$",
        r"^my (?:shop|business) is called\s+(?P<name>.+?)\.?$",
        r"^(?:my |the )?(?:business|shop) name\s*(?:is|:)\s*(?P<name>.+?)\.?$",
        r"^call (?:my|the|this) (?:shop|business)\s+(?P<name>.+?)\.?$",
        r"^(?:please\s+)?(?:rename|change) (?:my |the )?(?:shop|business)(?: name)? to\s+(?P<name>.+?)\.?$",
    )
    for pattern in patterns:
        match = re.match(pattern, cleaned, re.I)
        if match:
            return _clean_name(match.group("name"))
    return None


def name_to_save(text: str, suggested: str | None = None) -> str | None:
    parsed = business_name_from(text)
    if parsed:
        return parsed
    if not suggested:
        return None
    cleaned = _clean_name(str(suggested))
    if not cleaned or not name_grounded(cleaned, text or ""):
        return None
    return cleaned


def menu_from(text: str) -> list[str] | None:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    match = re.match(r"^(?:my|our) (?:products|menu|items) (?:are|is)\s+(.+?)\.?$", cleaned, re.I)
    if not match:
        match = re.match(r"^(?:my|our) (?:products|menu|items)\s*:\s*(.+?)\.?$", cleaned, re.I)
    if not match:
        match = re.match(r"^we sell\s+(.+?)\.?$", cleaned, re.I)
    if not match:
        return None
    return _split_menu(match.group(1))


def menu_to_save(text: str, suggested: list | None = None) -> list[str] | None:
    parsed = menu_from(text)
    if parsed:
        return parsed
    kept = []
    for item in suggested or []:
        name = str(item).strip()
        if name and name_grounded(name, text or ""):
            kept.append(name)
    return kept or None


def _split_menu(body: str) -> list[str] | None:
    if "," in body:
        parts = []
        for piece in body.split(","):
            piece = re.sub(r"^(?:and|&)\s+", "", piece.strip(), flags=re.I)
            piece = piece.strip(" .")
            if piece:
                parts.append(piece)
    else:
        parts = [piece.strip(" .") for piece in re.split(r"\s+(?:and|&)\s+", body, flags=re.I) if piece.strip(" .")]
    names = [part for part in parts if len(part) >= 2]
    return names or None


async def shop_is_demo(conn, shop_id: str) -> bool:
    row = await fetchone(conn, "SELECT is_demo FROM shops WHERE id = %s", (shop_id,))
    return bool(row and row["is_demo"])


async def save_business_name(conn, shop_id: str, name: str) -> str:
    if await shop_is_demo(conn, shop_id):
        return (
            "This is a demo shop, so I won't rename it. "
            "On your own WhatsApp, say: Bra Kota Nate is my business name."
        )
    await execute(
        conn,
        """
        UPDATE shops
        SET name = %s, updated_at = now()
        WHERE id = %s AND is_demo = FALSE
        """,
        (name, shop_id),
    )
    return f"Saved. Your shop name is {name}."


async def save_menu(conn, shop_id: str, names: list[str]) -> str:
    if await shop_is_demo(conn, shop_id):
        return (
            "This is a demo shop, so I won't change the menu. "
            "On your own WhatsApp, say: my products are classic kota, russian and cheese, full house."
        )
    saved: list[str] = []
    for spoken in names:
        product = canonical_product(spoken) or re.sub(r"\s+", " ", spoken.strip().lower())
        if not product:
            continue
        await fetchone(
            conn,
            """
            INSERT INTO products (shop_id, name, unit)
            VALUES (%s, %s, 'each')
            ON CONFLICT (shop_id, name) DO UPDATE SET is_active = TRUE
            RETURNING name
            """,
            (shop_id, product),
        )
        if product not in saved:
            saved.append(product)
    if not saved:
        return "Tell me the products like: my products are classic kota, russian and cheese, full house."
    return "Saved your menu: " + ", ".join(saved) + "."
