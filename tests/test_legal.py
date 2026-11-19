from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PAGES = (
    ("/privacy", "Privacy Policy"),
    ("/terms", "Terms of Service"),
    ("/data-deletion", "User Data Deletion Instructions"),
    ("/legal", "Legal"),
)


def _client():
    app = create_app(
        Settings(
            meta_app_secret="test-secret",
            webhook_verify_token="verify-me",
            database_url="",
            scheduler_enabled=False,
            llm_api_key="",
            seed_demo=False,
        )
    )
    return TestClient(app)


def test_legal_pages_are_public_html():
    with _client() as client:
        for path, heading in PAGES:
            page = client.get(path)
            assert page.status_code == 200
            assert page.headers["content-type"].startswith("text/html")
            assert "<title>SmartStock AI</title>" in page.text
            assert f"<h1>{heading}</h1>" in page.text
            assert "Effective date: 19 November 2026" in page.text


def test_each_policy_links_to_the_other_two_pages():
    with _client() as client:
        privacy = client.get("/privacy").text
        terms = client.get("/terms").text
        deletion = client.get("/data-deletion").text
        index = client.get("/legal").text

        assert 'href="/terms"' in privacy
        assert 'href="/data-deletion"' in privacy
        assert "Contact:" in privacy
        assert "compcos1511unisa@gmail.com" in privacy
        assert "We do not sell your personal or business data to third parties." in privacy

        assert 'href="/privacy"' in terms
        assert 'href="/data-deletion"' in terms
        assert "Meta Platform Terms" in terms

        assert 'href="/privacy"' in deletion
        assert 'href="/terms"' in deletion
        assert "Delete My SmartStock Data" in deletion
        assert "permanently deleted within 7 days." in deletion

        assert 'href="/privacy"' in index
        assert 'href="/terms"' in index
        assert 'href="/data-deletion"' in index


def test_legal_pages_do_not_change_existing_routes():
    with _client() as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        root = client.get("/")
        assert root.status_code == 200
        assert root.headers["content-type"].startswith("text/plain")

        demo = client.get("/demo")
        assert demo.status_code == 200
        assert "text/html" in demo.headers["content-type"]

        webhook = client.get("/webhook")
        assert webhook.status_code == 403
