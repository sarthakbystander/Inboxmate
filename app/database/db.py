"""SQLite connection management for InboxMate.

Uses a single SQLite database with WAL mode and foreign keys enabled.
The schema is created idempotently on startup.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_LOCAL = threading.local()


def _connect(database: str) -> sqlite3.Connection:
    conn = sqlite3.connect(database, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def get_connection() -> sqlite3.Connection:
    """Return a connection bound to the current thread/context."""
    conn = getattr(_LOCAL, "conn", None)
    if conn is None:
        from app.config import get_settings

        # Read the database path fresh from the environment so tests can
        # swap to a throwaway database between cases.
        env_path = os.getenv("INBOXMATE_DATABASE_PATH", "")
        db_path = env_path or get_settings().db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(db_path)
        setattr(_LOCAL, "conn", conn)
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    imap_host TEXT NOT NULL DEFAULT '',
    imap_port INTEGER,
    smtp_host TEXT NOT NULL DEFAULT '',
    smtp_port INTEGER,
    email_address TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    smtp_use_tls INTEGER NOT NULL DEFAULT 0,
    imap_use_ssl INTEGER NOT NULL DEFAULT 1,
    -- Encrypted credential material
    imap_username_enc TEXT,
    imap_password_enc TEXT,
    smtp_username_enc TEXT,
    smtp_password_enc TEXT,
    -- AI provider block
    ai_provider TEXT NOT NULL DEFAULT 'none',
    ai_base_url TEXT NOT NULL DEFAULT '',
    ai_api_key_enc TEXT,
    ai_model TEXT NOT NULL DEFAULT '',
    ai_max_tokens INTEGER NOT NULL DEFAULT 1000,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    folder_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    uid TEXT NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    recipients TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    body_html TEXT NOT NULL DEFAULT '',
    is_read INTEGER NOT NULL DEFAULT 0,
    is_important INTEGER NOT NULL DEFAULT 0,
    classification TEXT NOT NULL DEFAULT 'unclassified',
    conversation_id TEXT NOT NULL DEFAULT '',
    UNIQUE(user_id, folder_id, uid)
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email_id INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mimetype TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    data BLOB NOT NULL,
    is_inline INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_attachments_email ON attachments(email_id);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email_id INTEGER REFERENCES emails(id) ON DELETE SET NULL,
    to_address TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    is_ai_generated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_drafts_user ON drafts(user_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
"""


def init_db() -> None:
    """Create tables if they don't exist and run migrations."""
    get_connection().executescript(SCHEMA)
    get_connection().commit()
    _migrate()


def _migrate() -> None:
    """Idempotent additive migrations for older databases."""
    conn = get_connection()
    columns = _table_columns(conn, "settings")
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE settings ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'))")
        conn.commit()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    except sqlite3.Error:
        return set()
    return {row["name"] for row in rows}