"""Sanitization and validation helpers.

HTML email bodies are untrusted and must be cleaned before being stored
or rendered. nh3 (Rust ammonia) strips active content (scripts, event
handlers, javascript: URLs) and enforces a safe allowlist of tags.
"""
from __future__ import annotations

import re

import nh3

_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "del", "div", "em", "h1",
    "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre",
    "s", "span", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
}

_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "name"},
    "img": {"src", "alt", "width", "height", "title"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align"},
    "*": {"class", "dir", "lang", "title"},
}

_URL_SCHEMES = {"http", "https", "mailto", "ftp", "tel"}


def sanitize_html(html: str | None) -> str:
    """Return a safe, display-safe version of untrusted email HTML."""
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )


# --------------------------------------------------------------------------- validation
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(address: str) -> bool:
    return bool(_EMAIL_RE.match(address.strip()))


def safe_filename(filename: str | None) -> str:
    """Sanitize an uploaded/attachment filename for display and storage."""
    if not filename:
        return "file"
    name = str(filename).replace("\\", "/").split("/")[-1].strip()
    # Strip path separators and control chars; keep it short.
    name = "".join(c for c in name if c.isprintable()).strip()
    return (name or "file")[:120]


def clean_subject(subject: str | None, max_len: int = 200) -> str:
    if not subject:
        return "(no subject)"
    return "".join(c for c in subject.replace("\x00", "") if c.isprintable())[:max_len]