"""Write confirmed drafts. Called only after the owner replies YES."""

from datetime import date
from decimal import Decimal

from app.money import D, fmt_qty, q2, q3
from app.modules.inventory import apply_deduction, apply_receipt
from app.modules.invoice import check_vat, price_changed
from app.store import execute, fetchall, fetchone


def _dec(value) -> Decimal | None:
    number = D(value)
    return number


def _day(value, fallback: date) -> date:
    if not value:
        return fallback
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


async def _product(conn, shop_id, name, unit="each"):
    return await fetchone(
        conn,
        """
        INSERT INTO products (shop_id, name, unit)
        VALUES (%s, %s, %s)
        ON CONFLICT (shop_id, name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id, name, unit, usual_price
        """,
        (shop_id, name, unit or "each"),
    )


async def _ingredient(conn, shop_id, name, unit):
    return await fetchone(
        conn,
        """
        INSERT INTO ingredients (shop_id, name, unit)
        VALUES (%s, %s, %s)
        ON CONFLICT (shop_id, name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id, name, unit, stock_on_hand, latest_unit_cost, avg_unit_cost,
                  reorder_level, safety_buffer
        """,
        (shop_id, name, unit or "each"),
    )


async def _supplier(conn, shop_id, name):
    existing = await fetchone(
        conn,
        """
        SELECT id FROM suppliers
        WHERE shop_id = %s AND name = %s AND branch IS NULL
        LIMIT 1
        """,
        (shop_id, name),
    )
    if existing:
        return existing["id"]
    created = await fetchone(
        conn,
        "INSERT INTO suppliers (shop_id, name) VALUES (%s, %s) RETURNING id",
        (shop_id, name),
    )
    return created["id"]


async def write_record(conn, ctx, record: dict) -> str:
    kind = record.get("kind")
    shop_id = ctx.shop_id
    message_id = ctx.message_id
    if kind == "sale":
        return await _sale(conn, shop_id, message_id, record, ctx.today)
    if kind == "purchase":
        return await _purchase(conn, shop_id, message_id, record, ctx.today)
    if kind == "expense":
        return await _expense(conn, shop_id, message_id, record, ctx.today)
    if kind == "waste":
        return await _waste(conn, shop_id, message_id, record, ctx.today)
    if kind == "usage":
        return await _usage(conn, shop_id, record, ctx.today)
    if kind == "stock_count":
        return await _count(conn, shop_id, message_id, record, ctx.today)
    if kind == "invoice":
        return await _invoice(conn, shop_id, message_id, record, ctx.today)
    if kind == "set_price" or record.get("action") == "set_price":
        return await _price(conn, shop_id, record)
    if kind == "set_target" or record.get("action") == "set_target":
        return await _target(conn, shop_id, record, ctx.today)
    return "I skipped an item I don't know how to save."


async def _sale(conn, shop_id, message_id, record, today) -> str:
    product = await _product(conn, shop_id, record["item_name"], record.get("unit") or "each")
    qty = q3(_dec(record["quantity"]))
    unit_price = _dec(record.get("unit_price"))
    if unit_price is not None:
        unit_price = q2(unit_price)
    row = await fetchone(
        conn,
        """
        INSERT INTO sales_entries
            (shop_id, sale_date, product_id, quantity, unit_price, source_message_id, sale_hour)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            shop_id,
            _day(record.get("record_date"), today),
            product["id"],
            qty,
            unit_price,
            message_id,
            record.get("sale_hour"),
        ),
    )
    recipes = await fetchall(
        conn,
        """
        SELECT ingredient_id, qty_per_unit
        FROM recipes
        WHERE shop_id = %s AND product_id = %s
        """,
        (shop_id, product["id"]),
    )
    notes = [f"Sold {record['item_name']}."]
    if not recipes:
        notes.append("I didn't change stock — there is no recipe for this item yet.")
    else:
        for recipe in recipes:
            need = q3(recipe["qty_per_unit"] * qty)
            notes.append(
                await apply_deduction(
                    conn,
                    shop_id,
                    recipe["ingredient_id"],
                    need,
                    _day(record.get("record_date"), today),
                    "sales_entries",
                    row["id"],
                    "sale_deduction",
                )
            )
    if unit_price is not None and product["usual_price"] is None:
        notes.append("This price is on the sale only. Say usual price and the amount to set the normal price.")
    return " ".join(notes)


async def _purchase(conn, shop_id, message_id, record, today) -> str:
    ingredient = await _ingredient(conn, shop_id, record["item_name"], record.get("unit") or "each")
    qty = q3(_dec(record["quantity"]))
    unit_cost = _dec(record.get("unit_cost"))
    total = _dec(record.get("total"))
    if unit_cost is None and total is not None and qty:
        unit_cost = total / qty
    if total is None and unit_cost is not None:
        total = unit_cost * qty
    if unit_cost is not None:
        unit_cost = q2(unit_cost)
    if total is not None:
        total = q2(total)
    row = await fetchone(
        conn,
        """
        INSERT INTO purchases
            (shop_id, purchase_date, ingredient_id, quantity, unit, unit_cost, total_cost,
             source_message_id, qty_remaining)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            shop_id,
            _day(record.get("record_date"), today),
            ingredient["id"],
            qty,
            record.get("unit") or ingredient["unit"] or "each",
            unit_cost,
            total,
            message_id,
            qty,
        ),
    )
    note = await apply_receipt(
        conn,
        shop_id,
        ingredient["id"],
        qty,
        unit_cost,
        _day(record.get("record_date"), today),
        "purchases",
        row["id"],
    )
    return note


async def _usage(conn, shop_id, record, today) -> str:
    ingredient = await _ingredient(conn, shop_id, record["item_name"], record.get("unit") or "each")
    qty = q3(_dec(record["quantity"]))
    unit = record.get("unit") or ingredient["unit"] or "each"
    await execute(
        conn,
        """
        INSERT INTO usage_entries
            (shop_id, usage_date, ingredient_id, quantity, unit, unit_cost)
        VALUES (%s, %s, %s, %s, %s, NULL)
        ON CONFLICT (shop_id, usage_date, ingredient_id)
        DO UPDATE SET quantity = EXCLUDED.quantity, unit = EXCLUDED.unit
        """,
        (shop_id, _day(record.get("record_date"), today), ingredient["id"], qty, unit),
    )
    unit_bit = ""
    if unit and unit not in {"each", "piece"} and unit not in (record.get("item_name") or ""):
        unit_bit = unit + " "
    return (
        f"Recorded {fmt_qty(qty)} {unit_bit}{record['item_name']} used. "
        "I didn't change the stock count."
    )


async def _expense(conn, shop_id, message_id, record, today) -> str:
    amount = q2(_dec(record["amount"]))
    await execute(
        conn,
        """
        INSERT INTO expenses (shop_id, expense_date, amount, category, note, source_message_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            shop_id,
            _day(record.get("record_date"), today),
            amount,
            record["category"],
            record.get("spoken_category"),
            message_id,
        ),
    )
    return f"Saved the {record.get('spoken_category') or record['category']} expense."


async def _waste(conn, shop_id, message_id, record, today) -> str:
    qty = q3(_dec(record["quantity"]))
    ingredient_id = None
    product_id = None
    if record.get("item_kind") == "product":
        product = await _product(conn, shop_id, record["item_name"], "each")
        product_id = product["id"]
    else:
        ingredient = await _ingredient(conn, shop_id, record["item_name"], record.get("unit") or "each")
        ingredient_id = ingredient["id"]
    row = await fetchone(
        conn,
        """
        INSERT INTO waste
            (shop_id, waste_date, ingredient_id, product_id, quantity, unit, reason, source_message_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            shop_id,
            _day(record.get("record_date"), today),
            ingredient_id,
            product_id,
            qty,
            record.get("unit") or "each",
            record.get("reason"),
            message_id,
        ),
    )
    notes = ["Saved the waste."]
    if ingredient_id:
        notes.append(
            await apply_deduction(
                conn,
                shop_id,
                ingredient_id,
                qty,
                _day(record.get("record_date"), today),
                "waste",
                row["id"],
                "waste",
            )
        )
    elif product_id:
        recipes = await fetchall(
            conn,
            "SELECT ingredient_id, qty_per_unit FROM recipes WHERE shop_id = %s AND product_id = %s",
            (shop_id, product_id),
        )
        if not recipes:
            notes.append("I didn't change ingredient stock because this item has no recipe.")
        for recipe in recipes:
            notes.append(
                await apply_deduction(
                    conn,
                    shop_id,
                    recipe["ingredient_id"],
                    q3(recipe["qty_per_unit"] * qty),
                    _day(record.get("record_date"), today),
                    "waste",
                    row["id"],
                    "waste",
                )
            )
    return " ".join(notes)


async def _count(conn, shop_id, message_id, record, today) -> str:
    ingredient = await _ingredient(conn, shop_id, record["item_name"], record.get("unit") or "each")
    locked = await fetchone(
        conn,
        """
        SELECT stock_on_hand FROM ingredients
        WHERE id = %s AND shop_id = %s
        FOR UPDATE
        """,
        (ingredient["id"], shop_id),
    )
    counted = q3(_dec(record["quantity"]))
    expected = locked["stock_on_hand"]
    variance = counted - expected if expected is not None else None
    row = await fetchone(
        conn,
        """
        INSERT INTO stock_counts
            (shop_id, count_date, ingredient_id, counted_qty, expected_qty, variance, source_message_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            shop_id,
            _day(record.get("record_date"), today),
            ingredient["id"],
            counted,
            expected,
            variance,
            message_id,
        ),
    )
    change = counted - expected if expected is not None else counted
    await execute(
        conn,
        "UPDATE ingredients SET stock_on_hand = %s, updated_at = now() WHERE id = %s AND shop_id = %s",
        (counted, ingredient["id"], shop_id),
    )
    await execute(
        conn,
        """
        INSERT INTO stock_movements
            (shop_id, ingredient_id, movement_date, movement_type, qty_change, ref_table, ref_id, stock_after)
        VALUES (%s, %s, %s, 'count_adjustment', %s, 'stock_counts', %s, %s)
        """,
        (shop_id, ingredient["id"], _day(record.get("record_date"), today), q3(change), row["id"], counted),
    )
    if expected is None:
        return f"I set the starting count for {record['item_name']} to {counted}."
    return f"Stock count saved. {record['item_name']} is now {counted} (it was {expected})."


async def _invoice(conn, shop_id, message_id, record, today) -> str:
    supplier_id = None
    if record.get("supplier"):
        supplier_id = await _supplier(conn, shop_id, record["supplier"])
    subtotal = _dec(record.get("subtotal"))
    vat = _dec(record.get("vat"))
    total = _dec(record.get("total"))
    discount = _dec(record.get("discount"))
    flags = check_vat(
        q2(subtotal) if subtotal is not None else None,
        q2(vat) if vat is not None else None,
        q2(total) if total is not None else None,
        q2(discount) if discount is not None else None,
    )
    invoice = await fetchone(
        conn,
        """
        INSERT INTO invoices
            (shop_id, supplier, supplier_id, invoice_date, subtotal, vat, discount, total,
             confirmed, source_message_id, flags)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
        RETURNING id
        """,
        (
            shop_id,
            record.get("supplier"),
            supplier_id,
            _day(record.get("record_date"), today),
            q2(subtotal) if subtotal is not None else None,
            q2(vat) if vat is not None else None,
            q2(discount) if discount is not None else None,
            q2(total) if total is not None else None,
            message_id,
            flags,
        ),
    )
    notes = ["Saved the invoice."]
    if "vat_mismatch" in flags:
        notes.append("VAT does not match 15%. I stored the invoice figures as they were.")
    elif "vat_missing" in flags:
        notes.append("VAT was missing, so I did not calculate it.")
    for index, line in enumerate(record.get("lines") or [], start=1):
        name = line.get("item_name") or line.get("description")
        ingredient_id = None
        if name:
            ingredient = await _ingredient(conn, shop_id, name, line.get("unit") or "each")
            ingredient_id = ingredient["id"]
        qty = _dec(line.get("quantity"))
        unit_price = _dec(line.get("unit_price"))
        line_total = _dec(line.get("line_total"))
        inserted = await fetchone(
            conn,
            """
            INSERT INTO invoice_lines
                (invoice_id, shop_id, line_no, description, ingredient_id, quantity, unit,
                 unit_price, line_total, confidence, confirmed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            RETURNING id
            """,
            (
                invoice["id"],
                shop_id,
                index,
                name,
                ingredient_id,
                q3(qty) if qty is not None else None,
                line.get("unit"),
                q2(unit_price) if unit_price is not None else None,
                q2(line_total) if line_total is not None else None,
                line.get("confidence") or "medium",
            ),
        )
        if ingredient_id and unit_price is not None:
            current = await fetchone(
                conn,
                "SELECT latest_unit_cost FROM ingredients WHERE id = %s AND shop_id = %s",
                (ingredient_id, shop_id),
            )
            if price_changed(current["latest_unit_cost"] if current else None, unit_price):
                notes.append(
                    f"{name} price changed from {current['latest_unit_cost']} to {unit_price}."
                )
        if ingredient_id and qty is not None:
            notes.append(
                await apply_receipt(
                    conn,
                    shop_id,
                    ingredient_id,
                    q3(qty),
                    q2(unit_price) if unit_price is not None else None,
                    _day(record.get("record_date"), today),
                    "invoice_lines",
                    inserted["id"],
                )
            )
        elif name:
            notes.append(f"Saved {name} on the invoice. No quantity, so stock was not changed.")
    if not record.get("lines"):
        notes.append("No line quantities, so I did not change stock.")
    return " ".join(notes)


async def _price(conn, shop_id, record) -> str:
    product = await _product(conn, shop_id, record["item_name"], "each")
    price = q2(_dec(record["unit_price"]))
    await execute(
        conn,
        "UPDATE products SET usual_price = %s WHERE id = %s AND shop_id = %s",
        (price, product["id"], shop_id),
    )
    return f"Usual price for {record['item_name']} is now {price}."


async def _target(conn, shop_id, record, today) -> str:
    product_id = None
    if record.get("item_name"):
        product = await _product(conn, shop_id, record["item_name"], "each")
        product_id = product["id"]
    start = _day(record.get("period_start"), today)
    end = _day(record.get("period_end"), today)
    await execute(
        conn,
        """
        INSERT INTO sales_targets
            (shop_id, period_type, period_start, period_end, product_id,
             target_units, target_revenue, basis, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'owner', 'open')
        """,
        (
            shop_id,
            record.get("period_type") or "day",
            start,
            end,
            product_id,
            _dec(record.get("quantity")),
            _dec(record.get("amount")),
            ),
    )
    return "Saved the sales target."
