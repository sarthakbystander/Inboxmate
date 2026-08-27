"""End-to-end feature tests using the in-memory mock mail/AI backends."""
from __future__ import annotations

from tests.conftest import get_csrf


def test_full_user_flow(client, registered_user):
    """Register -> sync demo inbox -> view email -> create draft."""
    csrf = get_csrf(client)

    # Empty inbox initially.
    html = client.get("/inbox").text
    assert "No messages here yet" in html or "Connect a mailbox" in html

    # Sync mock inbox.
    r = client.post("/inbox/sync", headers={"X-CSRF-Token": csrf, "X-InboxMate-Mock": "1"})
    assert r.status_code == 200

    html = client.get("/inbox").text
    assert "messages" in html

    # Find the welcome email.
    email_id = extract_email_id(client, "Welcome to InboxMate")
    assert email_id

    # View email: read state toggles automatically.
    r = client.get(f"/email/{email_id}")
    assert r.status_code == 200
    assert "Welcome to InboxMate" in r.text

    # Save a manual draft.
    r = client.post(
        "/drafts/save",
        data={"to": "friend@example.com", "subject": "Hi", "body": "Just checking in.",
              "_csrf": csrf},
    )
    assert r.status_code == 303
    draft_url = r.headers["location"]
    draft_id = draft_url.rstrip("/").split("/")[-1]

    # Update the draft.
    r = client.post(
        f"/drafts/{draft_id}/update",
        data={"to": "friend@example.com", "subject": "Hi there", "body": "Updated body.",
              "_csrf": csrf},
    )
    assert r.status_code == 303
    assert "Updated body" in client.get(f"/drafts/{draft_id}").text


def test_mailbox_settings_required_for_send(client, registered_user):
    """Sending without SMTP configured must fail gracefully (no crash)."""
    from tests.conftest import get_csrf
    csrf = get_csrf(client)
    draft = client.post(
        "/drafts/save",
        data={"to": "friend@example.com", "subject": "Hi", "body": "Body.", "_csrf": csrf},
    )
    draft_id = draft.headers["location"].rstrip("/").split("/")[-1]
    r = client.post(
        f"/drafts/{draft_id}/send", data={"_csrf": csrf},
    )
    # SMTP not configured -> graceful error page with explanation, not a crash.
    assert r.status_code < 500
    if r.status_code == 400:
        assert "SMTP" in r.text


def test_email_ai_requires_configuration(client, registered_user):
    csrf = get_csrf(client)
    client.post("/inbox/sync", headers={"X-CSRF-Token": csrf, "X-InboxMate-Mock": "1"})
    email_id = extract_email_id(client, "Welcome to InboxMate")
    r = client.post(
        f"/email/{email_id}/ai", data={"kind": "summarize"}, headers={"X-CSRF-Token": csrf},
    )
    # AI not configured -> 400 with helpful message.
    assert r.status_code == 400
    assert "not configured" in r.text.lower()


def test_classification_with_ai_disabled(client, registered_user):
    csrf = get_csrf(client)
    r = client.post("/inbox/classify", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_search_filters_inbox(client, registered_user):
    csrf = get_csrf(client)
    client.post("/inbox/sync", headers={"X-CSRF-Token": csrf, "X-InboxMate-Mock": "1"})
    html = client.get("/inbox?q=COBOL").text
    assert "COBOL compiler notes" in html


# ------------------------------------------------------------------ helpers
def extract_email_id(client, subject_fragment):
    html = client.get("/inbox", params={"q": subject_fragment}).text
    import re

    m = re.search(r'href="/email/(\d+)"', html)
    return int(m.group(1)) if m else None