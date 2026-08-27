"""Application configuration.

All configuration is driven by environment variables. No secrets are
hardcoded here; production values are supplied through the environment
or a ``.env`` file loaded by the launcher.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional
    load_dotenv = None


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv()


def _as_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- General -----------------------------------------------------
    app_name: str = "InboxMate"
    secret_key: str = os.getenv("INBOXMATE_SECRET_KEY", "insecure-dev-key-change-me")
    base_url: str = os.getenv("INBOXMATE_BASE_URL", "http://localhost:8080")
    # Absolute path to the data directory (SQLite + encryption keys).
    data_dir: str = os.path.abspath(os.getenv("INBOXMATE_DATA_DIR", "./data"))

    # --- Database -----------------------------------------------------
    database_path: str = os.getenv(
        "INBOXMATE_DATABASE_PATH", ""
    )  # empty => derived from data_dir

    # --- Auth/session ---------------------------------------------------
    session_hours: int = int(os.getenv("INBOXMATE_SESSION_HOURS", "168"))  # 7 days
    login_rate_limit_per_minute: int = int(
        os.getenv("INBOXMATE_LOGIN_RATE_LIMIT", "10")
    )

    # --- Email ----------------------------------------------------------
    max_email_size_bytes: int = int(
        os.getenv("INBOXMATE_MAX_EMAIL_SIZE", str(3 * 1024 * 1024))
    )
    max_attachment_size_bytes: int = int(
        os.getenv("INBOXMATE_MAX_ATTACHMENT_SIZE", str(10 * 1024 * 1024))
    )
    inbox_page_size: int = int(os.getenv("INBOXMATE_PAGE_SIZE", "50"))
    imap_timeout_seconds: int = int(os.getenv("INBOXMATE_IMAP_TIMEOUT", "30"))

    # --- AI ---------------------------------------------------------------
    default_ai_provider: str = os.getenv("INBOXMATE_AI_PROVIDER", "none")
    default_ai_base_url: str = os.getenv("INBOXMATE_AI_BASE_URL", "")
    default_ai_api_key: str = os.getenv("INBOXMATE_AI_API_KEY", "")
    default_ai_model: str = os.getenv("INBOXMATE_AI_MODEL", "")
    default_ai_max_tokens: int = int(
        os.getenv("INBOXMATE_AI_MAX_TOKENS", "1000")
    )
    # Ollama default endpoint (no key needed).
    default_ollama_url: str = os.getenv("INBOXMATE_OLLAMA_URL", "http://localhost:11434")

    # --- Dev --------------------------------------------------------------
    debug: bool = _as_bool("INBOXMATE_DEBUG", False)

    @property
    def db_path(self) -> str:
        if self.database_path:
            return os.path.abspath(self.database_path)
        return os.path.join(self.data_dir, "inboxmate.db")


def get_settings() -> Settings:
    """Load settings once and cache on the module."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


_settings: Settings | None = None