"""InboxMate -- a lightweight, self-hosted AI email assistant.

FastAPI application that wires the database, security helpers, templates
and route blueprints together.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database.db import init_db
from app.routes import auth, drafts, email, inbox
from app.routes import settings as settings_routes
from app.templating import templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("inboxmate")


def create_app(test_config: dict | None = None) -> FastAPI:
    settings = get_settings()
    init_db()

    app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    templates.env.globals.update(app_name=settings.app_name)

    # Register route blueprints.
    app.include_router(auth.router)
    app.include_router(inbox.router)
    app.include_router(email.router)
    app.include_router(drafts.router)
    app.include_router(settings_routes.router)

    @app.get("/")
    def homepage(request: Request):
        return templates.TemplateResponse(request, "home.html", {"app_name": settings.app_name})

    @app.get("/health")
    def health():
        return {"status": "ok", "app": settings.app_name}

    return app


app = create_app()