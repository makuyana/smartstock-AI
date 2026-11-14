"""X-Hub-Signature-256 and the webhook verify handshake."""

import hashlib
import hmac
import re


def sign_body(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def verify_signature(body: bytes, header: str | None, secret: str) -> bool:
    """Fail closed. An empty app secret never matches."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    provided = header[len("sha256=") :].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", provided):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def verification_ok(mode: str | None, token: str | None, expected: str) -> bool:
    if mode != "subscribe" or not expected or token is None:
        return False
    return hmac.compare_digest(token, expected)
