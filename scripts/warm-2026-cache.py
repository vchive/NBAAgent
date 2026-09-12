#!/usr/bin/env python3
"""预热 2026 年 NBA 结构化比赛缓存。

该任务是低优先级离线作业，不在用户请求线程中运行。它按 7 天窗口从
允许列表中的公开比赛接口拉取赛程/赛果，把每个北京时间日期的安全投影
写入 SQLite；可选的 ``--details`` 会继续补齐每场比赛的 box score、教练、
场馆和逐回合详情。重复执行是幂等的，缓存层会拒绝低完整度或冲突比分。
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import date, timedelta

from apps.api.src.api.schemas import HighlightGame, HighlightsResponse
from apps.api.src.application.highlights import HighlightsService
from apps.api.src.config import Settings
from apps.api.src.infrastructure.cache import InMemoryTTLCache
from apps.api.src.infrastructure.highlights_cache import SQLiteHighlightsCache, stable_cache_key
from apps.api.src.providers.espn_adapter import ESPNAdapter
from apps.api.src.providers.gateway import ProviderGateway


def _parse_day(value: str) -> date:
    return date.fromisoformat(value)


def _chunks(start: date, end: date, size: int = 7):
    current = start
    while current <= end:
        chunk_end = min(end, current + timedelta(days=size - 1))
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


async def warm(start_day: date, end_day: date, *, details: bool) -> tuple[int, int, int]:
    settings = Settings.from_env()
    cache = SQLiteHighlightsCache(
        settings.highlights_cache_db,
        max_entries=settings.highlights_cache_max_entries,
        max_payload_bytes=settings.highlights_cache_max_payload_bytes,
        lease_seconds=settings.highlights_cache_lease_seconds,
        busy_timeout_ms=settings.highlights_cache_busy_timeout_ms,
    )
    if not cache.available:
        raise RuntimeError("SQLite highlights cache is unavailable")
    adapter = ESPNAdapter(
        base_url=settings.espn_base_url,
        timeout_seconds=max(8.0, settings.provider_timeout_seconds),
        max_response_bytes=settings.provider_max_response_bytes,
        allowed_hosts=settings.espn_allowed_hosts,
    )
    gateway = ProviderGateway(
        adapter,
        cache=InMemoryTTLCache(max_entries=256),
        max_retries=0,
    )
    service = HighlightsService(gateway)
    game_count = detail_count = day_count = 0
    try:
        for chunk_start, chunk_end in _chunks(start_day, end_day):
            # Seven daily scoreboard calls fit comfortably in one bounded
            # service request and avoid the 93-day public HTTP route limit.
            response = await service.for_range(
                chunk_start,
                chunk_end,
                timezone_name="Asia/Shanghai",
            )
            # Use an explicit ZoneInfo conversion rather than host timezone.
            from zoneinfo import ZoneInfo

            beijing = ZoneInfo("Asia/Shanghai")
            grouped: dict[date, list[HighlightGame]] = defaultdict(list)
            for game in response.games:
                grouped[game.start_utc.astimezone(beijing).date()].append(game)
            for day in (
                chunk_start + timedelta(days=i) for i in range((chunk_end - chunk_start).days + 1)
            ):
                projection = HighlightsResponse(
                    date=day.isoformat(),
                    timezone="Asia/Shanghai",
                    games=grouped.get(day, []),
                    as_of_beijing=response.as_of_beijing,
                    evidence_state=response.evidence_state,
                    data_origin="public",
                )
                key = stable_cache_key(
                    "date",
                    "public",
                    "Asia/Shanghai",
                    day.isoformat(),
                )
                if cache.set(
                    key,
                    "date",
                    projection,
                    ttl_seconds=(
                        settings.highlights_cache_detail_ttl_seconds
                        if day < date.today()
                        else settings.highlights_cache_live_ttl_seconds
                    ),
                ):
                    day_count += 1
            game_count += len(response.games)
            print(f"赛程 {chunk_start}..{chunk_end}: {len(response.games)} 场", flush=True)
            if details:
                for game in response.games:
                    if game.status != "final":
                        continue
                    try:
                        detail = await service.detail(game.game_id, timezone_name="Asia/Shanghai")
                    except Exception as exc:  # one malformed game must not abort the batch
                        print(f"详情跳过 {game.game_id}: {type(exc).__name__}", flush=True)
                        continue
                    detail_key = stable_cache_key(
                        "detail",
                        "public",
                        "Asia/Shanghai",
                        game.game_id,
                    )
                    if cache.set(
                        detail_key,
                        "detail",
                        detail,
                        ttl_seconds=settings.highlights_cache_detail_ttl_seconds,
                    ):
                        detail_count += 1
        return game_count, day_count, detail_count
    finally:
        cache.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="预热 2026 NBA 比赛 SQLite 缓存")
    parser.add_argument("--from", dest="start", default="2026-01-01", help="开始日期 YYYY-MM-DD")
    parser.add_argument(
        "--to",
        dest="end",
        default=None,
        help="结束日期 YYYY-MM-DD（默认当前日期；未来赛程不会请求）",
    )
    parser.add_argument("--details", action="store_true", help="同时补齐每场已结束比赛详情")
    args = parser.parse_args()
    start_day = _parse_day(args.start)
    end_day = _parse_day(args.end) if args.end else date.today()
    if end_day < start_day:
        parser.error("结束日期不能早于开始日期")
    games, days, details = asyncio.run(warm(start_day, end_day, details=args.details))
    print(f"完成：{days} 个日期，{games} 场比赛，{details} 场详情写入 SQLite。")


if __name__ == "__main__":
    main()
