#!/usr/bin/env python3
"""Send a signed WhatsApp message, then an Instagram message, at a running app.

Checks that both messages are stored and that the pretend Meta server got a reply.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.webhook.signature import sign_body

APP = os.environ.get("APP_URL", "http://127.0.0.1:43123")
MOCK = os.environ.get("GRAPH_MOCK_URL", "http://127.0.0.1:18099")
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://smartstock:smartstock@127.0.0.1:54329/smartstock",
)
SECRET = os.environ.get("META_APP_SECRET", "dev-app-secret")


def wait_url(url: str, seconds: int = 60) -> None:
    deadline = time.time() + seconds
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last = str(exc)
        time.sleep(1)
    raise SystemExit(f"not ready: {url} ({last})")


def post_json(url: str, body: bytes, headers: dict) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"POST {url} failed: {exc.code} {exc.read()[:300]!r}") from exc


def seed() -> None:
    sql = (ROOT / "scripts" / "seed_demo.sql").read_text()
    result = subprocess.run(
        ["psql", DATABASE_URL, "-v", "ON_ERROR_STOP=1"],
        input=sql,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)


def query(external_id: str) -> list[tuple[str, str]]:
    sql = (
        "SELECT direction || '|' || replace(COALESCE(body, ''), E'\\n', ' ') "
        "FROM messages WHERE shop_id = 'kota-shop-001' AND ("
        f"external_id = '{external_id}' "
        f"OR raw->>'in_reply_to_external' = '{external_id}') "
        "ORDER BY created_at;"
    )
    result = subprocess.run(
        ["psql", DATABASE_URL, "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr)
    rows = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        direction, _, body = line.partition("|")
        rows.append((direction, body))
    return rows


def wait_rows(external_id: str, count: int) -> list[tuple[str, str]]:
    deadline = time.time() + 20
    rows: list[tuple[str, str]] = []
    while time.time() < deadline:
        rows = query(external_id)
        if len(rows) >= count:
            return rows
        time.sleep(0.4)
    return rows


def mock_calls() -> list[dict]:
    with urllib.request.urlopen(MOCK + "/calls", timeout=5) as response:
        return json.loads(response.read().decode())


def send(payload: dict) -> None:
    body = json.dumps(payload).encode()
    status = post_json(
        APP + "/webhook",
        body,
        {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sign_body(body, SECRET),
        },
    )
    if status != 200:
        raise SystemExit(f"webhook status {status}")


def main() -> None:
    wait_url(APP + "/ready")
    wait_url(MOCK + "/health")
    seed()
    urllib.request.urlopen(MOCK + "/reset", timeout=5).read()

    wa_id = "wamid.SIM." + uuid.uuid4().hex
    ig_id = "mid.SIM." + uuid.uuid4().hex
    send(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "contacts": [
                                    {"profile": {"name": "Demo Owner"}, "wa_id": "27820001111"}
                                ],
                                "messages": [
                                    {
                                        "from": "27820001111",
                                        "id": wa_id,
                                        "timestamp": "1763000000",
                                        "type": "text",
                                        "text": {"body": "sold 12 chicken kota 8 russian"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
    )
    rows = wait_rows(wa_id, 2)
    if len(rows) < 2:
        raise SystemExit(f"expected stored inbound and outbound, got {rows!r}")
    directions = [direction for direction, _ in rows]
    outbound = " ".join(body for direction, body in rows if direction == "outbound")
    if "inbound" not in directions or "outbound" not in directions:
        raise SystemExit(f"missing direction in {rows!r}")
    if "chicken kota" not in outbound or "YES" not in outbound or "russian" not in outbound:
        raise SystemExit(f"unexpected WhatsApp reply: {outbound}")

    yes_id = "wamid.YES." + uuid.uuid4().hex
    send(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "27820001111",
                                        "id": yes_id,
                                        "timestamp": "1763000002",
                                        "type": "text",
                                        "text": {"body": "YES"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ],
        }
    )
    yes_rows = wait_rows(yes_id, 2)
    yes_out = " ".join(body for direction, body in yes_rows if direction == "outbound")
    if "Saved" not in yes_out or "recipe" not in yes_out.lower():
        raise SystemExit(f"unexpected confirm reply: {yes_out}")
    saved = subprocess.run(
        [
            "psql",
            DATABASE_URL,
            "-v",
            "ON_ERROR_STOP=1",
            "-t",
            "-A",
            "-c",
            """
            SELECT p.name || ' ' || trim(to_char(se.quantity, 'FM999990.###'))
            FROM sales_entries se
            JOIN products p ON p.id = se.product_id
            WHERE se.shop_id = 'kota-shop-001'
            ORDER BY se.created_at DESC
            LIMIT 2;
            """,
        ],
        text=True,
        capture_output=True,
    )
    if saved.returncode != 0:
        raise SystemExit(saved.stderr)
    saved_text = saved.stdout
    if "chicken kota 12" not in saved_text or "russian kota 8" not in saved_text:
        raise SystemExit(f"sales were not saved: {saved_text!r}")

    send(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "27820001111",
                                        "id": wa_id,
                                        "timestamp": "1763000001",
                                        "type": "text",
                                        "text": {"body": "sold 12 chicken kota 8 russian"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ],
        }
    )
    time.sleep(1.5)
    if len(query(wa_id)) != 2:
        raise SystemExit("duplicate webhook stored or replied again")

    send(
        {
            "object": "instagram",
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": "ig-demo-owner"},
                            "recipient": {"id": "987654321"},
                            "timestamp": 1763000002000,
                            "message": {"mid": ig_id, "text": "bought 20 bread R180"},
                        }
                    ]
                }
            ],
        }
    )
    ig_rows = wait_rows(ig_id, 2)
    ig_out = " ".join(body for direction, body in ig_rows if direction == "outbound")
    if "bread" not in ig_out or "180" not in ig_out or "YES" not in ig_out:
        raise SystemExit(f"unexpected Instagram reply: {ig_rows!r}")

    calls = mock_calls()
    paths = [call["path"] for call in calls]
    if not any("/123456789/messages" in path for path in paths):
        raise SystemExit(f"WhatsApp send was not called: {paths}")
    if not any("/987654321/messages" in path for path in paths):
        raise SystemExit(f"Instagram send was not called: {paths}")
    if not all(call["has_auth"] for call in calls):
        raise SystemExit("a Graph call was missing its bearer token")
    wa_body = next(call["body"] for call in calls if "/123456789/messages" in call["path"])
    if "chicken" not in wa_body:
        raise SystemExit(f"WhatsApp payload did not include the reply: {wa_body[:400]}")
    print("ok")
    print(f"whatsapp reply: {outbound}")
    print(f"instagram reply: {ig_out}")
    print(f"graph calls: {len(calls)}")


if __name__ == "__main__":
    main()
