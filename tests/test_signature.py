import hashlib
import hmac

from app.webhook.signature import sign_body, verification_ok, verify_signature


def test_sign_and_verify_round_trip():
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    secret = "app-secret"
    header = sign_body(body, secret)
    assert header.startswith("sha256=")
    assert verify_signature(body, header, secret)


def test_signature_rejects_tampered_body():
    body = b'{"object":"whatsapp_business_account"}'
    header = sign_body(body, "app-secret")
    assert not verify_signature(body + b" ", header, "app-secret")


def test_signature_rejects_wrong_secret_and_missing_header():
    body = b"{}"
    header = sign_body(body, "app-secret")
    assert not verify_signature(body, header, "other-secret")
    assert not verify_signature(body, None, "app-secret")
    assert not verify_signature(body, "sha256=abcd", "app-secret")
    assert not verify_signature(body, header, "")


def test_signature_is_sha256_hmac_of_raw_bytes():
    body = b'{"a":1}'
    secret = "s3cret"
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sign_body(body, secret) == "sha256=" + expected
    assert verify_signature(body, "SHA256=" + expected, secret) is False
    assert verify_signature(body, "sha256=" + expected.upper(), secret)


def test_verify_token_handshake():
    assert verification_ok("subscribe", "token", "token")
    assert not verification_ok("subscribe", "nope", "token")
    assert not verification_ok("unsubscribe", "token", "token")
    assert not verification_ok("subscribe", "token", "")
    assert not verification_ok(None, None, "token")
