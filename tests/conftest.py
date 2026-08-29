"""Pytest fixtures: isolated data directory + app under test.

Environment variables must be set before ``app.config`` is imported, so we
configure them at the top of this module. Each test gets a fresh app with
its own SQLite file and encryption key.

Note: the global ``app.config.get_settings`` singleton and the Fernet
key cache live at import time, so they are reset per-session rather than
per-test. Use ``app_config`` / ``reset_encryption`` fixtures where cross-test
isolation matters.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# --- isolate configuration before any app import ---------------------------
_tmp = tempfile.mkdtemp(prefix="inboxmate-test-")
os.environ["INBOXMATE_DATA_DIR"] = _tmp
os.environ["INBOXMATE_DATABASE_PATH"] = os.path.join(_tmp, "test.db")
os.environ["INBOXMATE_ENCRYPTION_KEY"] = "test-only-encryption-key"
os.environ["INBOXMATE_SECRET_KEY"] = "test-secret-key"

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

_test_counter = [0]


@pytest.fixture(autouse=True)
def _fresh_db():
    """Give every test a brand-new database + reset in-process mutable state."""
    from app.database.db import _LOCAL  # thread-local connection cache
    from app.security.ratelimit import reset_rate_limiter

    _test_counter[0] += 1
    # Point the connection at a unique file for this test.
    db_file = os.path.join(_tmp, f"test-{_test_counter[0]}.db")
    os.environ["INBOXMATE_DATABASE_PATH"] = db_file
    if os.path.exists(db_file):
        os.remove(db_file)
    for suffix in ("-wal", "-shm"):
        p = db_file + suffix
        if os.path.exists(p):
            os.remove(p)
    # Drop any cached thread-local connection so a fresh one opens.
    setattr(_LOCAL, "conn", None)
    reset_rate_limiter()
    yield
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        conn.close()
        setattr(_LOCAL, "conn", None)


@pytest.fixture
def client():
    from app.main import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def registered_user(client):
    """Register a fresh user and return (email, csrf, cookies)."""
    email = "alice@example.com"
    resp = client.post(
        "/register",
        data={
            "email": email,
            "password": "supersecretpass",
            "confirm": "supersecretpass",
        },
    )
    assert resp.status_code == 303, resp.text[:200]
    # Fetch inbox to obtain the CSRF token from the meta tag.
    return {"email": email, "csrf": get_csrf(client)}


def get_csrf(client):
    import re

    html = client.get("/inbox").text
    m = re.search(r'meta name="csrf-token" content="([^"]+)"', html)
    return m.group(1) if m else ""


@pytest.fixture
def csrf(client):
    return get_csrf(client)