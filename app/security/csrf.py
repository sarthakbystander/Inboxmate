"""CSRF protection.

Every session carries a random CSRF token stored server-side. All
state-changing endpoints require it, submitted either:
  * as a form field named ``_csrf`` (HTML forms / HTMX), or
  * in the ``X-CSRF-Token`` request header (JS fetch/delegates).
Tokens are compared with a constant-time comparison.
"""
from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, Request, status


def _matches(token_a: str | None, token_b: str | None) -> bool:
    return bool(token_a and token_b) and hmac.compare_digest(token_a, token_b)


def verify_csrf(session_csrf: str, supplied: str | None) -> bool:
    if not session_csrf:
        return False
    return _matches(session_csrf, supplied)


def require_valid_csrf(session_csrf: str, supplied_token: str | None) -> None:
    """Raise 403 when the supplied token does not match the session token."""
    if not verify_csrf(session_csrf, supplied_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token"
        )


def ensure_csrf_form(
    request: Request,
    session_csrf: str,
    form_csrf: str | None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    """Check CSRF from either the form field or the header (whichever is present)."""
    supplied = form_csrf or x_csrf_token
    if supplied:
        require_valid_csrf(session_csrf, supplied)
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def enforce_csrf_generic(request: Request, user: dict) -> None:
    """Verify CSRF from the X-CSRF-Token header (used by HTMX-driven routes).

    The app template injects the session CSRF token into a meta tag and a small
    script forwards it as the ``X-CSRF-Token`` header on every HTMX request.
    Also accepts a ``_csrf`` form field for robustness.
    """
    supplied = request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token")
    if not supplied:
        form = getattr(request, "_form", None)
        if form is not None:
            supplied = form.get("_csrf")
    require_valid_csrf(user.get("csrf_token", ""), supplied)