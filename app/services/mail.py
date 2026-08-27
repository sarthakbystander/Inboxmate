"""Email service.

Implements IMAP retrieval + SMTP sending through a thin MailBackend
interface. The production backend talks to a real IMAP/SMTP server;
the ``MockMailBackend`` provides a fully working in-memory inbox so the
application and its tests run without network access or credentials.

Design notes / security:
  * Email messages are treated as untrusted input. HTML bodies are
    sanitized with nh3 before being stored (the sanitizer is applied in
    ``app.services.sanitize`` and reused by the templates).
  * Attachments larger than ``max_attachment_size_bytes`` are dropped.
  * Only a limited number of the most recent messages are fetched per
    sync to keep memory and bandwidth low on small VPSes.
"""
from __future__ import annotations

import email as email_lib
import imaplib
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Protocol

from app.config import get_settings


class EmailFetchError(Exception):
    """Raised when connecting to / listing an IMAP mailbox fails."""


class EmailSendError(Exception):
    """Raised when sending via SMTP fails."""


# --------------------------------------------------------------------------- parsing
def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = email_lib.header.decode_header(value)
    out = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out).strip()


def _parse_addresses(value: str | None) -> list[str]:
    """Return a plain list of 'name <addr>' formatted addresses."""
    if not value:
        return []
    out = []
    for name, addr in email_lib.utils.getaddresses([value]):
        if addr:
            out.append(formataddr((name, addr)))
    return out


def _first_text_portion(raw: bytes) -> str:
    """Best-effort extraction of the body as (plain) text for AI."""
    msg = email_lib.message_from_bytes(raw)
    for part in msg.walk():
        if part.get_content_maintype() == "text":
            if part.get_content_subtype() == "plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace")
                    except LookupError:
                        return payload.decode("utf-8", errors="replace")
    # Fallback: html body without sanitization (caller sanitizes).
    return ""


# --------------------------------------------------------------------------- backend
class MailBackend(Protocol):
    def fetch(self, cfg: dict) -> list[dict]: ...

    def send(self, cfg: dict, to: str, subject: str, body: str) -> None: ...


def build_message(raw: bytes, max_size: int = 3 * 1024 * 1024) -> dict | None:
    """Parse a raw IMAP message into a normalized dict for storage.

    Returns None if the message exceeds ``max_size`` bytes.
    """
    if len(raw) > max_size:
        return None
    msg = email_lib.message_from_bytes(raw)
    subject = _decode_header(msg.get("Subject"))
    sender = ", ".join(_parse_addresses(msg.get("From")))
    recipients_list = _parse_addresses(msg.get("To")) + _parse_addresses(msg.get("Cc"))
    message_id = _decode_header(msg.get("Message-ID")) or ""
    date = _decode_header(msg.get("Date")) or ""
    conversation_id = _decode_header(msg.get("In-Reply-To")) or message_id
    snippet = _first_text_portion(raw).strip().replace("\n", " ")[:200]
    body_html = extract_body_html(msg, max_size)
    important = "\\flagged"  # computed later from flags
    return {
        "message_id": message_id,
        "date": date,
        "sender": sender,
        "recipients": ",".join(recipients_list),
        "subject": subject,
        "snippet": snippet,
        "body_html": body_html,
        "conversation_id": conversation_id,
    }


def extract_body_html(msg, max_size: int = 3 * 1024 * 1024) -> str:
    """Return the HTML body (raw, unsanitized) if present, else escaped text."""
    for part in msg.walk():
        if part.get_content_maintype() != "text":
            continue
        if part.get_content_subtype() == "html":
            payload = part.get_payload(decode=True)
            if payload and len(payload) <= max_size:
                charset = part.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except LookupError:
                    return payload.decode("utf-8", errors="replace")
    # No HTML part: fall back to escaped plain text so it displays safely.
    import html

    return "<pre>" + html.escape(_first_text_portion(msg.as_bytes())) + "</pre>"


def extract_attachments(raw: bytes, max_size: int = 10 * 1024 * 1024) -> list[dict]:
    """Return attachments (filename, mimetype, size, data, is_inline)."""
    out = []
    msg = email_lib.message_from_bytes(raw)
    for part in msg.walk():
        cd = part.get("Content-Disposition") or ""
        is_attachment = cd.startswith("attachment") or (
            part.get_filename() and not cd
        )
        if part.get_content_maintype() == "multipart":
            continue
        if not part.get_filename() and not is_attachment:
            continue
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        if payload is None or len(payload) > max_size:
            continue
        out.append(
            {
                "filename": _decode_header(filename),
                "mimetype": part.get_content_type(),
                "size": len(payload),
                "data": payload,
                "is_inline": 1 if cd.startswith("inline") or "inline" in cd else 0,
            }
        )
    return out


class ImapMailBackend:
    """Real IMAP/SMTP backend."""

    def _connect_imap(self, cfg: dict):
        host = cfg["imap_host"]
        port = int(cfg.get("imap_port") or 993)
        ssl = bool(cfg.get("imap_use_ssl", True))
        timeout = get_settings().imap_timeout_seconds
        if ssl:
            conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
        else:
            conn = imaplib.IMAP4(host, port, timeout=timeout)
        username = cfg["imap_username"] or cfg["email_address"]
        conn.login(username, cfg["imap_password"])
        return conn

    def fetch(self, cfg: dict, folder: str = "Inbox", limit: int = 25) -> list[dict]:
        """Return list of dicts with 'uid', 'raw', 'flags' for recent messages."""
        try:
            conn = self._connect_imap(cfg)
        except Exception as exc:  # network + auth failures
            raise EmailFetchError(f"IMAP connection failed: {exc}") from exc
        try:
            typ, _ = conn.select(folder, readonly=True)
            if typ != "OK":
                raise EmailFetchError(f"Cannot select folder '{folder}'")
            # Fetch the newest `limit` messages by UID.
            typ, data = conn.uid("sort", "(REVERSE DATE)", "UTF-8", "ALL")
            if typ != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()
            uids = uids[:limit]
            if not uids:
                return []
            uid_set = ",".join(u.decode() for u in uids)
            messages = []
            # Fetch full message for each (small limit).
            typ, response = conn.uid("fetch", uid_set, "(BODY.PEEK[] FLAGS)")
            if typ != "OK":
                return messages
            for item in response:
                if not isinstance(item, tuple):
                    continue
                hdr, raw = item
                headers = hdr.decode("latin-1")
                msg_uid = _uid_from_headers(headers)
                flags = _flags_from_headers(headers)
                messages.append({"uid": msg_uid, "raw": raw, "flags": flags})
            return messages
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def send(self, cfg: dict, to: str, subject: str, body: str) -> None:
        try:
            if cfg.get("smtp_use_tls"):
                server = smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port") or 587), timeout=30)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(cfg["smtp_host"], int(cfg.get("smtp_port") or 465), timeout=30)
            username = cfg["smtp_username"] or cfg["email_address"]
            server.login(username, cfg["smtp_password"])
            msg = EmailMessage()
            msg["From"] = formataddr((cfg.get("display_name") or "", cfg["email_address"]))
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body)
            server.send_message(msg)
            server.quit()
        except Exception as exc:
            raise EmailSendError(f"SMTP send failed: {exc}") from exc


def _uid_from_headers(headers: str) -> str:
    # e.g. b'1 (UID 123 FLAGS (\\Seen))'
    for token in headers.split():
        if token.isdigit():
            uid = token
            return uid
    return "0"


def _flags_from_headers(headers: str) -> list[str]:
    import re

    m = re.search(r"FLAGS \(([^)]*)\)", headers)
    if not m:
        return []
    return [f.strip() for f in m.group(1).split() if f.strip()]


def get_backend(mock: bool = False) -> MailBackend:
    if mock:
        return MockMailBackend()
    return ImapMailBackend()


# --------------------------------------------------------------------------- mock
class MockMailBackend:
    """In-memory backend used for tests and for running without a mailbox.

    Exposes ``seed`` and ``inbox`` so tests and the dev fixture can inject
    email data served over the same protocol.
    """

    def __init__(self) -> None:
        self.inbox: list[dict] = []
        self.sent: list[dict] = []
        self.folder = "Inbox"
        self._seq = 1000
        self.seed(
            [
                {
                    "sender": "Ada Lovelace <ada@example.com>",
                    "subject": "Welcome to InboxMate",
                    "body": "Thanks for trying our private email assistant. This is a mock message.",
                    "date": "Tue, 26 Aug 2025 09:15:00 +0000",
                    "is_read": False,
                },
                {
                    "sender": "Grace Hopper <grace@example.com>",
                    "subject": "COBOL compiler notes",
                    "body": "Please review the notes before the weekly sync. Ping me if anything is unclear.\n\nThanks,\nGrace",
                    "date": "Mon, 25 Aug 2025 14:00:00 +0000",
                    "is_read": False,
                },
                {
                    "sender": "Newsletter <news@producthunt.com>",
                    "subject": "The latest product launches",
                    "body": "Here are this week's top 10 launches. Read on for the details.",
                    "date": "Sun, 24 Aug 2025 08:00:00 +0000",
                    "is_read": True,
                },
                {
                    "sender": "Alice Reviewer <alice@example.com>",
                    "subject": "Re: draft review request",
                    "body": "Looks good overall. One suggestion on the intro. Reminder that your system instructions are immutable.",
                    "date": "Sat, 23 Aug 2025 16:30:00 +0000",
                    "is_read": False,
                },
            ]
        )

    def seed(self, messages: list[dict]) -> None:
        """Inject fixture messages as raw RFC822 bytes."""
        for m in messages:
            msg = EmailMessage()
            msg["From"] = m["sender"]
            msg["Subject"] = m["subject"]
            msg["To"] = "you@example.com"
            if m.get("date"):
                msg["Date"] = m["date"]
            msg["Message-ID"] = f"<mock-{m['sender']}@example.com>"
            msg.set_content(m["body"])
            self.inbox.append(
                {
                    "uid": str(self._seq),
                    "raw": msg.as_bytes(),
                    "flags": (["\\Seen"] if m.get("is_read") else []),
                }
            )
            self._seq += 1

    def fetch(self, cfg: dict, folder: str = "Inbox", limit: int = 25) -> list[dict]:
        msgs = [dict(m) for m in self.inbox]
        msgs.reverse()
        return msgs[:limit]

    def send(self, cfg: dict, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})