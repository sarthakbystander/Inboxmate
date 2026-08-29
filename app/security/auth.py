"""Authentication: Argon2 password hashing, session management, and the
FastAPI dependency that resolves the current user.

Sessions are stored server-side in SQLite. The client only ever holds an
opaque random session id inside an HttpOnly, SameSite=Lax cookie and a
CSRF token that is rendered into forms.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status

from app.database import models
from app.database.models import log_audit

_hasher = PasswordHasher()

SESSION_COOKIE = "inboxmate_session"

# ---- passwords ---------------------------------------------------------------
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, Exception):
        return False


def _new_session_token() -> str:
    return secrets.token_urlsafe(32)


def _new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def create_user_session(user_id: int, request: Request | None = None) -> dict:
    """Create a session row and return (session_id, csrf_token, expires_at)."""
    from app.config import get_settings

    session_id = _new_session_token()
    csrf = _new_csrf_token()
    expires = (
        datetime.utcnow() + timedelta(hours=get_settings().session_hours)
    ).strftime("%Y-%m-%d %H:%M:%S")
    models.create_session(session_id, user_id, csrf, expires)
    return {"session_id": session_id, "csrf_token": csrf, "expires_at": expires}


def destroy_session(session_id: str) -> None:
    models.delete_session(session_id)


def session_cookie_value(session_id: str, request: Request | None = None) -> str:
    secure = request.url.scheme == "https" if request else False
    max_age = 60 * 60 * 24 * 7
    return (
        f"inboxmate_session={session_id}; Path=/; HttpOnly; SameSite=Lax; "
        f"Max-Age={max_age};{ ' Secure;' if secure else ''}"
    )


def session_is_valid(session_id: str) -> bool:
    """True if the session exists and has not expired."""
    if not session_id:
        return False
    from app.config import get_settings

    session = models.get_session(session_id)
    if not session:
        return False
    if session["expires_at"] < datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"):
        models.delete_session(session_id)
        return False
    return models.get_user(int(session["user_id"])) is not None


# ---- current-user dependency --------------------------------------------------
def _read_session_cookie(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE)


def current_user(request: Request) -> dict:
    """Resolve and return the authenticated user, or raise 401."""
    session_id = _read_session_cookie(request)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session = models.get_session(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    # Session expiration check.
    from app.config import get_settings
    if session["expires_at"] < datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"):
        models.delete_session(session_id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = models.get_user(int(session["user_id"]))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    user["session_id"] = session_id
    user["csrf_token"] = session["csrf_token"]
    return user


CurrentUser = Annotated[dict, Depends(current_user)]