"""Development/server entry point: ``python -m app`` or ``uvicorn app.main:app``.

Also starts the in-process background scheduler (periodic sync + session
cleanup) when run as a long-lived server.
"""
from __future__ import annotations

import os

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    host = os.getenv("INBOXMATE_HOST", "0.0.0.0")
    port = int(os.getenv("INBOXMATE_PORT", "8080"))
    uvicorn.run("app.main:app", host=host, port=port, workers=1)


if __name__ == "__main__":
    main()