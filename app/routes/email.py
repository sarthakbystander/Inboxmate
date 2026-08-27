"""Email routes: view a message, run AI operations, mark read/important."""
from __future__ import annotations

import html as html_mod

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.database import models
from app.database.models import log_audit
from app.templating import templates
from app.security.auth import current_user
from app.services import factory
from app.services.sanitize import sanitize_html

router = APIRouter()


def _load_email_or_404(email_id: int, user_id: int) -> dict:
    email = models.get_email(email_id, user_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


@router.get("/email/{email_id}", response_class=HTMLResponse)
def email_view(request: Request, email_id: int, user: dict = Depends(current_user)):
    email = _load_email_or_404(email_id, user["id"])
    attachments = models.list_attachments(email_id, user["id"])
    drafts = [d for d in models.list_drafts(user["id"]) if d.get("email_id") == email_id]
    unread = models.count_unread(user["id"])
    srow = models.get_settings(user["id"])
    ai_configured = srow.get("ai_provider") in ("openai", "ollama") and bool(srow.get("ai_model"))
    if not email["is_read"]:
        models.mark_read(email_id, user["id"], 1)
    return templates.TemplateResponse(
        request,
        "email.html",
        {
            "email": email,
            "attachments": attachments,
            "drafts": drafts,
            "unread": unread,
            "user": user,
            "csrf": user.get("csrf_token", ""),
            "ai_configured": ai_configured,
            "sanitized_body": sanitize_html(email["body_html"]),
        },
    )


@router.post("/email/{email_id}/ai")
def email_ai(request: Request, email_id: int, kind: str = Form("summarize"), user: dict = Depends(current_user)):
    from app.security.csrf import enforce_csrf_generic
    enforce_csrf_generic(request, user)
    email = _load_email_or_404(email_id, user["id"])
    srow = models.get_settings(user["id"])
    ai = _build_ai(user["id"], srow)
    if not ai.is_configured():
        return HTMLResponse('<p class="muted">AI is not configured. Add a provider in Settings.</p>', status_code=400)
    body = _plain_text(email)
    if kind == "summarize":
        result = ai.summarize(email["sender"], email["subject"], body)
        title = "Summary"
    elif kind == "classify":
        label = ai.classify(email["sender"], email["subject"], body)
        models.set_classification(email_id, user["id"], label)
        log_audit(user["id"], "ai_classify", f"email {email_id} -> {label}")
        return HTMLResponse(
            f'<span class="badge">{html_mod.escape(label)}</span> '
            f'<button class="btn btn-sm" hx-post="/email/{email_id}/classify" hx-target="#ai-box" hx-swap="innerHTML">Reclassify</button>'
        )
    elif kind == "draft":
        instruction = (request.query_params.get("instruction") or "").strip()
        result = ai.draft_reply(email["sender"], email["subject"], body, instruction)
        log_audit(user["id"], "ai_draft", f"generated draft for email {email_id}")
        return _render_draft_response(user, email, result)
    else:
        return HTMLResponse('<p class="muted">Unknown action.</p>', status_code=400)

    log_audit(user["id"], "ai_summarize", f"summarized email {email_id}")
    return HTMLResponse(
        f'<div class="ai-result"><h4>Summary</h4><p>{html_mod.escape(result)}</p></div>'
    )


@router.post("/email/{email_id}/classify")
def email_classify(request: Request, email_id: int, user: dict = Depends(current_user)):
    from app.security.csrf import enforce_csrf_generic
    enforce_csrf_generic(request, user)
    email = _load_email_or_404(email_id, user["id"])
    srow = models.get_settings(user["id"])
    ai = _build_ai(user["id"], srow)
    if not ai.is_configured():
        return HTMLResponse('<span class="muted">AI not configured</span>', status_code=400)
    label = ai.classify(email["sender"], email["subject"], _plain_text(email))
    models.set_classification(email_id, user["id"], label)
    log_audit(user["id"], "ai_classify", f"email {email_id} -> {label}")
    return HTMLResponse(f'<span class="badge">{html_mod.escape(label)}</span>')


@router.post("/email/{email_id}/read")
def email_mark_read(request: Request, email_id: int, is_read: int = Form(1), user: dict = Depends(current_user)):
    from app.security.csrf import enforce_csrf_generic
    enforce_csrf_generic(request, user)
    _load_email_or_404(email_id, user["id"])
    models.mark_read(email_id, user["id"], int(is_read))
    return HTMLResponse("")


@router.post("/email/{email_id}/important")
def email_important(request: Request, email_id: int, user: dict = Depends(current_user)):
    from app.security.csrf import enforce_csrf_generic
    enforce_csrf_generic(request, user)
    email = _load_email_or_404(email_id, user["id"])
    new_val = 0 if email["is_important"] else 1
    models.set_important(email_id, user["id"], new_val)
    label = "Unmark important" if new_val else "Mark important"
    return HTMLResponse(label)


def _render_draft_response(user: dict, email: dict, body: str):
    draft_id = models.create_draft(
        user["id"], email["id"], "", "Re: " + (email["subject"] or ""), body, is_ai_generated=1
    )
    return HTMLResponse(
        '<div class="ai-result draft"><p class="ok">Draft ready — review before sending.</p>'
        f'<p>{html_mod.escape(body)}</p>'
        f'<a class="btn btn-sm" href="/drafts/{draft_id}">Open & edit draft</a></div>'
    )


def _plain_text(email: dict) -> str:
    """Best-effort plain text of an email for the AI (minimal content)."""
    body = email.get("body_html", "") or ""
    # Strip tags crudely.
    import re

    text = re.sub(r"<[^>]+>", " ", body)
    import html as _h

    return _h.unescape(re.sub(r"\s+", " ", text)).strip()


def _build_ai(user_id: int, row: dict):
    from app.services.ai import build_ai_service_from_settings

    return build_ai_service_from_settings(row, lambda enc: factory.decrypt_settings(row)["ai_api_key"])


@router.get("/email/{email_id}/attachment/{attach_id}")
def email_attachment(email_id: int, attach_id: int, user: dict = Depends(current_user)):
    """Download an attachment. IDOR-guarded: both ids must belong to the user."""
    _load_email_or_404(email_id, user["id"])
    att = models.get_attachment(attach_id, user["id"])
    if not att or int(att["email_id"]) != email_id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    data = att["data"]
    if isinstance(data, str):
        data = data.encode("latin-1", errors="replace")
    content_disposition = f'attachment; filename="{att["filename"].replace(chr(34), "")}"'
    return Response(
        content=bytes(data),
        media_type=att["mimetype"] or "application/octet-stream",
        headers={"Content-Disposition": content_disposition, "X-Content-Type-Options": "nosniff"},
    )