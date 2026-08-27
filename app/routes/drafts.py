"""Draft routes: create/edit drafts, and explicit SMTP sending.

Sending is a separate, authenticated, user-confirmed action. Drafts are
never auto-sent by the AI path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.database import models
from app.database.models import log_audit
from app.templating import templates
from app.security.auth import current_user
from app.security.csrf import ensure_csrf_form
from app.services import factory
from app.services.mail import EmailSendError, get_backend
from app.services.sanitize import is_valid_email, safe_filename, clean_subject

router = APIRouter()


def _load_draft_or_404(draft_id: int, user_id: int) -> dict:
    draft = models.get_draft(draft_id, user_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.get("/compose", response_class=HTMLResponse)
def compose_page(request: Request, to: str = "", user: dict = Depends(current_user)):
    return templates.TemplateResponse(
        request, "compose.html",
        {"to": to, "user": user, "csrf": user.get("csrf_token", ""), "editing": False},
    )


@router.post("/drafts/save")
def draft_save(
    request: Request,
    to: str = Form(""),
    subject: str = Form(""),
    body: str = Form(""),
    csrf_tok: str = Form(None, alias="_csrf"),
    user: dict = Depends(current_user),
):
    ensure_csrf_form(request, user["csrf_token"], csrf_tok, "")
    draft_id = models.create_draft(user["id"], None, to.strip(), clean_subject(subject), body, is_ai_generated=0)
    log_audit(user["id"], "draft_save", f"created draft {draft_id}")
    return RedirectResponse(f"/drafts/{draft_id}", status_code=303)


@router.post("/compose/reply")
def compose_reply(
    request: Request,
    email_id: str = Form(""),
    user: dict = Depends(current_user),
):
    email_id_int = int(email_id) if email_id.strip().lstrip("-").isdigit() else None
    email = models.get_email(email_id_int, user["id"]) if email_id_int else None
    to = email["sender"] if email else ""
    return templates.TemplateResponse(
        request, "compose.html",
        {"to": to, "user": user, "csrf": user.get("csrf_token", ""), "editing": False},
    )


@router.get("/drafts/{draft_id}", response_class=HTMLResponse)
def draft_view(request: Request, draft_id: int, user: dict = Depends(current_user)):
    draft = _load_draft_or_404(draft_id, user["id"])
    email = None
    if draft.get("email_id"):
        email = models.get_email(draft["email_id"], user["id"])
    return templates.TemplateResponse(
        request, "compose.html",
        {"draft": draft, "email": email, "to": draft["to_address"], "user": user,
         "csrf": user.get("csrf_token", ""), "editing": True},
    )


@router.post("/drafts/{draft_id}/update")
def draft_update(
    request: Request,
    draft_id: int,
    to: str = Form(""),
    subject: str = Form(""),
    body: str = Form(""),
    csrf_tok: str = Form(None, alias="_csrf"),
    user: dict = Depends(current_user),
):
    ensure_csrf_form(request, user["csrf_token"], csrf_tok, "")
    _load_draft_or_404(draft_id, user["id"])
    models.update_draft(draft_id, user["id"], body=body, subject=clean_subject(subject), to_address=to.strip())
    log_audit(user["id"], "draft_update", f"updated draft {draft_id}")
    return RedirectResponse(f"/drafts/{draft_id}?saved=1", status_code=303)


@router.post("/drafts/{draft_id}/send")
def draft_send(
    request: Request,
    draft_id: int,
    csrf_tok: str = Form(None, alias="_csrf"),
    user: dict = Depends(current_user),
):
    ensure_csrf_form(request, user["csrf_token"], csrf_tok, "")
    draft = _load_draft_or_404(draft_id, user["id"])
    to = draft["to_address"].strip()
    if not is_valid_email(to):
        return templates.TemplateResponse(
            request, "compose.html",
            {"draft": draft, "to": to, "user": user, "csrf": user.get("csrf_token", ""),
             "editing": True, "error": "Enter a valid recipient address."},
            status_code=400,
        )
    srow = models.get_settings(user["id"])
    if not (srow.get("smtp_host") and srow.get("email_address")):
        return templates.TemplateResponse(
            request, "compose.html",
            {"draft": draft, "to": to, "user": user, "csrf": user.get("csrf_token", ""),
             "editing": True, "error": "SMTP is not configured in Settings. Outgoing mail disabled."},
            status_code=400,
        )
    try:
        cfg = factory.get_mail_config(srow, factory.decrypt_settings(srow))
    except Exception:
        return templates.TemplateResponse(
            request, "compose.html",
            {"draft": draft, "to": to, "user": user, "csrf": user.get("csrf_token", ""),
             "editing": True, "error": "Could not decrypt stored credentials."},
            status_code=400,
        )
    backend = get_backend()
    try:
        backend.send(cfg, to, draft["subject"], draft["body"])
    except EmailSendError as exc:
        log_audit(user["id"], "email_send_failed", str(exc))
        return templates.TemplateResponse(
            request, "compose.html",
            {"draft": draft, "to": to, "user": user, "csrf": user.get("csrf_token", ""),
             "editing": True, "error": f"Send failed: {exc}"},
            status_code=502,
        )
    models.delete_draft(draft_id, user["id"])
    log_audit(user["id"], "email_sent", f"sent to {to} from draft {draft_id}")
    return RedirectResponse("/inbox?sent=1", status_code=303)


@router.post("/drafts/{draft_id}/delete")
def draft_delete(request: Request, draft_id: int, csrf_tok: str = Form(None, alias="_csrf"), user: dict = Depends(current_user)):
    ensure_csrf_form(request, user["csrf_token"], csrf_tok, "")
    _load_draft_or_404(draft_id, user["id"])
    models.delete_draft(draft_id, user["id"])
    log_audit(user["id"], "draft_delete", f"deleted draft {draft_id}")
    return RedirectResponse("/inbox?folder=Drafts", status_code=303)