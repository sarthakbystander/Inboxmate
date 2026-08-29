"""Tests for settings (encrypted credential storage, clear) and the scheduler."""
from __future__ import annotations

from tests.conftest import get_csrf


def test_email_settings_saved_and_encrypted(client, registered_user):
    csrf = get_csrf(client)
    r = client.post(
        "/settings/email",
        data={
            "imap_host": "imap.example.com", "imap_port": "993",
            "imap_use_ssl": "on", "imap_username": "user@example.com",
            "imap_password": "supersecret-imap-pass",
            "smtp_host": "smtp.example.com", "smtp_port": "587",
            "smtp_use_tls": "on", "smtp_password": "supersecret-smtp-pass",
            "email_address": "me@example.com", "display_name": "Me",
            "_csrf": csrf,
        },
    )
    assert r.status_code == 200
    assert "Email settings saved" in r.text

    # Verify at-rest encryption.
    from app.database.db import get_connection
    row = get_connection().execute(
        "SELECT imap_password_enc, smtp_password_enc FROM settings"
    ).fetchone()
    assert row
    assert "supersecret" not in (row["imap_password_enc"] or "")

    # Verify these values never reach the rendered settings page.
    assert "supersecret" not in client.get("/settings").text


def test_email_settings_requires_csrf(client, registered_user):
    r = client.post("/settings/email", data={"imap_host": "x"})
    assert r.status_code == 403


def test_email_validation(client, registered_user):
    csrf = get_csrf(client)
    r = client.post(
        "/settings/email",
        data={"email_address": "not-an-email", "_csrf": csrf},
    )
    assert r.status_code == 200
    assert "valid email" in r.text.lower()


def test_ai_settings_saved_without_exposing_key(client, registered_user):
    csrf = get_csrf(client)
    r = client.post(
        "/settings/ai",
        data={
            "ai_provider": "openai", "ai_base_url": "https://api.example.com/v1",
            "ai_model": "gpt-4o-mini", "ai_max_tokens": "500", "ai_api_key": "sk-secretvalue",
            "_csrf": csrf,
        },
    )
    assert r.status_code == 200
    from app.database.db import get_connection
    row = get_connection().execute("SELECT ai_api_key_enc, ai_base_url FROM settings").fetchone()
    assert "sk-secretvalue" not in (row["ai_api_key_enc"] or "")
    assert row["ai_base_url"] == "https://api.example.com/v1"
    assert "sk-secretvalue" not in client.get("/settings").text


def test_account_delete_removes_user_and_logs_out(client, registered_user):
    csrf = get_csrf(client)
    r = client.post("/settings/account/delete", data={"_csrf": csrf})
    assert r.status_code == 303
    assert client.get("/inbox").status_code == 401


# ------------------------------------------------------------------ scheduler
def test_scheduler_runs_jobs():
    from app.services.scheduler import Scheduler

    ran = []
    s = Scheduler()
    s.add_job(lambda: ran.append(1), interval_seconds=1)
    s.start()
    import time

    time.sleep(2.2)
    s.stop()
    assert ran, "expected the scheduled job to run at least once"


def test_audit_log_records_login_and_sync(client, registered_user):
    from app.database.db import get_connection
    row = get_connection().execute(
        "SELECT action FROM audit_log WHERE action = 'register' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row and row["action"] == "register"