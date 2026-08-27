"""Date-scoped scoreboard/highlights projection."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from apps.api.src.api.schemas import HighlightGame, HighlightsResponse
from apps.api.src.application.ports import RequestBudget
from apps.api.src.domain.models import EvidenceState, Game, GameFilters
from apps.api.src.domain.time_policy import (
    format_beijing,
    local_date_range,
    validate_timezone,
)


class FutureHighlightsDateError(ValueError):
    """Requested calendar date is later than today in the caller's timezone."""


class HighlightsProviderError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


class HighlightsService:
    def __init__(self, gateway: Any, *, clock: Any | None = None) -> None:
        self.gateway = gateway
        self.clock = clock

    def _now(self) -> datetime:
        if self.clock is None:
            return datetime.now(UTC)
        value = self.clock.now_utc() if hasattr(self.clock, "now_utc") else self.clock()
        return value.astimezone(UTC)

    async def for_date(
        self, day: date, *, timezone_name: str = "Asia/Shanghai"
    ) -> HighlightsResponse:
        zone = validate_timezone(timezone_name)
        timezone_name = zone.key
        local_today = self._now().astimezone(zone).date()
        if day > local_today:
            raise FutureHighlightsDateError("future highlight dates are not available")
        date_range = local_date_range(day, timezone_name)
        budget = RequestBudget(
            self._now().replace(microsecond=0) + timedelta(seconds=8),
            max_provider_operations=2,
            clock=self.clock,
        )
        result = await self.gateway.search_games(
            GameFilters(date_range=date_range),
            budget=budget,
        )
        if result.error is not None:
            kind = getattr(result.error.kind, "value", str(result.error.kind))
            mapping = {
                "TIMEOUT": ("UPSTREAM_TIMEOUT", "数据暂时不可用，请稍后重试。", True, 504),
                "RATE_LIMITED": (
                    "UPSTREAM_RATE_LIMITED",
                    "数据服务暂时繁忙，请稍后重试。",
                    True,
                    429,
                ),
                "AUTH": ("UPSTREAM_AUTH", "数据服务暂时不可用，请稍后再试。", False, 502),
            }
            code, message, retryable, status_code = mapping.get(
                kind,
                (
                    "INVALID_UPSTREAM_DATA",
                    "公开数据格式异常，暂时无法核验。",
                    False,
                    502,
                ),
            )
            raise HighlightsProviderError(code, message, retryable, status_code)
        games = result.data or []
        public_games = [self._public_game(game) for game in games if isinstance(game, Game)]
        evidence_state = (
            EvidenceState.NONE
            if not public_games
            else EvidenceState.PARTIAL
            if result.partial
            else EvidenceState.VERIFIED
        )
        as_of = format_beijing(result.retrieved_at_utc) if public_games else None
        # Pydantic wire schema accepts lowercase values; use its coercion to keep
        # the domain model uppercase internally.
        return HighlightsResponse(
            date=day.isoformat(),
            timezone=timezone_name,
            games=public_games,
            as_of_beijing=as_of,
            evidence_state=evidence_state.value.lower(),
        )

    @staticmethod
    def _public_game(game: Game) -> HighlightGame:
        return HighlightGame(
            game_id=game.game_id,
            start_utc=game.start_utc,
            home_name=game.home.display_name,
            home_abbreviation=next(
                (alias for alias in game.home.aliases if len(alias) <= 4 and alias.isascii()),
                None,
            ),
            away_name=game.away.display_name,
            away_abbreviation=next(
                (alias for alias in game.away.aliases if len(alias) <= 4 and alias.isascii()),
                None,
            ),
            status=game.status.value.lower(),
            home_score=game.home_score,
            away_score=game.away_score,
            series_game_number=game.series_game_number,
        )


__all__ = ["FutureHighlightsDateError", "HighlightsProviderError", "HighlightsService"]
