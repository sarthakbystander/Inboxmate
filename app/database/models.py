"""Thin data-access helpers for the InboxMate relational model.

Kept deliberately small; all SQL is parameterized to avoid injection.
The fixture/mock mail implementations write through these functions.
"""
from __future__ import annotations

from app.database.db import get_connection


# --------------------------------------------------------------------------- users
def create_user(email: str, password_hash: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO users(email, password_hash) VALUES (?, ?)",
        (email, password_hash),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_user_by_email(email: str) -> dict | None:
    row = get_connection().execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    row = get_connection().execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------- sessions
def create_session(session_id: str, user_id: int, csrf_token: str, expires_at: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO sessions(id, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
        (session_id, user_id, csrf_token, expires_at),
    )
    conn.commit()


def get_session(session_id: str) -> dict | None:
    row = get_connection().execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def delete_session(session_id: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


def delete_expired_sessions() -> None:
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
    conn.commit()


# --------------------------------------------------------------------------- settings
def get_settings(user_id: int) -> dict:
    row = get_connection().execute(
        "SELECT * FROM settings WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row:
        return dict(row)
    conn = get_connection()
    conn.execute("INSERT INTO settings(user_id) VALUES (?)", (user_id,))
    conn.commit()
    return get_settings(user_id)


def save_setting(user_id: int, key: str, value) -> None:
    conn = get_connection()
    conn.execute(
        f"UPDATE settings SET {key} = ?, updated_at = datetime('now') WHERE user_id = ?",
        (value, user_id),
    )
    conn.commit()


def save_settings(user_id: int, updates: dict[str, object]) -> None:
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    conn = get_connection()
    conn.execute(
        f"UPDATE settings SET {cols}, updated_at = datetime('now') WHERE user_id = ?",
        (*updates.values(), user_id),
    )
    conn.commit()


# --------------------------------------------------------------------------- folders
def upsert_folder(user_id: int, name: str) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO folders(user_id, name) VALUES (?, ?) "
        "ON CONFLICT(user_id, name) DO UPDATE SET name = excluded.name",
        (user_id, name),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM folders WHERE user_id = ? AND name = ?", (user_id, name)
    ).fetchone()
    return int(row["id"])


# --------------------------------------------------------------------------- emails
def upsert_email(user_id: int, folder_id: int | None, uid: str, fields: dict) -> int:
    """Insert (or update) an email keyed by (user_id, folder_id, uid)."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO emails
             (user_id, folder_id, uid, message_id, date, sender, recipients,
              subject, snippet, body_html, is_read, is_important, classification,
              conversation_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, folder_id, uid) DO UPDATE SET
             message_id = excluded.message_id,
             date = excluded.date,
             sender = excluded.sender,
             recipients = excluded.recipients,
             subject = excluded.subject,
             snippet = excluded.snippet,
             body_html = excluded.body_html,
             conversation_id = excluded.conversation_id""",
        (
            user_id, folder_id, uid,
            fields.get("message_id", ""),
            fields.get("date", ""),
            fields.get("sender", ""),
            fields.get("recipients", ""),
            fields.get("subject", ""),
            fields.get("snippet", ""),
            fields.get("body_html", ""),
            int(fields.get("is_read", 0)),
            int(fields.get("is_important", 0)),
            fields.get("classification", "unclassified"),
            fields.get("conversation_id", ""),
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM emails WHERE user_id = ? AND folder_id = ? AND uid = ?",
        (user_id, folder_id, uid),
    ).fetchone()
    return int(row["id"]) if row else 0


def get_email(email_id: int, user_id: int) -> dict | None:
    row = get_connection().execute(
        "SELECT * FROM emails WHERE id = ? AND user_id = ?", (email_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def list_emails(
    user_id: int,
    folder: str = "Inbox",
    query: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return (rows, total) for a folder/search view, newest first."""
    conn = get_connection()
    where = "e.user_id = ?"
    params: list = [user_id]
    if not query:
        where += " AND f.name = ?"
        params.append(folder)
    else:
        like = f"%{query}%"
        where += (
            " AND (e.subject LIKE ? OR e.sender LIKE ? OR e.snippet LIKE ? "
            "OR e.body_html LIKE ?)"
        )
        params.extend([like, like, like, like])
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM emails e JOIN folders f ON f.id = e.folder_id "
        f"WHERE {where}",
        params,
    ).fetchone()["n"]
    rows = conn.execute(
        f"""SELECT e.id, e.uid, e.date, e.sender, e.subject, e.snippet,
                   e.is_read, e.is_important, e.classification,
                   f.name AS folder
            FROM emails e JOIN folders f ON f.id = e.folder_id
            WHERE {where}
            ORDER BY datetime(e.date) DESC, e.id DESC
            LIMIT ? OFFSET ?""",
        (*params, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows], int(total)


def mark_read(email_id: int, user_id: int, is_read: int = 1) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE emails SET is_read = ? WHERE id = ? AND user_id = ?",
        (is_read, email_id, user_id),
    )
    conn.commit()


def set_important(email_id: int, user_id: int, important: int = 1) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE emails SET is_important = ? WHERE id = ? AND user_id = ?",
        (important, email_id, user_id),
    )
    conn.commit()


def set_classification(email_id: int, user_id: int, classification: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE emails SET classification = ? WHERE id = ? AND user_id = ?",
        (classification, email_id, user_id),
    )
    conn.commit()


def get_important_emails(user_id: int, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    conn = get_connection()
    where = "e.user_id = ? AND e.is_important = 1"
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM emails e WHERE {where}", (user_id,)
    ).fetchone()["n"]
    rows = conn.execute(
        f"""SELECT e.id, e.uid, e.date, e.sender, e.subject, e.snippet,
                   e.is_read, e.is_important, e.classification,
                   f.name AS folder
            FROM emails e JOIN folders f ON f.id = e.folder_id
            WHERE {where}
            ORDER BY datetime(e.date) DESC, e.id DESC
            LIMIT ? OFFSET ?""",
        (user_id, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows], int(total)


def count_unread(user_id: int) -> int:
    row = get_connection().execute(
        """SELECT COUNT(*) AS n FROM emails e
           JOIN folders f ON f.id = e.folder_id
           WHERE e.user_id = ? AND f.name = 'Inbox' AND e.is_read = 0""",
        (user_id,),
    ).fetchone()
    return int(row["n"])


# --------------------------------------------------------------------------- attachments
def add_attachment(user_id: int, email_id: int, filename: str, mimetype: str, size: int, data: bytes, is_inline: int = 0) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO attachments(user_id, email_id, filename, mimetype, size, data, is_inline) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, email_id, filename, mimetype, size, data, is_inline),
    )
    conn.commit()


def get_attachment(attach_id: int, user_id: int) -> dict | None:
    row = get_connection().execute(
        "SELECT * FROM attachments WHERE id = ? AND user_id = ?",
        (attach_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def list_attachments(email_id: int, user_id: int) -> list[dict]:
    rows = get_connection().execute(
        "SELECT id, filename, mimetype, size, is_inline FROM attachments "
        "WHERE email_id = ? AND user_id = ?",
        (email_id, user_id),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- drafts
def create_draft(
    user_id: int,
    email_id: int | None,
    to_address: str,
    subject: str,
    body: str,
    is_ai_generated: int = 0,
) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO drafts(user_id, email_id, to_address, subject, body, is_ai_generated) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, email_id, to_address, subject, body, is_ai_generated),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_draft(draft_id: int, user_id: int) -> dict | None:
    row = get_connection().execute(
        "SELECT * FROM drafts WHERE id = ? AND user_id = ?", (draft_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def update_draft(draft_id: int, user_id: int, body: str | None = None, subject: str | None = None, to_address: str | None = None) -> None:
    conn = get_connection()
    sets, params = [], []
    if body is not None:
        sets.append("body = ?")
        params.append(body)
    if subject is not None:
        sets.append("subject = ?")
        params.append(subject)
    if to_address is not None:
        sets.append("to_address = ?")
        params.append(to_address)
    if sets:
        params.extend([draft_id, user_id])
        conn.execute(
            f"UPDATE drafts SET {', '.join(sets)}, updated_at = datetime('now') "
            "WHERE id = ? AND user_id = ?",
            params,
        )
        conn.commit()


def list_drafts(user_id: int) -> list[dict]:
    rows = get_connection().execute(
        "SELECT d.id, d.email_id, d.to_address, d.subject, d.body, d.is_ai_generated, "
        "d.updated_at FROM drafts d WHERE d.user_id = ? ORDER BY d.updated_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_draft(draft_id: int, user_id: int) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM drafts WHERE id = ? AND user_id = ?", (draft_id, user_id))
    conn.commit()


# --------------------------------------------------------------------------- audit
def log_audit(user_id: int | None, action: str, detail: str = "", ip: str = "") -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO audit_log(user_id, action, detail, ip) VALUES (?, ?, ?, ?)",
        (user_id, action, detail, ip),
    )
    conn.commit()


def recent_audit(user_id: int, limit: int = 50) -> list[dict]:
    rows = get_connection().execute(
        "SELECT id, action, detail, created_at FROM audit_log "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]