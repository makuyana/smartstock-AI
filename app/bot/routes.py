"""HTTP routes for the trusted shop bots. Hidden from the public OpenAPI schema."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.bot.guard import body_too_large, require_bot
from app.bot.service import (
    BotError,
    Invalid,
    draft_query,
    idempotency_key,
    list_drafts,
    list_forecasts,
    list_invoices,
    list_messages,
    list_movements,
    list_revenue,
    list_sales,
    list_shops,
    list_stock,
    message_query,
    page_from,
    save_draft,
    save_forecast,
    save_movement,
    save_record,
    shop_profile,
)
from app.timeutil import jhb_now

logger = logging.getLogger("smartstock.bot")

router = APIRouter(
    prefix="/bot/v1",
    include_in_schema=False,
    dependencies=[Depends(require_bot)],
)


async def _run(fn):
    try:
        return await fn()
    except BotError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    except HTTPException:
        raise
    except Exception:
        logger.exception("bot api failed")
        raise HTTPException(status_code=500, detail="error") from None


async def _json(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/json"):
        raise Invalid()
    raw = await request.body()
    if body_too_large(raw):
        raise HTTPException(status_code=413, detail="too large")
    try:
        body = await request.json()
    except Exception:
        raise Invalid() from None
    if not isinstance(body, dict):
        raise Invalid()
    return body


def _today():
    return jhb_now().date()


@router.get("/shops")
async def get_shops(request: Request):
    async def fn():
        _start, _end, limit, offset = page_from(request.query_params)
        shops = await list_shops(request.app.state.db, limit, offset)
        return {"shops": shops, "limit": limit, "offset": offset}

    return await _run(fn)


@router.get("/shops/{shop_id}")
async def get_shop(shop_id: str, request: Request):
    async def fn():
        return await shop_profile(request.app.state.db, shop_id)

    return await _run(fn)


@router.get("/shops/{shop_id}/messages")
async def get_messages(shop_id: str, request: Request):
    async def fn():
        channel, since, until, limit, offset = message_query(request.query_params)
        rows = await list_messages(
            request.app.state.db, shop_id, channel, since, until, limit, offset
        )
        return {"messages": rows, "limit": limit, "offset": offset}

    return await _run(fn)


@router.get("/shops/{shop_id}/sales")
async def get_sales(shop_id: str, request: Request):
    async def fn():
        start, end, limit, offset = page_from(request.query_params)
        rows = await list_sales(request.app.state.db, shop_id, start, end, limit, offset)
        return {"sales": rows, "limit": limit, "offset": offset}

    return await _run(fn)


@router.get("/shops/{shop_id}/stock")
async def get_stock(shop_id: str, request: Request):
    async def fn():
        _start, _end, limit, offset = page_from(request.query_params)
        rows = await list_stock(request.app.state.db, shop_id, limit, offset)
        return {"stock": rows, "limit": limit, "offset": offset}

    return await _run(fn)


@router.get("/shops/{shop_id}/stock/movements")
async def get_movements(shop_id: str, request: Request):
    async def fn():
        start, end, limit, offset = page_from(request.query_params)
        rows = await list_movements(request.app.state.db, shop_id, start, end, limit, offset)
        return {"movements": rows, "limit": limit, "offset": offset}

    return await _run(fn)


@router.get("/shops/{shop_id}/revenue")
async def get_revenue(shop_id: str, request: Request):
    async def fn():
        start, end, limit, offset = page_from(request.query_params)
        rows = await list_revenue(request.app.state.db, shop_id, start, end, limit, offset)
        return {"revenue": rows, "limit": limit, "offset": offset}

    return await _run(fn)


@router.get("/shops/{shop_id}/invoices")
async def get_invoices(shop_id: str, request: Request):
    async def fn():
        start, end, limit, offset = page_from(request.query_params)
        rows = await list_invoices(request.app.state.db, shop_id, start, end, limit, offset)
        return {"invoices": rows, "limit": limit, "offset": offset}

    return await _run(fn)


@router.get("/shops/{shop_id}/drafts")
async def get_drafts(shop_id: str, request: Request):
    async def fn():
        status, limit, offset = draft_query(request.query_params)
        rows = await list_drafts(request.app.state.db, shop_id, status, limit, offset)
        return {"drafts": rows, "limit": limit, "offset": offset}

    return await _run(fn)


@router.get("/shops/{shop_id}/forecasts")
async def get_forecasts(shop_id: str, request: Request):
    async def fn():
        start, end, limit, offset = page_from(request.query_params)
        rows = await list_forecasts(request.app.state.db, shop_id, start, end, limit, offset)
        return {"forecasts": rows, "limit": limit, "offset": offset}

    return await _run(fn)


@router.post("/shops/{shop_id}/sales")
async def post_sale(shop_id: str, request: Request):
    async def fn():
        body = await _json(request)
        body = {**body, "kind": "sale"}
        key = idempotency_key(request.headers.get("idempotency-key"), body)
        return await save_record(request.app.state.db, shop_id, body, key, _today())

    return await _run(fn)


@router.post("/shops/{shop_id}/records")
async def post_record(shop_id: str, request: Request):
    async def fn():
        body = await _json(request)
        key = idempotency_key(request.headers.get("idempotency-key"), body)
        return await save_record(request.app.state.db, shop_id, body, key, _today())

    return await _run(fn)


@router.post("/shops/{shop_id}/stock/movements")
async def post_movement(shop_id: str, request: Request):
    async def fn():
        body = await _json(request)
        key = idempotency_key(request.headers.get("idempotency-key"), body)
        return await save_movement(request.app.state.db, shop_id, body, key, _today())

    return await _run(fn)


@router.post("/shops/{shop_id}/forecasts")
async def post_forecast(shop_id: str, request: Request):
    async def fn():
        body = await _json(request)
        return await save_forecast(request.app.state.db, shop_id, body)

    return await _run(fn)


@router.post("/shops/{shop_id}/drafts")
async def post_draft(shop_id: str, request: Request):
    async def fn():
        body = await _json(request)
        key = idempotency_key(request.headers.get("idempotency-key"), body)
        return await save_draft(request.app.state.db, shop_id, body, key)

    return await _run(fn)


@router.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def unknown(rest: str):
    return JSONResponse({"detail": "not found"}, status_code=404)
