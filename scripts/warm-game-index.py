#!/usr/bin/env python3
"""Warm the persistent public game index from bounded schedule/detail pages."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.src.application.ports import RequestBudget  # noqa: E402
from apps.api.src.config import Settings  # noqa: E402
from apps.api.src.domain.models import (  # noqa: E402
    Evidence,
    Game,
    GameBundle,
    GameStatus,
    PlayByPlayBundle,
    SeasonLabel,
)
from apps.api.src.infrastructure.game_index import GameIndex  # noqa: E402
from apps.api.src.providers.hupu_adapter import (  # noqa: E402
    HUPU_TEAM_SLUGS,
    HupuAdapter,
)

_BEIJING = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class WarmResult:
    schedules_requested: int
    unique_games: int
    inserted: int
    updated: int
    unchanged: int
    details_loaded: int
    plays_loaded: int
    failures: int


def parse_season(value: str) -> SeasonLabel:
    raw = str(value or "").strip()
    if len(raw) != 7 or raw[4] != "-" or not raw[:4].isdigit() or not raw[5:].isdigit():
        raise argparse.ArgumentTypeError("赛季格式必须为 YYYY-YY，例如 2025-26")
    start = int(raw[:4])
    end = (start // 100) * 100 + int(raw[5:])
    if end != start + 1:
        raise argparse.ArgumentTypeError("赛季结束年份必须紧接开始年份")
    return SeasonLabel(start_year=start, end_year=end, label=f"{start:04d}-{end % 100:02d}")


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期格式必须为 YYYY-MM-DD") from exc


def resolve_team_slugs(value: str) -> list[str]:
    raw = [item.strip().lower() for item in str(value or "all").split(",") if item.strip()]
    if not raw or raw == ["all"]:
        return sorted(HUPU_TEAM_SLUGS)
    by_team_id = {team_id: slug for slug, team_id in HUPU_TEAM_SLUGS.items()}
    slugs: list[str] = []
    for item in raw:
        slug = item if item in HUPU_TEAM_SLUGS else by_team_id.get(item)
        if slug is None:
            raise argparse.ArgumentTypeError(f"未知球队标识：{item}")
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def _request_budget(timeout_seconds: float) -> RequestBudget:
    return RequestBudget(
        datetime.now(UTC) + timedelta(seconds=max(1.0, timeout_seconds)),
        max_provider_operations=1,
        max_retries_per_operation=0,
    )


def _inside(game: Game, start_day: date, end_day: date) -> bool:
    local_day = game.start_utc.astimezone(_BEIJING).date()
    return start_day <= local_day <= end_day


def infer_june_series(games: Iterable[Game]) -> dict[str, Game]:
    """Infer game numbers for repeated June matchups lacking phase metadata.

    The schedule source does not label playoff rounds.  Four or more games
    between the same two teams in a compact June window are the only records
    eligible for this Finals inference.  All other games retain null series
    metadata instead of being guessed.
    """

    values = {game.game_id: game for game in games}
    groups: dict[tuple[str, tuple[str, str]], list[Game]] = {}
    for game in values.values():
        local_day = game.start_utc.astimezone(_BEIJING).date()
        if local_day.month != 6:
            continue
        pair = tuple(sorted((game.home.canonical_id, game.away.canonical_id)))
        groups.setdefault((game.season.label, pair), []).append(game)
    for (season, pair), group in groups.items():
        ordered = sorted(group, key=lambda item: item.start_utc)
        finals = [item for item in ordered if item.status is GameStatus.FINAL]
        if len(finals) < 4:
            continue
        span = (ordered[-1].start_utc - ordered[0].start_utc).days
        if span > 28:
            continue
        wins = {team_id: 0 for team_id in pair}
        for game in finals:
            if None in (game.home_score, game.away_score):
                continue
            winner = (
                game.home.canonical_id
                if int(game.home_score) > int(game.away_score)
                else game.away.canonical_id
            )
            wins[winner] += 1
        series_finished = max(wins.values(), default=0) >= 4
        if series_finished:
            last_final = max(item.start_utc for item in finals)
            for game in ordered:
                if game.status is not GameStatus.FINAL and game.start_utc > last_final:
                    values.pop(game.game_id, None)
            ordered = [item for item in ordered if item.game_id in values]
        series_id = f"{season}-finals-{'-'.join(pair)}"
        for number, game in enumerate(ordered, start=1):
            values[game.game_id] = game.model_copy(
                update={"series_id": series_id, "series_game_number": number}
            )
    return values


def _game_evidence(game: Game, evidence: list[Evidence]) -> list[Evidence]:
    source_id = game.game_id.split(":", 1)[-1]
    matching = [item for item in evidence if item.evidence_id.endswith(f":{source_id}")]
    return matching or evidence[:1]


async def warm_index(
    *,
    adapter: HupuAdapter,
    index: GameIndex,
    season: SeasonLabel,
    team_slugs: list[str],
    start_day: date,
    end_day: date,
    details: bool,
    max_details: int = 2_500,
    as_of_day: date | None = None,
) -> WarmResult:
    effective_as_of = as_of_day or datetime.now(_BEIJING).date()
    games: dict[str, Game] = {}
    evidence_by_game: dict[str, list[Evidence]] = {}
    failures = 0
    for slug in team_slugs:
        result = await adapter.fetch_team_schedule(
            slug,
            season,
            _request_budget(adapter.timeout_seconds + 1),
        )
        if result.error is not None or not isinstance(result.data, list):
            failures += 1
            continue
        for game in result.data:
            if not isinstance(game, Game) or not _inside(game, start_day, end_day):
                continue
            local_day = game.start_utc.astimezone(_BEIJING).date()
            if game.status is not GameStatus.FINAL and local_day < effective_as_of:
                continue
            games[game.game_id] = game
            existing = evidence_by_game.setdefault(game.game_id, [])
            known = {item.evidence_id for item in existing}
            existing.extend(
                item
                for item in _game_evidence(game, result.evidence)
                if item.evidence_id not in known
            )

    games = infer_june_series(games.values())
    inserted = updated = unchanged = 0
    for game in sorted(games.values(), key=lambda item: item.start_utc):
        outcome = index.upsert_bundle(
            GameBundle(game=game),
            evidence_by_game.get(game.game_id, []),
            origin="public",
        )
        if outcome == "inserted":
            inserted += 1
        elif outcome == "updated":
            updated += 1
        elif outcome == "unchanged":
            unchanged += 1
        else:
            failures += 1

    details_loaded = 0
    plays_loaded = 0
    if details:
        detail_candidates = [
            game
            for game in sorted(games.values(), key=lambda item: item.start_utc)
            if game.status is GameStatus.FINAL
        ][: max(0, max_details)]
        for game in detail_candidates:
            existing = index.get_game_summary(game.game_id)
            bundle = existing.data if existing is not None else GameBundle(game=game)
            evidence = list(existing.evidence if existing is not None else [])

            # Resume a partial warm-up instead of downloading a box score that
            # is already in SQLite.  A PBP-only retry is common after upgrading
            # an older database whose detail rows predate event ingestion.
            if not bundle.stat_lines:
                result = await adapter.get_game_summary(
                    game.game_id,
                    _request_budget(adapter.timeout_seconds + 1),
                )
                if result.error is not None or not isinstance(result.data, GameBundle):
                    failures += 1
                else:
                    bundle = result.data
                    evidence.extend(result.evidence)
                    details_loaded += 1

            if bundle.plays is None or not bundle.plays.events:
                play_result = await adapter.get_play_by_play(
                    game.game_id,
                    _request_budget(adapter.timeout_seconds + 1),
                )
                if (
                    play_result.error is not None
                    or not isinstance(play_result.data, PlayByPlayBundle)
                    or not play_result.data.events
                ):
                    failures += 1
                else:
                    bundle = bundle.model_copy(update={"plays": play_result.data})
                    evidence.extend(play_result.evidence)
                    plays_loaded += 1

            detail_game = bundle.game.model_copy(
                update={
                    "series_id": game.series_id,
                    "series_game_number": game.series_game_number,
                }
            )
            deduped_evidence = list(
                {item.evidence_id: item for item in evidence}.values()
            ) or evidence_by_game.get(game.game_id, [])
            outcome = index.upsert_bundle(
                bundle.model_copy(update={"game": detail_game}),
                deduped_evidence,
                origin="public",
            )
            if outcome not in {"inserted", "updated", "unchanged"}:
                failures += 1

    return WarmResult(
        schedules_requested=len(team_slugs),
        unique_games=len(games),
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        details_loaded=details_loaded,
        plays_loaded=plays_loaded,
        failures=failures,
    )


async def _run(args: argparse.Namespace) -> WarmResult:
    settings = Settings.from_env()
    index = GameIndex(
        args.database or settings.game_index_db,
        max_documents=settings.game_index_max_documents,
        max_document_bytes=settings.game_index_max_document_bytes,
        busy_timeout_ms=settings.game_index_busy_timeout_ms,
    )
    if not index.available:
        raise RuntimeError("赛事索引不可用")
    adapter = HupuAdapter(
        timeout_seconds=settings.hupu_timeout_seconds,
        max_response_bytes=settings.hupu_max_response_bytes,
    )
    try:
        return await warm_index(
            adapter=adapter,
            index=index,
            season=args.season,
            team_slugs=args.teams,
            start_day=args.start,
            end_day=args.end,
            details=args.details,
            max_details=args.max_details,
        )
    finally:
        index.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="预热可检索的 NBA 赛事索引")
    parser.add_argument("--season", type=parse_season, default=parse_season("2025-26"))
    parser.add_argument("--teams", type=resolve_team_slugs, default=resolve_team_slugs("all"))
    parser.add_argument("--from", dest="start", type=parse_day)
    parser.add_argument("--to", dest="end", type=parse_day)
    parser.add_argument(
        "--details",
        action="store_true",
        help="补齐已结束比赛的详情、球员数据与文字逐回合",
    )
    parser.add_argument("--max-details", type=int, default=2_500)
    parser.add_argument("--database", help="覆盖 GAME_INDEX_DB，仅用于运维任务")
    args = parser.parse_args()
    if args.start is None:
        args.start = date(args.season.start_year, 9, 1)
    if args.end is None:
        args.end = date(args.season.end_year, 7, 1)
    if args.end < args.start:
        parser.error("结束日期不能早于开始日期")
    if not 0 <= args.max_details <= 5_000:
        parser.error("max-details 必须在 0 到 5000 之间")
    result = asyncio.run(_run(args))
    print(
        "完成："
        f"{result.schedules_requested} 个球队赛程，"
        f"{result.unique_games} 场唯一比赛，"
        f"新增 {result.inserted}、更新 {result.updated}、未变化 {result.unchanged}，"
        f"详情 {result.details_loaded}、逐回合 {result.plays_loaded}，"
        f"失败 {result.failures}。"
    )


if __name__ == "__main__":
    main()
