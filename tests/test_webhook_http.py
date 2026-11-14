from fastapi.testclient import TestClient

from app.config import Settings, normalize_database_url
from app.main import create_app
from app.webhook.signature import sign_body

WA = b"""
{
  "object": "whatsapp_business_account",
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "27820001111",
          "id": "wamid.test",
          "timestamp": "1763000000",
          "type": "text",
          "text": {"body": "sold 12 chicken kota"}
        }]
      }
    }]
  }]
}
""".strip()


def _client():
    app = create_app(
        Settings(
            meta_app_secret="test-secret",
            webhook_verify_token="verify-me",
            database_url="",
            scheduler_enabled=False,
            llm_api_key="",
        )
    )
    return TestClient(app)


def test_hosted_database_url_requires_ssl_and_local_urls_do_not():
    hosted = normalize_database_url("postgres://user:pw@db.example.com/smartstock")
    assert hosted.startswith("postgresql://")
    assert "sslmode=require" in hosted
    local = normalize_database_url(
        "postgresql://smartstock:smartstock@host.docker.internal:54329/smartstock"
    )
    assert "sslmode" not in local


def test_health_and_verify_handshake():
    with _client() as client:
        assert client.get("/health").json()["status"] == "ok"
        denied = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "123"})
        assert denied.status_code == 403
        ok = client.get(
            "/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "654321"},
        )
        assert ok.status_code == 200
        assert ok.text == "654321"


def test_post_rejects_bad_signature_and_accepts_a_good_one():
    with _client() as client:
        bad = client.post("/webhook", content=WA, headers={"X-Hub-Signature-256": "sha256=" + "ab" * 32})
        assert bad.status_code == 401
        good = client.post(
            "/webhook",
            content=WA,
            headers={"X-Hub-Signature-256": sign_body(WA, "test-secret"), "Content-Type": "application/json"},
        )
        assert good.status_code == 200
