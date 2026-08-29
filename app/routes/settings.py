"""Settings routes: email + AI provider configuration and account."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.database import models
from app.database.models import log_audit
from app.templating import templates
from app.security import encryption
from app.security.auth import current_user, destroy_session, SESSION_COOKIE
from app.security.csrf import ensure_csrf_form
from app.services.sanitize import is_valid_email, safe_filename
from app.services import factory

router = APIRouter()


def _render_settings(request: Request, user: dict, message: str | None = None, error: str | None = None):
    row = models.get_settings(user["id"])
    decrypted = factory.decrypt_settings(row)
    has_mail_credentials = bool(decrypted.get("imap_password") or decrypted.get("smtp_password"))
    has_ai_key = bool(decrypted.get("ai_api_key"))
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "u": row, "user": user, "csrf": user.get("csrf_token", ""), "message": message,
            "error": error, "has_mail_credentials": has_mail_credentials,
            "has_ai_key": has_ai_key, "mail_configured": bool(row.get("email_address")),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: dict = Depends(current_user)):
    return _render_settings(request, user)


def _int_or_none(value: str, default: int | None = None) -> int | None:
    value = value.strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return None


@router.post("/settings/email")
def settings_email(
    request: Request,
    imap_host: str = Form(""),
    imap_port: str = Form(""),
    imap_use_ssl: str = Form(""),
    imap_username: str = Form(""),
    imap_password: str = Form(""),
    smtp_host: str = Form(""),
    smtp_port: str = Form(""),
    smtp_use_tls: str = Form(""),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    email_address: str = Form(""),
    display_name: str = Form(""),
    csrf_tok: str = Form(None, alias="_csrf"),
    user: dict = Depends(current_user),
):
    ensure_csrf_form(request, user["csrf_token"], csrf_tok, "")
    email_address = email_address.strip()
    if email_address and not is_valid_email(email_address):
        return _render_settings(request, user, error="Enter a valid email address.")
    updates = {
        "imap_host": imap_host.strip(),
        "imap_port": _int_or_none(imap_port),
        "imap_use_ssl": 1 if imap_use_ssl == "on" else 0,
        "smtp_host": smtp_host.strip(),
        "smtp_port": _int_or_none(smtp_port),
        "smtp_use_tls": 1 if smtp_use_tls == "on" else 0,
        "email_address": email_address,
        "display_name": safe_filename(display_name)[:60] if display_name else "",
    }
    # Encrypt credentials only when the user supplied new ones.
    if imap_username:
        updates["imap_username_enc"] = encryption.encrypt(imap_username.strip())
    if imap_password:
        updates["imap_password_enc"] = encryption.encrypt(imap_password)
    if smtp_username:
        updates["smtp_username_enc"] = encryption.encrypt(smtp_username.strip())
    if smtp_password:
        updates["smtp_password_enc"] = encryption.encrypt(smtp_password)
    models.save_settings(user["id"], updates)
    log_audit(user["id"], "settings_email", "updated email configuration")
    return _render_settings(request, user, message="Email settings saved.")


@router.post("/settings/ai")
def settings_ai(
    request: Request,
    ai_provider: str = Form("none"),
    ai_base_url: str = Form(""),
    ai_model: str = Form(""),
    ai_max_tokens: str = Form(""),
    ai_api_key: str = Form(""),
    csrf_tok: str = Form(None, alias="_csrf"),
    user: dict = Depends(current_user),
):
    ensure_csrf_form(request, user["csrf_token"], csrf_tok, "")
    provider = ai_provider.strip() if ai_provider.strip() in ("openai", "ollama", "none") else "none"
    updates = {
        "ai_provider": provider,
        "ai_base_url": ai_base_url.strip(),
        "ai_model": ai_model.strip(),
        "ai_max_tokens": _int_or_none(ai_max_tokens, 1000) or 1000,
    }
    if ai_api_key:
        updates["ai_api_key_enc"] = encryption.encrypt(ai_api_key)
    models.save_settings(user["id"], updates)
    log_audit(user["id"], "settings_ai", f"updated AI provider ({provider})")
    return _render_settings(request, user, message="AI settings saved.")


@router.post("/settings/ai/test")
def settings_ai_test(request: Request, user: dict = Depends(current_user)):
    from app.security.csrf import enforce_csrf_generic
    enforce_csrf_generic(request, user)
    from app.services.ai import build_ai_service_from_settings

    row = models.get_settings(user["id"])
    ai = build_ai_service_from_settings(row, lambda enc: factory.decrypt_settings(row)["ai_api_key"])
    if not ai.is_configured():
        return HTMLResponse('<p class="muted">Configure and save an AI provider first.</p>')
    try:
        reply = ai.summarize("test@example.com", "Connectivity check", "This is a connectivity test.")
    except Exception as exc:
        return HTMLResponse(f'<p class="error">Connection failed: {html_escape(str(exc))}</p>')
    return HTMLResponse('<p class="ok">Connected. Model responded successfully.</p>')


@router.post("/settings/ai/clear-key")
def settings_ai_clear_key(request: Request, csrf_tok: str = Form(None, alias="_csrf"), user: dict = Depends(current_user)):
    ensure_csrf_form(request, user["csrf_token"], csrf_tok, "")
    models.save_setting(user["id"], "ai_api_key_enc", "")
    return _render_settings(request, user, message="AI API key removed.")


@router.post("/settings/email/clear-credentials")
def settings_email_clear(request: Request, csrf_tok: str = Form(None, alias="_csrf"), user: dict = Depends(current_user)):
    ensure_csrf_form(request, user["csrf_token"], csrf_tok, "")
    models.save_settings(
        user["id"],
        {
            "imap_username_enc": "", "imap_password_enc": "",
            "smtp_username_enc": "", "smtp_password_enc": "",
        },
    )
    return _render_settings(request, user, message="Stored credentials cleared.")


@router.post("/settings/account/delete")
def settings_delete_account(request: Request, csrf_tok: str = Form(None, alias="_csrf"), user: dict = Depends(current_user)):
    ensure_csrf_form(request, user["csrf_token"], csrf_tok, "")
    log_audit(user["id"], "account_delete", "account deleted")
    conn = models.get_connection()
    conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    conn.commit()
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        destroy_session(session_id)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def html_escape(value: str) -> str:
    import html as _h

    return _h.escape(value)