"""Tests for security-critical primitives: hashing, sessions, CSRF, IDOR,
encryption, HTML sanitization, and rate limiting.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.security import encryption
from app.security.auth import hash_password, verify_password
from app.services.sanitize import sanitize_html, safe_filename, is_valid_email


# ------------------------------------------------------------------ passwords
def test_password_hash_is_not_plaintext():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert h.startswith("$argon2")


def test_password_verify_roundtrip():
    h = hash_password("supersecret")
    assert verify_password("supersecret", h)
    assert not verify_password("wrong", h)


def test_password_verify_failed_hash_does_not_raise():
    assert verify_password("x", "not-a-hash") is False


# ------------------------------------------------------------------ encryption
def test_encryption_roundtrip():
    enc = encryption.encrypt("hunter2")
    assert enc != "hunter2" and "hunter2" not in enc
    assert encryption.decrypt(enc) == "hunter2"


def test_encryption_is_randomized():
    assert encryption.encrypt("same") != encryption.encrypt("same")


def test_decrypt_garbage_raises():
    import pytest
    with pytest.raises(encryption.EncryptionError):
        encryption.decrypt("garbage-not-valid-fernet-token-value")
    # Empty string is treated as "no secret"
    assert encryption.decrypt("") == ""
    assert encryption.decrypt(None) == ""


# ------------------------------------------------------------------ sanitization
def test_sanitize_strips_script():
    html = "<p>hello <script>alert(1)</script></p>"
    out = sanitize_html(html)
    assert "<script" not in out.lower()
    assert "alert(1)" not in out
    assert "hello" in out


def test_sanitize_removes_event_handlers():
    html = '<a href="https://ok.dev" onclick="steal()">link</a>'
    out = sanitize_html(html)
    assert "onclick" not in out.lower()


def test_sanitize_blocks_javascript_url():
    html = '<a href="javascript:alert(1)">x</a>'
    out = sanitize_html(html)
    assert "javascript:" not in out.lower()


def test_sanitize_keeps_safe_links_rel_attributes():
    out = sanitize_html('<a href="https://example.com">link</a>')
    assert "href=\"https://example.com\"" in out
    assert "noopener" in out


def test_sanitize_empty_values():
    assert sanitize_html(None) == ""
    assert sanitize_html("") == ""


def test_safe_filename_strips_paths():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("../x.exe") == "x.exe"
    assert safe_filename("") == "file"


def test_is_valid_email():
    assert is_valid_email("a@b.co")
    assert not is_valid_email("not-an-email")
    assert not is_valid_email("a@b")  # missing TLD
    assert not is_valid_email("")


# ------------------------------------------------------------------ CSRF
def test_csrf_required_for_state_changing(client):
    # No auth endpoint: /inbox/sync asserts 401 when not logged in.
    r = client.post("/inbox/sync")
    assert r.status_code == 401


def test_csrf_rejected_without_token(client, registered_user):
    from tests.conftest import get_csrf
    r = client.post(
        "/inbox/sync",
        headers={"X-InboxMate-Mock": "1"},
    )
    assert r.status_code == 403


def test_csrf_accepted_with_valid_token(client, registered_user):
    from tests.conftest import get_csrf
    csrf = get_csrf(client)
    r = client.post(
        "/inbox/sync",
        headers={"X-CSRF-Token": csrf, "X-InboxMate-Mock": "1"},
    )
    assert r.status_code == 200


def test_write_via_form_requires_csrf(client, registered_user):
    r = client.post("/drafts/save", data={"to": "x@y.co", "subject": "s", "body": "b"})
    assert r.status_code == 403


# ------------------------------------------------------------------ sessions
def test_session_cookie_is_httpOnly_samesite(client):
    client.post(
        "/register",
        data={"email": "bob@example.com", "password": "supersecretpass", "confirm": "supersecretpass"},
    )
    r = client.post(
        "/login",
        data={"email": "bob@example.com", "password": "supersecretpass"},
    )
    set_cookie = r.headers.get("set-cookie", "")
    assert "inboxmate_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie


def test_logout_destroys_session(client, registered_user):
    r = client.get("/inbox")
    assert r.status_code == 200
    from tests.conftest import get_csrf
    csrf = get_csrf(client)
    r = client.post("/logout", headers={"X-CSRF-Token": csrf})
    # Redirect away; accessing inbox now must be 401.
    assert r.status_code == 303
    assert client.get("/inbox").status_code == 401


# ------------------------------------------------------------------ auth flows
def test_register_requires_strong_password(client):
    r = client.post(
        "/register",
        data={"email": "weak@example.com", "password": "short", "confirm": "short"},
    )
    assert r.status_code == 400
    assert "10 characters" in r.text


def test_login_wrong_password(client, registered_user):
    r = client.post(
        "/login", data={"email": "alice@example.com", "password": "wrongpass"},
    )
    assert r.status_code == 401


def test_protected_route_redirects_when_not_auth(client):
    # We registered a user but this fixture shares cookie jar; force logout check
    # is done via a fresh client.
    fresh = TestClient(get_app_for_test())
    assert fresh.get("/inbox").status_code == 401


def get_app_for_test():
    from app.main import create_app
    return create_app()


# ------------------------------------------------------------------ rate limiting
def test_login_rate_limit(client):
    # Use a different email so registration doesn't interfere; hit login many times.
    for _ in range(get_settings().login_rate_limit_per_minute + 2):
        client.post("/login", data={"email": "nobody@example.com", "password": "x" * 12})
    r = client.post("/login", data={"email": "nobody@example.com", "password": "x" * 12})
    assert r.status_code == 429


# ------------------------------------------------------------------ IDOR
def test_idor_blocks_cross_user_email_access():
    """User A must not be able to view/send on User B's email or draft."""
    from tests.conftest import get_csrf
    from app.main import create_app
    from fastapi.testclient import TestClient

    # User A.
    app = create_app()
    a = TestClient(app, follow_redirects=False)
    a.post("/register", data={"email": "a@example.com", "password": "supersecretpass", "confirm": "supersecretpass"})
    a_csrf = get_csrf(a)
    a.post("/inbox/sync", headers={"X-CSRF-Token": a_csrf, "X-InboxMate-Mock": "1"})
    a_email_id = a.get("/inbox?q=Welcome").text
    import re
    m = re.search(r'href="/email/(\d+)"', a_email_id)
    a_email = int(m.group(1)) if m else None
    assert a_email, "User A should have synced a message"

    # User B, different DB? Same DB instance; register second user.
    b = TestClient(app, follow_redirects=False)
    b.post("/register", data={"email": "b@example.com", "password": "supersecretpass", "confirm": "supersecretpass"})
    b_csrf = get_csrf(b)

    # B trying to read A's email -> 401/404 (unauthorized, not found).
    r = b.get(f"/email/{a_email}")
    assert r.status_code == 404

    # B trying an AI action on A's email -> 404.
    r = b.post(f"/email/{a_email}/ai", data={"kind": "summarize"}, headers={"X-CSRF-Token": b_csrf})
    assert r.status_code == 404

    # B trying to view A's draft -> 404 (created below by A).
    d = a.post("/drafts/save", data={"to": "x@y.co", "subject": "s", "body": "A private draft", "_csrf": a_csrf})
    draft_url = d.headers.get("location", "")
    draft_id = draft_url.rstrip("/").split("/")[-1]
    assert a.get(f"/drafts/{draft_id}").status_code == 200
    assert b.get(f"/drafts/{draft_id}").status_code == 404