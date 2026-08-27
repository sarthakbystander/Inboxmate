"""Inbox routes: mailbox sync + conversation listing + blank compose."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.database import models
from app.database.models import log_audit
from app.templating import templates
from app.security.auth import current_user
from app.services import factory
from app.services.mail import EmailFetchError, build_message, extract_attachments, get_backend
from app.services.sanitize import sanitize_html

router = APIRouter()


def run_sync(user_id: int, mock: bool = False) -> dict:
    """Fetch new mail for the user. Returns a summary dict."""
    settings_row = models.get_settings(user_id)
    if not mock and not (settings_row.get("imap_host") and settings_row.get("email_address")):
        return {"new": 0, "error": "Mailbox not configured"}
    cfg = {"email_address": settings_row.get("email_address", ""), "display_name": settings_row.get("display_name", "")}
    if mock:
        messages = get_backend(mock=True).fetch(cfg)
    else:
        try:
            decrypted = factory.decrypt_settings(settings_row)
        except Exception:
            return {"new": 0, "error": "Unable to decrypt stored credentials"}
        cfg = factory.get_mail_config(settings_row, decrypted)
        backend = get_backend(mock=False)
        try:
            messages = backend.fetch(cfg)
        except EmailFetchError as exc:
            return {"new": 0, "error": str(exc)}

    folder_id = models.upsert_folder(user_id, "Inbox")
    max_size = get_settings().max_email_size_bytes
    att_max = get_settings().max_attachment_size_bytes
    new_count = 0
    seen_uids: set[tuple[int, str]] = set()
    existing = models.get_connection().execute(
        "SELECT uid FROM emails WHERE user_id = ? AND folder_id = ?", (user_id, folder_id)
    ).fetchall()
    for r in existing:
        seen_uids.add((user_id, str(r["uid"])))

    for m in messages:
        parsed = build_message(m["raw"], max_size=max_size)
        if parsed is None:
            continue
        uid = str(m.get("uid", "0"))
        flags = [f.lower() for f in (m.get("flags") or [])]
        parsed["is_read"] = 1 if "\\seen" in flags else 0
        parsed["is_important"] = 1 if "\\flagged" in flags else 0
        parsed["body_html"] = sanitize_html(parsed.get("body_html", ""))
        is_new = (user_id, uid) not in seen_uids
        email_id = models.upsert_email(user_id, folder_id, uid, parsed)
        if is_new:
            new_count += 1
            for att in extract_attachments(m["raw"], max_size=att_max):
                models.add_attachment(
                    user_id, email_id, att["filename"], att["mimetype"],
                    att["size"], att["data"], att["is_inline"],
                )
        seen_uids.add((user_id, uid))
    if new_count:
        log_audit(user_id, "mail_sync", f"synced {new_count} new messages")
    return {"new": new_count}


def _ai_configured(row: dict) -> bool:
    return row.get("ai_provider") in ("openai", "ollama") and bool(row.get("ai_model"))


def _render_inbox(request: Request, user: dict, error: str | None = None):
    settings = get_settings()
    folder = request.query_params.get("folder", "Inbox")
    query = request.query_params.get("q", "").strip()
    page = max(1, int(request.query_params.get("page", "1") or 1))
    per_page = settings.inbox_page_size
    offset = (page - 1) * per_page

    if folder == "Drafts":
        drafts = models.list_drafts(user["id"])
        return templates.TemplateResponse(
            request, "drafts.html",
            {"folder": "Drafts", "drafts": drafts, "user": user, "csrf": user.get("csrf_token", ""), "error": error},
        )
    if folder == "Important":
        rows, total = models.get_important_emails(user["id"], limit=per_page, offset=offset)
        view = "important"
    elif folder == "Sent":
        rows, total = [], 0
        view = "sent"
    else:
        rows, total = models.list_emails(user["id"], folder="Inbox", query=query, limit=per_page, offset=offset)
        view = "inbox"

    unread = models.count_unread(user["id"])
    pages = max(1, -(-total // per_page))
    srow = models.get_settings(user["id"])
    mail_configured = bool(srow.get("email_address"))
    return templates.TemplateResponse(
        request,
        "inbox.html",
        {
            "emails": rows, "folder": folder, "query": query, "view": view,
            "page": page, "pages": pages, "total": total, "unread": unread,
            "user": user, "csrf": user.get("csrf_token", ""),
            "mail_configured": mail_configured,
            "ai_configured": _ai_configured(srow),
            "error": error,
        },
    )


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, user: dict = Depends(current_user)):
    return _render_inbox(request, user)


@router.post("/inbox/sync")
def sync_inbox(request: Request, user: dict = Depends(current_user)):
    from app.security.csrf import enforce_csrf_generic
    enforce_csrf_generic(request, user)
    mock = bool(request.headers.get("X-InboxMate-Mock", ""))
    result = run_sync(user["id"], mock=mock)
    log_audit(user["id"], "mail_sync_request")
    # Return just the email list fragment so HTMX can swap the #app-region.
    return _render_email_list_fragment(request, user, error=result.get("error"))


def _render_email_list_fragment(request: Request, user: dict, error: str | None = None):
    settings = get_settings()
    folder = request.query_params.get("folder", "Inbox")
    query = request.query_params.get("q", "").strip()
    rows, total = models.list_emails(user["id"], folder="Inbox", query=query,
                                     limit=settings.inbox_page_size, offset=0)
    unread = models.count_unread(user["id"])
    pages = max(1, -(-total // settings.inbox_page_size))
    return templates.TemplateResponse(
        request,
        "partials/_email_list.html",
        {"emails": rows, "folder": folder, "query": query, "page": 1, "pages": pages,
         "total": total, "unread": unread, "user": user, "csrf": user.get("csrf_token", ""),
         "error": error},
    )


@router.post("/inbox/classify")
def classify_inbox(request: Request, user: dict = Depends(current_user)):
    """Classify a batch of unclassified emails with the AI (if configured)."""
    from app.security.csrf import enforce_csrf_generic
    enforce_csrf_generic(request, user)
    srow = models.get_settings(user["id"])
    if not _ai_configured(srow):
        return HTMLResponse('<p class="muted">AI not configured in Settings.</p>', status_code=400)
    ai = _build_ai(user["id"], srow)
    rows = models.get_connection().execute(
        "SELECT id, sender, subject, snippet FROM emails WHERE user_id = ? AND classification = 'unclassified' AND body_html != '' LIMIT 25",
        (user["id"],),
    ).fetchall()
    classified = 0
    for r in rows:
        try:
            label = ai.classify(r["sender"], r["subject"], r["snippet"] or "(no content)")
            models.set_classification(int(r["id"]), user["id"], label)
            classified += 1
        except Exception:
            continue
    if classified:
        log_audit(user["id"], "ai_classify", f"classified {classified} emails")
    return HTMLResponse(f'<p class="ok">Classified {classified} email(s).</p>')


def _build_ai(user_id: int, row: dict):
    from app.services.ai import build_ai_service_from_settings

    return build_ai_service_from_settings(row, lambda enc: factory.decrypt_settings(row)["ai_api_key"])