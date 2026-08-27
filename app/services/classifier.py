"""Email classification.

Currently wraps the AI service's ``classify`` operation. Kept as a thin
standalone service so it can later gain offline/heuristic fallbacks
without touching the AI layer or routes.
"""
from __future__ import annotations

from app.services.ai import AIService


class Classifier:
    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service

    def classify(self, sender: str, subject: str, body: str) -> str:
        if not self.ai_service.is_configured():
            return "unclassified"
        try:
            return self.ai_service.classify(sender, subject, body)
        except Exception:
            return "unclassified"