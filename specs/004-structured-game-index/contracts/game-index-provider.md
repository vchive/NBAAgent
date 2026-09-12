# Contract: Game Index and Indexed Provider

## Storage operations

### `upsert_game(bundle, evidence, origin)`

- Accepts canonical `GameBundle` data and one or more public structured evidence records.
- Rejects origins other than `public` and rejects evidence without a bounded HTTP(S) public source.
- Runs atomically across game and supplied player/play rows.
- Returns `inserted`, `updated`, `unchanged`, or `rejected_conflict`.
- Never replaces a complete final score with a conflicting or less-complete record.

### `search_games(filters, limit)`

- Applies every supplied season, date, team, and status constraint using relational predicates.
- A two-team filter means both teams must participate, independent of home/away order.
- Orders newest first unless the caller requests a series projection.
- Returns canonical `Game` objects with public structured evidence.

### `search_documents(query, subject_refs, season, game_id, limit)`

- Builds canonical terms from recognized entities and season.
- Applies exact entity/season/game constraints when present.
- Uses a parameterized FTS expression over sanitized tokens and BM25 ranking.
- Returns at most the requested bounded limit and never returns raw HTML or internal URLs to public output.
- Degrades to an empty local result if FTS is unavailable; it does not fail the live provider call.

## Provider behavior

The indexed provider implements the existing `ProviderPort`:

- `search_games`: local relational hit first; otherwise delegate and persist accepted public games.
- `get_game_summary`: local complete hit first; a known Hupu game may be enriched once through the fixed-host detail adapter; otherwise delegate.
- `get_play_by_play`, `get_player_stats`, `get_team_stats`: return sufficient local typed rows when present, otherwise delegate.
- `search_news`: retrieve local BM25 documents, call the wrapped public search when budget permits, sanitize/deduplicate/merge results, and store new results only as partial documents.
- `search_web`: retrieve local BM25 documents, then call only the configured web-search adapter chain when a remote lookup is needed; it MUST NOT call the wrapped structured `search_news` provider first. Legacy injected search adapters may expose only `search_news` as a compatibility alias, but the production composition keeps ESPN/news separate from this path.
- `get_standings`, `get_history`: delegate unchanged in v1.

For every operation:

- Local SQLite reads consume no external-operation budget.
- External calls use the existing shared deadline and operation cap.
- SQLite errors fail open to the wrapped provider.
- Returned `ProviderResult` retains evidence and freshness; local records are not marked as gateway fallback.
- Runtime/search quota, authentication, rate-limit, and timeout failures retain their typed kind and
  retryability when a fallback is empty or also fails.
- If a fallback returns usable data after an earlier capability failure, the result carries that
  failure only as a request-scoped `capability_issue`; the issue is not evidence and does not turn
  the successful data into an error.
- Gateway/index caches MUST strip `capability_issues` before writing and on cache read, so a later
  request never inherits an old quota/authentication notice.
- The Agent tool bridge may project a request-scoped issue into internal tool-call metadata while
  keeping the usable observation completed. Public HTTP/SSE projection is provider-neutral: usable
  evidence produces an answer with a notice; no usable evidence produces a technical failure,
  never `no_data` or a generic clarification.

## Hupu adapter behavior

- Allowed host: `nba.hupu.com` only.
- Schedule URL shape: `/schedule/{known-team-slug}`.
- Detail URL shape: `/games/boxscore/{numeric-game-id}`.
- Text replay URL shape: `/games/playbyplay/{numeric-game-id}`; replay rows are
  persisted as typed `PlayEvent` records, including a bounded sanitized
  `action_text` projection when the source supplies one.
- Redirects are rejected; HTTPS is required; response status, byte limit, charset, and HTML structure are validated.
- Schedule rows provide canonical teams, Beijing start time, status, score, and source game ID.
- Detail rows may add period scores, duration, venue, attendance, coaches, and player stats; missing values remain null.
- Replay rows may add period/clock, event type, shooter, assister, shot type,
  points and score-after values. A missing participant or score is retained as
  null and never inferred from the surrounding row.
- Page text, links, scripts, and forum content outside the validated score/stat sections are ignored.

## Diagnostics

Readiness may report implementation-neutral fields:

- index status: ok/degraded/disabled
- indexed game/document counts
- last successful import time

The public UI must not show database, provider, table, host, tool, model, or prompt names.
