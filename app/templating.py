"""Jinja2 template environment (shared by main and all route modules).

Kept in its own module to avoid the ``main <-> routes`` circular import.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))