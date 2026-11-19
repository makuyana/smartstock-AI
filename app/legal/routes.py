"""Public legal pages. No auth, database, env vars, or webhook checks."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_EFFECTIVE = "Effective date: 19 November 2026"
_EMAIL = "compcos1511unisa@gmail.com"

_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Segoe UI", Helvetica, Arial, sans-serif;
  background: #f4f7f6;
  color: #1a1a1a;
  line-height: 1.6;
  font-size: 16px;
}
main {
  max-width: 40rem;
  margin: 0 auto;
  padding: 1.5rem 1.25rem 3rem;
}
.brand {
  margin: 0;
  font-weight: 700;
  color: #075e54;
}
h1 {
  font-size: 1.6rem;
  line-height: 1.25;
  margin: 0.25rem 0 0.35rem;
}
.effective {
  margin: 0 0 1.25rem;
  color: #444;
}
p { margin: 0 0 1rem; }
ul { margin: 0 0 1rem; padding-left: 1.25rem; }
li { margin: 0.45rem 0; }
a { color: #075e54; }
nav {
  margin-top: 2rem;
  padding-top: 1rem;
  border-top: 1px solid #d5dedb;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.7rem;
}
nav a {
  font-weight: 600;
  padding: 0.15rem 0;
}
"""

_PRIVACY = (
    'SmartStock AI ("we", "our") provides an AI-powered inventory, sales, and '
    "business messaging assistant for small businesses via WhatsApp and Instagram. "
    "When you connect your business account or message our service, we collect and "
    "process your business profile identifiers, messages, sales logs, expenses, and "
    "supplier invoices strictly to operate inventory tracking, demand forecasting, "
    "and business analytics for your shop. We do not sell your personal or business "
    "data to third parties."
)

_TERMS = (
    "By connecting your WhatsApp or Instagram Business account to SmartStock AI, "
    "you authorize SmartStock AI to process incoming messages and publish responses "
    "or marketing posts on your behalf in accordance with Meta Platform Terms. You "
    "remain responsible for the accuracy of financial and stock records confirmed "
    "in your account."
)

_DELETION_INTRO = (
    "To delete your SmartStock AI account and all associated WhatsApp, Instagram, "
    "and shop records:"
)
_OPTION_A = (
    'Option A: Remove SmartStock AI from your Facebook/Instagram Settings under '
    '"Apps and Websites" and click "Send Request".'
)
_OPTION_B = (
    f'Option B: Email {_EMAIL} with the subject "Delete My SmartStock Data" and '
    "your business phone number or Instagram username. All associated records are "
    "permanently deleted within 7 days."
)


def _page(heading: str, body: str, links: list[tuple[str, str]]) -> str:
    nav = ""
    if links:
        anchors = "".join(f'<a href="{href}">{label}</a>' for href, label in links)
        nav = f'<nav aria-label="Other legal pages">{anchors}</nav>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SmartStock AI</title>
  <style>{_STYLE}</style>
</head>
<body>
  <main>
    <p class="brand">SmartStock AI</p>
    <h1>{heading}</h1>
    <p class="effective">{_EFFECTIVE}</p>
    {body}
    {nav}
  </main>
</body>
</html>
"""


def _email_link(text: str) -> str:
    linked = f'<a href="mailto:{_EMAIL}">{_EMAIL}</a>'
    return text.replace(_EMAIL, linked, 1)


def _links(*names: str) -> list[tuple[str, str]]:
    catalog = {
        "privacy": ("/privacy", "Privacy Policy"),
        "terms": ("/terms", "Terms of Service"),
        "deletion": ("/data-deletion", "User Data Deletion Instructions"),
        "index": ("/legal", "Legal"),
    }
    return [catalog[name] for name in names]


_PRIVACY_HTML = _page(
    "Privacy Policy",
    (
        f"<p>{_PRIVACY}</p>"
        f'<p>Contact: <a href="mailto:{_EMAIL}">{_EMAIL}</a>'
        ' · <a href="/data-deletion">User Data Deletion Instructions</a></p>'
    ),
    _links("terms", "deletion", "index"),
)

_TERMS_HTML = _page(
    "Terms of Service",
    f"<p>{_TERMS}</p>",
    _links("privacy", "deletion", "index"),
)

_DELETION_HTML = _page(
    "User Data Deletion Instructions",
    (
        f"<p>{_DELETION_INTRO}</p>"
        "<ul>"
        f"<li>{_OPTION_A}</li>"
        f"<li>{_email_link(_OPTION_B)}</li>"
        "</ul>"
    ),
    _links("privacy", "terms", "index"),
)

_INDEX_HTML = _page(
    "Legal",
    (
        "<ul>"
        '<li><a href="/privacy">Privacy Policy</a></li>'
        '<li><a href="/terms">Terms of Service</a></li>'
        '<li><a href="/data-deletion">User Data Deletion Instructions</a></li>'
        "</ul>"
    ),
    [],
)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy() -> HTMLResponse:
    return HTMLResponse(_PRIVACY_HTML)


@router.get("/terms", response_class=HTMLResponse)
async def terms() -> HTMLResponse:
    return HTMLResponse(_TERMS_HTML)


@router.get("/data-deletion", response_class=HTMLResponse)
async def data_deletion() -> HTMLResponse:
    return HTMLResponse(_DELETION_HTML)


@router.get("/legal", response_class=HTMLResponse)
async def legal_index() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML)
