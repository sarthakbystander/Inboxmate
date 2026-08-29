"""Shared helpers to build services from a user's settings row."""
from __future__ import annotations


def get_mail_config(settings_row: dict, decrypted: dict) -> dict:
    """Assemble the plaintext mail config used by mail backends."""
    return {
        "imap_host": settings_row.get("imap_host", ""),
        "imap_port": settings_row.get("imap_port"),
        "imap_use_ssl": bool(settings_row.get("imap_use_ssl", 1)),
        "imap_username": decrypted.get("imap_username", ""),
        "imap_password": decrypted.get("imap_password", ""),
        "smtp_host": settings_row.get("smtp_host", ""),
        "smtp_port": settings_row.get("smtp_port"),
        "smtp_use_tls": bool(settings_row.get("smtp_use_tls", 0)),
        "smtp_username": decrypted.get("smtp_username", ""),
        "smtp_password": decrypted.get("smtp_password", ""),
        "email_address": settings_row.get("email_address", ""),
        "display_name": settings_row.get("display_name", ""),
    }


def decrypt_settings(settings_row: dict) -> dict:
    """Decrypt all encrypted credential fields for this user's settings."""
    from app.security import encryption

    return {
        "imap_username": encryption.decrypt(settings_row.get("imap_username_enc", "")),
        "imap_password": encryption.decrypt(settings_row.get("imap_password_enc", "")),
        "smtp_username": encryption.decrypt(settings_row.get("smtp_username_enc", "")),
        "smtp_password": encryption.decrypt(settings_row.get("smtp_password_enc", "")),
        "ai_api_key": encryption.decrypt(settings_row.get("ai_api_key_enc", "")),
    }