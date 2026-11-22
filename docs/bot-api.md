# Bot API

The shop bots read and write SmartStock data over HTTPS. They do not open a Postgres connection.

Base URL: `https://smartstock-ai-2-8f59.onrender.com`

Send the secret in the `Authorization` header. The examples below use `$BOT_API_TOKEN`. Do not paste a real token into a file or a chat.

```bash
curl -sS \
  -H "Authorization: Bearer $BOT_API_TOKEN" \
  -H "X-Bot-Name: Orchestrator" \
  "https://smartstock-ai-2-8f59.onrender.com/bot/v1/shops"
```

`X-Bot-Name` is optional. It is only written to the server log so you can see which bot called.

## Turn it on

The API is off until `BOT_API_TOKEN` is set on the Render service. While it is blank, every bot route returns `503` and reads nothing.

1. On your own machine, run `openssl rand -hex 32`.
2. In Render, open the `smartstock-ai` service, then Environment, and add `BOT_API_TOKEN`.
3. Paste the value there. `render.yaml` lists the variable with `sync: false`, so a blueprint apply will not overwrite it.
4. Save and redeploy.

A missing or wrong token returns `401`. These routes are not listed on `/docs` or in the OpenAPI schema.

Each bot is limited to 120 requests a minute. A JSON body larger than 256 KB is refused.

## What you can read

Every shop route takes that shop's id in the path. A call for one shop does not return another shop's rows.

| Method and path | What it returns |
| --- | --- |
| `GET /bot/v1/shops` | Shop list: id, name, type, timezone, currency, demo flag |
| `GET /bot/v1/shops/{shop_id}` | That shop and its active menu |
| `GET /bot/v1/shops/{shop_id}/messages` | Messages. Default channel is `whatsapp` |
| `GET /bot/v1/shops/{shop_id}/sales` | Saved sales |
| `GET /bot/v1/shops/{shop_id}/stock` | Ingredient stock levels |
| `GET /bot/v1/shops/{shop_id}/stock/movements` | Stock movements |
| `GET /bot/v1/shops/{shop_id}/revenue` | Daily revenue |
| `GET /bot/v1/shops/{shop_id}/invoices` | Invoices and their lines |
| `GET /bot/v1/shops/{shop_id}/drafts` | Pending drafts. `status` can be `pending`, `confirmed`, `edited`, or `cancelled` |
| `GET /bot/v1/shops/{shop_id}/forecasts` | Saved prep forecasts |

List routes take `limit` (1 to 100, default 50) and `offset`. Date routes take `from` and `to` as `YYYY-MM-DD`. Messages take `since` and `until` as ISO datetimes, and `channel` (`whatsapp`, `instagram`, `demo`, `bot`, or `all`).

```bash
curl -sS \
  -H "Authorization: Bearer $BOT_API_TOKEN" \
  -H "X-Bot-Name: Intake" \
  "https://smartstock-ai-2-8f59.onrender.com/bot/v1/shops/SHOP_ID/messages?since=2026-11-01T00:00:00%2B02:00&limit=50"
```

```bash
curl -sS \
  -H "Authorization: Bearer $BOT_API_TOKEN" \
  -H "X-Bot-Name: Insights" \
  "https://smartstock-ai-2-8f59.onrender.com/bot/v1/shops/SHOP_ID/revenue?from=2026-11-01&to=2026-11-30"
```

```bash
curl -sS \
  -H "Authorization: Bearer $BOT_API_TOKEN" \
  -H "X-Bot-Name: Invoice" \
  "https://smartstock-ai-2-8f59.onrender.com/bot/v1/shops/SHOP_ID/invoices"
```

```bash
curl -sS \
  -H "Authorization: Bearer $BOT_API_TOKEN" \
  -H "X-Bot-Name: Inventory" \
  "https://smartstock-ai-2-8f59.onrender.com/bot/v1/shops/SHOP_ID/stock"
```

The responses leave out phone numbers, consent timestamps, WhatsApp and Instagram ids, message raw payloads, invoice file paths, and any token or secret that was stored on a draft. A draft whose action is `delete_shop_data` comes back as that action only. This API cannot run DELETE MY DATA.

## What you can write

A write uses one shop id in the path. If the body also has `shop_id`, it must be the same id. Demo shops, and any id that starts with `demo-`, are refused with `403`. WhatsApp and the demo page are unchanged.

Send `Idempotency-Key` (or `idempotency_key` in the JSON) when a retry must not create a second row. Use letters, numbers, and `._:-`, up to 80 characters. The same key on two shops is two different writes.

Sales, expenses, purchases, waste, usage, stock counts, invoices, usual prices, and targets go through the same checks as a confirmed WhatsApp message. A purchase still needs a cost. A sale still needs an item and a quantity above zero. Stock is not allowed to go negative.

```bash
curl -sS \
  -H "Authorization: Bearer $BOT_API_TOKEN" \
  -H "X-Bot-Name: Intake" \
  -H "Idempotency-Key: sale-2026-11-22-1" \
  -H "Content-Type: application/json" \
  -d '{"item_name":"classic kota","quantity":"2","unit_price":"25","record_date":"2026-11-22","sale_hour":11}' \
  "https://smartstock-ai-2-8f59.onrender.com/bot/v1/shops/SHOP_ID/sales"
```

```bash
curl -sS \
  -H "Authorization: Bearer $BOT_API_TOKEN" \
  -H "X-Bot-Name: Orchestrator" \
  -H "Content-Type: application/json" \
  -d '{"kind":"expense","amount":"50","category":"rent","record_date":"2026-11-22"}' \
  "https://smartstock-ai-2-8f59.onrender.com/bot/v1/shops/SHOP_ID/records"
```

`kind` on `/records` may be `sale`, `purchase`, `expense`, `waste`, `usage`, `stock_count`, `invoice`, `set_price`, or `set_target`.

```bash
curl -sS \
  -H "Authorization: Bearer $BOT_API_TOKEN" \
  -H "X-Bot-Name: Inventory" \
  -H "Idempotency-Key: move-1" \
  -H "Content-Type: application/json" \
  -d '{"item_name":"bread loaf","movement_type":"sale_deduction","qty_change":"-1","movement_date":"2026-11-22"}' \
  "https://smartstock-ai-2-8f59.onrender.com/bot/v1/shops/SHOP_ID/stock/movements"
```

`movement_type` is `purchase` (positive quantity), `sale_deduction` or `waste` (negative), or `count_adjustment` or `manual`. The ingredient must already belong to that shop. The first idempotency key wins. A later retry does not apply a different quantity.

```bash
curl -sS \
  -H "Authorization: Bearer $BOT_API_TOKEN" \
  -H "X-Bot-Name: Forecast" \
  -H "Content-Type: application/json" \
  -d '{"product_name":"classic kota","target_date":"2026-11-23","predicted_qty":"12","range_low":"10","range_high":"14","method":"weekday_average","confidence":"medium"}' \
  "https://smartstock-ai-2-8f59.onrender.com/bot/v1/shops/SHOP_ID/forecasts"
```

Saving the same shop, date, product, and method again replaces that forecast. The product must already be on the shop menu.

```bash
curl -sS \
  -H "Authorization: Bearer $BOT_API_TOKEN" \
  -H "X-Bot-Name: Intake" \
  -H "Idempotency-Key: draft-1" \
  -H "Content-Type: application/json" \
  -d '{"kind":"sale","read_back":"Sold 2 classic kota.","payload":{"item_name":"classic kota","quantity":"2"}}' \
  "https://smartstock-ai-2-8f59.onrender.com/bot/v1/shops/SHOP_ID/drafts"
```

A new draft does not cancel the owner's pending YES/NO. Promo text is not stored: the WhatsApp growth path only replies with the draft, and there is no promo table.

Errors are short: `invalid`, `not found`, `forbidden`, `conflict`, `too large`, `too many requests`, `unauthorized`, `unavailable`, or `error`. They do not include row contents or the token.
