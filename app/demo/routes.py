"""Browser demo. Same orchestrator as WhatsApp, only for shops marked is_demo."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.orchestrator.handle import build_reply
from app.store import fetchall, fetchone, insert_message, load_catalog
from app.timeutil import JOHANNESBURG

router = APIRouter()
PAGE = Path(__file__).with_name("page.html")

SUGGESTIONS = {
    "demo-kota": [
        "How did we do this week?",
        "What must I prep tomorrow?",
        "Where am I losing money?",
        "Show me the monthly report",
        "Sold 40 classic kota",
    ],
    "demo-beauty": [
        "How did we do this week?",
        "What must I prep tomorrow?",
        "Where am I losing money?",
        "Show me the monthly report",
        "Sold 5 braiding",
    ],
    "demo-courier": [
        "How did we do this week?",
        "What must I prep tomorrow?",
        "Where am I losing money?",
        "Show me the monthly report",
        "Sold 10 parcel drop",
    ],
    "demo-catering": [
        "How did we do this week?",
        "What must I prep tomorrow?",
        "Where am I losing money?",
        "Show me the monthly report",
        "Sold 4 vetkoek box",
    ],
}


@router.get("/demo", response_class=HTMLResponse)
async def demo_page():
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@router.get("/demo/shops")
async def demo_shops(request: Request):
    pool = request.app.state.db
    if pool is None:
        return {"shops": []}
    async with pool.connection() as conn:
        rows = await fetchall(
            conn,
            """
            SELECT id, name, business_type, demo_as_of
            FROM shops
            WHERE is_demo
            ORDER BY name
            """,
        )
    return {
        "shops": [
            {
                "id": row["id"],
                "name": row["name"],
                "business_type": row["business_type"],
                "as_of": row["demo_as_of"].isoformat() if row["demo_as_of"] else None,
                "suggestions": SUGGESTIONS.get(row["id"], SUGGESTIONS["demo-kota"]),
            }
            for row in rows
        ]
    }


@router.post("/demo/chat")
async def demo_chat(request: Request):
    pool = request.app.state.db
    if pool is None:
        return JSONResponse({"reply": "The database is not connected."}, status_code=503)
    payload = await request.json()
    shop_id = str(payload.get("shop_id") or "")
    text = str(payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"reply": "Type a message first."}, status_code=400)
    text = text[:1000]
    async with pool.connection() as conn:
        shop = await fetchone(
            conn,
            """
            SELECT id, demo_as_of FROM shops
            WHERE id = %s AND is_demo
            """,
            (shop_id,),
        )
        if shop is None:
            return JSONResponse({"reply": "Pick one of the demo businesses."}, status_code=404)
        user = await fetchone(
            conn,
            "SELECT id, consent_at FROM users WHERE shop_id = %s AND phone = %s",
            (shop_id, shop_id),
        )
        if user is None:
            return JSONResponse({"reply": "Demo user is missing. Run the seed again."}, status_code=500)
        as_of = shop["demo_as_of"]
        now = datetime.combine(as_of, datetime.min.time(), tzinfo=JOHANNESBURG).replace(hour=18)
        message_id = await insert_message(
            conn,
            shop_id=shop_id,
            user_id=user["id"],
            direction="inbound",
            channel="demo",
            body=text,
            media_url=None,
            sent_at=now,
            raw={"demo": True},
            external_id="demo-" + uuid4().hex,
        )
        products, ingredients = await load_catalog(conn, shop_id)
    ctx = SimpleNamespace(
        pool=pool,
        settings=request.app.state.settings,
        llm=request.app.state.llm,
        shop_id=shop_id,
        user_id=user["id"],
        message_id=message_id,
        channel="demo",
        sender_id=shop_id,
        text=text,
        today=as_of,
        now=now,
        consent_at=user["consent_at"],
        products=products,
        ingredients=ingredients,
        delete_after_send=False,
    )
    try:
        reply = await build_reply(ctx)
    except Exception:
        reply = "Something went wrong reading the demo sheet. Please try that question again."
    async with pool.connection() as conn:
        await insert_message(
            conn,
            shop_id=shop_id,
            user_id=user["id"],
            direction="outbound",
            channel="demo",
            body=reply,
            media_url=None,
            sent_at=now,
            raw={"demo": True, "in_reply_to": str(message_id)},
            external_id=None,
        )
    return {"reply": reply, "shop_id": shop_id}
