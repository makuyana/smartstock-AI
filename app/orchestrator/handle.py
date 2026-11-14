"""Route one shop message. The model may call tools. Numbers in replies come from our code."""

import json
import logging

from app.demo.briefing import answer as briefing_answer
from app.llm.guard import guard_reply, numbers_in
from app.modules import forecast, growth, insights, intake
from app.modules.inventory import answer_stock
from app.modules.parsing import (
    classify_question,
    extract_heuristic,
    is_delete_request,
    is_no,
    is_yes,
    normalize_extraction,
    text_numbers,
)

logger = logging.getLogger("smartstock.orchestrator")

HELP = (
    "Send me what happened at the shop. For example:\n"
    "• sold 12 chicken kota 8 russian\n"
    "• bought 20 bread R180\n"
    "• threw away 5 chips burnt\n"
    "You can also ask: stock, what to prep, profit this month, or a promo draft."
)

SYSTEM = (
    "You are SmartStock, the bookkeeper for one township kota shop in South Africa. "
    "Money is rand. The clock is Africa/Johannesburg. "
    "Call tools to read this shop's database or to propose records. "
    "Never invent quantities, prices, or stock. "
    "To save a sale, purchase, expense, waste, invoice, or stock count, call propose_records. "
    "The server asks the owner to reply YES. Do not say it is already saved. "
    "confirm_latest only works when the owner just said yes. The server blocks it otherwise. "
    "For prep, profit, stock, shopping, promos, or funding, call the matching tool and stop. "
    "The server sends that tool's reply. "
    "If the message is unclear, call propose_records with needs_clarification true and a short question."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "propose_records",
            "description": "Propose shop records from the owner's latest message. The server checks every number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "events": {"type": "array", "items": {"type": "object"}},
                    "needs_clarification": {"type": "boolean"},
                    "clarification_question": {"type": ["string", "null"]},
                },
                "required": ["events"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_loss_patterns",
            "description": "Waste, price rises, and fuel from saved rows. Does not invent a loss amount.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_catalog",
            "description": "List product and ingredient names for this shop.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock",
            "description": "Read stock on hand. Does not guess missing counts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_shopping_list",
            "description": "Shopping list from reorder levels and real stock counts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_forecast",
            "description": "Prep quantities from saved sales. The server computes the numbers.",
            "parameters": {
                "type": "object",
                "properties": {"when": {"type": "string", "enum": ["today", "tomorrow"]}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_summary",
            "description": "Today's saved sales, expenses, and waste.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weekly_summary",
            "description": "This week's saved figures.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_monthly_pnl",
            "description": "Monthly profit and loss from the monthly_pnl view. Incomplete parts stay missing.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_margins",
            "description": "Product margins from the product_margins view.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "promo_draft",
            "description": "Draft a promo. Does not invent a discount.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "funding_readiness",
            "description": "Checklist of records a funder usually asks for. No loan amount.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_latest",
            "description": "Save the pending draft. Only succeeds when the owner said yes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_latest",
            "description": "Cancel the pending draft. Only succeeds when the owner said no.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _names(ctx):
    products = [item["name"] for item in ctx.products]
    return products, ctx.ingredients


async def build_reply(ctx) -> str:
    text = (ctx.text or "").strip()
    if ctx.consent_at is None:
        return await _consent(ctx, text)
    if text and is_delete_request(text):
        return await _propose_delete(ctx)
    products, ingredients = _names(ctx)
    if text and is_yes(text):
        peeked = extract_heuristic(text, ctx.today, products, ingredients)
        if not peeked.is_record:
            return await _with_conn(ctx, lambda conn: intake.confirm(conn, ctx))
    if text and is_no(text):
        peeked = extract_heuristic(text, ctx.today, products, ingredients)
        if not peeked.is_record:
            return await _with_conn(ctx, lambda conn: intake.cancel(conn, ctx))
    if ctx.llm.configured:
        try:
            reply = await _llm_turn(ctx)
            if reply:
                return reply
        except Exception:
            logger.exception("llm failed, using shop rules")
    return await _rules(ctx, text)


async def _with_conn(ctx, func):
    async with ctx.pool.connection() as conn:
        async with conn.transaction():
            return await func(conn)


async def _consent(ctx, text: str) -> str:
    if text and is_yes(text):
        async with ctx.pool.connection() as conn:
            async with conn.transaction():
                await intake.grant_consent(conn, ctx.user_id)
                previous = await intake.previous_inbound(conn, ctx.shop_id, ctx.user_id, ctx.message_id)
                products, ingredients = await _reload(conn, ctx)
        ctx.consent_at = ctx.now
        thanks = (
            "Thank you. I will store your shop records for you. "
            "Say DELETE MY DATA if you want them removed."
        )
        if previous and not is_yes(previous) and not is_no(previous):
            outcome = extract_heuristic(previous, ctx.today, products, ingredients)
            if outcome.is_record:
                async with ctx.pool.connection() as conn:
                    async with conn.transaction():
                        draft = await intake.propose(conn, ctx, outcome)
                return thanks + "\n\n" + draft
        return thanks + "\nTell me what you sold, bought, or threw away."
    return (
        "Hi, I'm SmartStock. I can keep your kota shop sales, stock, and profit.\n"
        "Reply YES if you agree that I store those records for you. "
        "Your numbers stay with your shop. Say DELETE MY DATA if you want them removed."
    )


async def _reload(conn, ctx):
    from app.store import load_catalog

    products, ingredients = await load_catalog(conn, ctx.shop_id)
    ctx.products = products
    ctx.ingredients = ingredients
    return [item["name"] for item in products], ingredients


async def _propose_delete(ctx) -> str:
    from app.modules.parsing import Outcome

    outcome = Outcome(
        records=[],
        confirm_allowed=True,
        ambiguous=False,
        read_back=(
            "This deletes the shop's saved sales, stock, and messages. "
            "Reply YES to delete, or NO to keep them."
        ),
        kind="other",
        is_record=True,
    )
    payload = outcome.as_payload()
    payload["action"] = "delete_shop_data"
    payload["confirm_allowed"] = True

    class _DeleteOutcome:
        kind = "other"
        read_back = outcome.read_back

        def as_payload(self):
            return payload

    return await _with_conn(ctx, lambda conn: intake.propose(conn, ctx, _DeleteOutcome()))


async def _rules(ctx, text: str) -> str:
    products, ingredients = _names(ctx)
    outcome = extract_heuristic(text, ctx.today, products, ingredients)
    if outcome.is_record:
        return await _with_conn(ctx, lambda conn: intake.propose(conn, ctx, outcome))
    intent = classify_question(text)

    async def route(conn):
        if intent == "forecast":
            return await forecast.answer(conn, ctx.shop_id, text, ctx.today)
        if intent == "stock":
            return await answer_stock(conn, ctx.shop_id, text, ctx.today)
        if intent in {"insights", "losses"}:
            return await briefing_answer(conn, ctx.shop_id, text, ctx.today)
        if intent == "growth":
            return await growth.answer(conn, ctx.shop_id, text, ctx.products)
        if intent == "invoice":
            return (
                "Send the invoice like: invoice from Boxer subtotal R500 vat R75 total R575. "
                "I check VAT at 15% and I do not fill in a missing VAT amount."
            )
        return HELP

    return await _with_conn(ctx, route)


async def _llm_turn(ctx) -> str | None:
    products, ingredients = _names(ctx)
    catalog = (
        "Products: " + (", ".join(products) if products else "none yet") + "\n"
        "Ingredients: "
        + (", ".join(item["name"] for item in ingredients) if ingredients else "none yet")
    )
    messages = [
        {"role": "system", "content": SYSTEM + "\n" + catalog},
        {"role": "user", "content": ctx.text or ""},
    ]
    allowed = set(text_numbers(ctx.text or ""))
    for _ in range(4):
        response = await ctx.llm.chat(messages, tools=TOOLS)
        if not response.tool_calls:
            if response.content:
                return guard_reply(response.content, allowed)
            return None
        messages.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in response.tool_calls
                ],
            }
        )
        for call in response.tool_calls:
            result = await _dispatch(ctx, call.name, call.arguments)
            allowed |= numbers_in(result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, default=str),
                }
            )
            if isinstance(result, dict) and result.get("final") and result.get("reply"):
                return result["reply"]
    return None


async def _dispatch(ctx, name: str, arguments: dict) -> dict:
    products, ingredients = _names(ctx)
    if name == "propose_records":
        outcome = normalize_extraction(ctx.text or "", arguments or {}, ctx.today, products, ingredients)
        if not outcome.is_record:
            return {"final": False, "error": "No shop record was found in the owner's message."}
        reply = await _with_conn(ctx, lambda conn: intake.propose(conn, ctx, outcome))
        return {"final": True, "reply": reply}
    if name == "confirm_latest":
        if not is_yes(ctx.text or ""):
            return {"final": True, "reply": "I'll only save this when you reply YES."}
        reply = await _with_conn(ctx, lambda conn: intake.confirm(conn, ctx))
        return {"final": True, "reply": reply}
    if name == "cancel_latest":
        if not is_no(ctx.text or ""):
            return {"final": True, "reply": "I'll only cancel this when you reply NO."}
        reply = await _with_conn(ctx, lambda conn: intake.cancel(conn, ctx))
        return {"final": True, "reply": reply}
    if name == "get_catalog":
        return {
            "final": False,
            "products": products,
            "ingredients": [item["name"] for item in ingredients],
        }

    async def read(conn):
        if name == "get_stock":
            reply = await answer_stock(conn, ctx.shop_id, "stock", ctx.today)
        elif name == "get_shopping_list":
            reply = await answer_stock(conn, ctx.shop_id, "shopping list", ctx.today)
        elif name == "build_forecast":
            when = "tomorrow" if (arguments or {}).get("when") == "tomorrow" else ctx.text or ""
            reply = await forecast.answer(conn, ctx.shop_id, when, ctx.today)
        elif name == "get_daily_summary":
            reply = await insights.answer(conn, ctx.shop_id, "today", ctx.today)
        elif name == "get_weekly_summary":
            reply = await briefing_answer(conn, ctx.shop_id, "how did we do this week?", ctx.today)
        elif name == "get_monthly_pnl":
            reply = await briefing_answer(conn, ctx.shop_id, "show me the monthly report", ctx.today)
        elif name == "get_loss_patterns":
            reply = await briefing_answer(conn, ctx.shop_id, "where am I losing money?", ctx.today)
        elif name == "get_margins":
            reply = await insights.answer(conn, ctx.shop_id, "margins", ctx.today)
        elif name == "promo_draft":
            reply = await growth.answer(conn, ctx.shop_id, ctx.text or "promo", ctx.products)
        elif name == "funding_readiness":
            reply = await growth.answer(conn, ctx.shop_id, "funding readiness", ctx.products)
        else:
            reply = None
        return reply

    reply = await _with_conn(ctx, read)
    if reply is None:
        return {"final": False, "error": f"Unknown tool {name}"}
    return {"final": True, "reply": reply}
