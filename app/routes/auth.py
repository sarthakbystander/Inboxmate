"""Authentication routes: register, login, logout.

Session cookies are set/cleared here. CSRF tokens are minted with the
session and rendered into authenticated forms.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import get_settings
from app.database import models
from app.database.models import log_audit
from app.templating import templates
from app.security.auth import (
    SESSION_COOKIE,
    hash_password,
    verify_password,
    create_user_session,
    destroy_session,
    current_user,
    session_cookie_value,
    session_is_valid,
)
from app.security.csrf import ensure_csrf_form
from app.security.ratelimit import is_rate_limited
from app.services.sanitize import is_valid_email

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = ""):
    if session_is_valid(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/inbox", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"action": "login", "next": next}
    )


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if session_is_valid(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/inbox", status_code=303)
    return templates.TemplateResponse(request, "register.html", {"action": "register"})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
):
    settings = get_settings()
    if is_rate_limited(request, settings.login_rate_limit_per_minute):
        log_audit(None, "login_ratelimited", ip=_ip(request))
        return templates.TemplateResponse(
            request,
            "login.html",
            {"action": "login", "error": "Too many attempts. Please wait a minute.", "next": next},
            status_code=429,
        )
    user = models.get_user_by_email(email.strip().lower())
    if user and verify_password(password, user["password_hash"]):
        sess = create_user_session(user["id"], request)
        log_audit(user["id"], "login", "successful login", ip=_ip(request))
        response = RedirectResponse("/inbox", status_code=303)
        response.headers["Set-Cookie"] = session_cookie_value(sess["session_id"], request)
        return response
    log_audit(None, "login_failed", email, _ip(request))
    return templates.TemplateResponse(
        request,
        "login.html",
        {"action": "login", "error": "Invalid email or password.", "next": next},
        status_code=401,
    )


@router.post("/register")
def register(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    confirm: str = Form(""),
):
    settings = get_settings()
    if is_rate_limited(request, settings.login_rate_limit_per_minute):
        return templates.TemplateResponse(
            request,
            "register.html",
            {"action": "register", "error": "Too many attempts. Please wait a minute."},
            status_code=429,
        )
    email = email.strip().lower()
    if not is_valid_email(email):
        return templates.TemplateResponse(
            request, "register.html", {"action": "register", "error": "Enter a valid email address."},
            status_code=400,
        )
    if len(password) < 10:
        return templates.TemplateResponse(
            request, "register.html", {"action": "register", "error": "Password must be at least 10 characters."},
            status_code=400,
        )
    if password != confirm:
        return templates.TemplateResponse(
            request, "register.html", {"action": "register", "error": "Passwords do not match."},
            status_code=400,
        )
    if models.get_user_by_email(email):
        return templates.TemplateResponse(
            request, "register.html", {"action": "register", "error": "An account with that email already exists."},
            status_code=400,
        )
    user_id = models.create_user(email, hash_password(password))
    log_audit(user_id, "register", "account created", ip=_ip(request))
    sess = create_user_session(user_id, request)
    response = RedirectResponse("/inbox", status_code=303)
    response.headers["Set-Cookie"] = session_cookie_value(sess["session_id"], request)
    return response


@router.post("/logout")
def logout(request: Request, user: dict = Depends(current_user)):
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        destroy_session(session_id)
        log_audit(user["id"], "logout", "user logged out", ip=_ip(request))
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return getattr(request.client, "host", "") or ""