"""Encryption at rest for sensitive secrets (IMAP/SMTP passwords, AI keys).

Uses Fernet (AES-128-CBC+HMAC) for simplicity. In production the key is
provided via ``INBOXMATE_ENCRYPTION_KEY``; the base64-encoded value stored
in ``data/secret.key`` is derived from it or auto-generated on first run.
"""
from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class EncryptionError(Exception):
    """Raised when a secret cannot be encrypted/decrypted."""


def _key_path() -> Path:
    return Path(get_settings().data_dir) / "secret.key"


def _derive_key(secret: str) -> bytes:
    return base64.urlsafe_b64encode(
        hashlib.sha256(secret.encode("utf-8")).digest()
    )


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    settings = get_settings()
    env_key = os.getenv("INBOXMATE_ENCRYPTION_KEY", "")
    if env_key:
        key = _derive_key(env_key)
    else:
        _key_path().parent.mkdir(parents=True, exist_ok=True)
        if _key_path().exists():
            key = base64.urlsafe_b64decode(_key_path().read_bytes().strip())
        else:
            key = Fernet.generate_key()
            _key_path().write_bytes(key)
            try:
                os.chmod(_key_path(), 0o600)
            except OSError:
                pass
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    if plaintext is None:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str | None) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionError("Unable to decrypt secret (wrong encryption key?)") from exc


# Small helper used by tests.
def reset_key_for_tests() -> None:
    _fernet.cache_clear()