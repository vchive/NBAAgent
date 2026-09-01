# Provider Adapter Contract

**Feature**: [spec.md](../spec.md)
**Design**: [lld.md](../lld.md)

## 1. Purpose and trust boundary

Provider adapters are the only modules allowed to access public Internet sports/news data. They
accept typed filters and a fixed configured allow-list; they never fetch a URL supplied by a user.
The Provider Gateway (including its cache) is unreachable until the Safety Gate returns `ALLOW`;
BLOCK/`OUT_OF_SCOPE` branches perform no adapter call or cache lookup/write. Raw responses stay
inside the adapter/normalizer boundary. The application receives canonical entities plus internal
evidence metadata.

## 2. Typed port operations

```text
search_games(GameFilters) -> ProviderResult[Game[]]
get_game_summary(game_id: string) -> ProviderResult[GameBundle]
get_play_by_play(game_id: string) -> ProviderResult[PlayByPlayBundle]
get_player_stats(StatsQuery) -> ProviderResult[StatLine[]]
get_team_stats(StatsQuery) -> ProviderResult[StatLine[]]
get_standings(SeasonLabel) -> ProviderResult[Standing[]]
get_history(HistoryQuery) -> ProviderResult[HistoryRecord[]]
search_news(NewsQuery) -> ProviderResult[NewsItem[]]
```

`GameFilters` contains a canonical half-open `DateRange`, season, team IDs and status;
`StatsQuery` contains subject, scope and optional season/game/series/date filters;
`HistoryQuery.season_range` is an inclusive `SeasonRange` (`start_inclusive` through
`end_inclusive`); `NewsQuery` contains a bounded subject and `DateRange`, not arbitrary URLs or
HTML instructions.

## 3. Result schema

```text
ProviderResult[T]
  data: T | null
  evidence: Evidence[]
  partial: boolean
  error: ProviderError | null
  retrieved_at_utc: Instant

ProviderError
  kind: TIMEOUT|RATE_LIMITED|AUTH|HTTP|INVALID_JSON|SCHEMA_MISMATCH|NOT_FOUND
  retryable: boolean
  safe_message: string
  retry_after_seconds: int | null
```

Every non-null fact must link to at least one `Evidence`. Evidence fields `source_ref`, `url`,
raw response and provider field names are internal-only. The normalizer preserves nulls and emits
schema warnings instead of inventing defaults. In particular, a missing play score/score value is
`null`, not zero; a missing PBP participant remains unknown.

For `PlayByPlayBundle`, `sequence_valid=true` is permitted only when every event has a non-null,
unique usable sequence; otherwise it is `false`. `provider_index` is a non-negative, unique
adapter-order tie-breaker, and consumers use the ordering and full-bundle final-period rules in
`data-model.md`/`lld.md`.

## 4. Baseline adapter mapping

The first implementation may use ESPN public Web API endpoints behind this contract. The exact
base URL and path templates live in configuration/code, never in user responses. Expected mapping:

| Capability | Required source fields | Canonical output |
|---|---|---|
| Schedule/score | event ID, date, status, competitors, scores, optional venue name/address | `Game` |
| Summary/box score | game header, teams, leaders, athlete stats | `GameBundle`, `StatLine` |
| Play-by-play | period, display clock, sequence, participants, scoring, scores | `PlayByPlayBundle` |
| Roster/profile | stable ID, display name, aliases, team | `Player`, `Team` |
| Season stats | subject, season, metric name/value/scope | `StatLine` |
| History/records | record type, subject, season, value | `HistoryRecord` |
| News/search | title, published time, subject, summary | `NewsItem` + evidence |

Adapters must send a descriptive User-Agent, enforce response-size and timeout limits, and honor
terms, robots and rate limits. Date queries should be split into bounded daily requests; month-wide
responses may be too large for a single request.

场馆映射读取 competition 级 venue 的名称及 city/state/country；任一地址分量缺失时保持
`null`，venue 名称本身缺失时整个 `Game.venue` 为 `null`。fixture evidence 必须保持
`source_class=FIXTURE`，不得在 fallback 后伪装为实时公开来源。

### Controlled web-search augmentation

An optional DuckDuckGo adapter may implement only `search_news(NewsQuery)`. It uses the fixed
`https://api.duckduckgo.com/` Instant Answer endpoint, accepts no URL from a request, returns at
most five cleaned candidates, and enforces a three-second timeout plus a bounded response body.
HTML/script/control characters, links and prompt-injection instructions are removed at the adapter
boundary. Its evidence uses `source_class=SEARCH`, medium/low trust and unknown freshness; the
result remains partial and cannot be the sole evidence for scores, standings, player statistics,
series arithmetic or PBP. A search error must not replace an otherwise valid NBA provider result.

## 5. Reliability policy

- Retry only idempotent GET requests; at most two exponential-backoff retries by default.
- Do not retry 400, 401, 403 or schema errors. Open a circuit after repeated timeout/5xx failures.
- A 429 returns `RATE_LIMITED` with a retry-after hint internally; the user sees a generic retry
  message.
- Primary and fallback sources are selected by capability, trust tier and freshness. Conflicting
  high-risk facts become `PARTIAL/UNVERIFIED`, never an arbitrary winner.
- Fixtures must represent success, empty, partial, timeout, 429, invalid JSON and conflicting data.

## 6. Data freshness defaults (configurable targets)

| Scope | Default TTL | Use |
|---|---:|---|
| Live score/schedule | 45 seconds | Current-game answers only while fresh |
| Box score | 300 seconds | Recent completed/live game |
| History/records | 86,400 seconds | Background facts; recheck latestness questions |

These are engineering defaults, not PDF requirements. An expired record cannot be presented as
current without a freshness warning and revalidation attempt.
