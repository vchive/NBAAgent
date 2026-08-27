"""Fact-backed analysis helper.

Analysis is intentionally rendered from canonical facts.  This module provides
an application seam for a future Hermes runtime without allowing it to perform
retrieval or arithmetic.
"""

from __future__ import annotations

from apps.api.src.application.template_composer import TemplateComposer


class AnalysisComposer:
    def __init__(self, composer: TemplateComposer | None = None) -> None:
        self.composer = composer or TemplateComposer()

    def compose(self, intent, facts, **kwargs):
        return self.composer.compose(intent, facts, **kwargs)


__all__ = ["AnalysisComposer"]
