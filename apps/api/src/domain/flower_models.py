"""Backward-compatible import surface for flower domain models.

Keeping this tiny re-export module makes the feature easy to discover for
adapters that conventionally import ``*_models`` while the canonical
definitions remain in :mod:`apps.api.src.domain.flower`.
"""

from .flower import *  # noqa: F403

