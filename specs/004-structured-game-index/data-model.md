# Data Model: Structured Game Index and Retrieval

## IndexedGame

| Field | Type | Rules |
|---|---|---|
| game_id | text | Primary canonical ID, max 128 characters |
| source_class | text | `PUBLIC_STRUCTURED`; demo values rejected |
| source_ref | text | Internal bounded source record identity |
| source_game_id | text | Provider identity, unique with source reference |
| season_label | text | Canonical `YYYY-YY` |
| start_utc | aware timestamp | Required |
| home_id / away_id | text | Canonical team IDs; must differ |
| home_name / away_name | text | Sanitized display names |
| home_score / away_score | integer nullable | Both required for final games |
| status | enum text | scheduled, live, final, postponed, unknown |
| series_id / series_game_number | nullable | Game number 1–20 |
| venue fields | nullable text | Stored only when present |
| coach fields | nullable text | Stored only when present |
| duration_seconds / attendance | nullable integer | Non-negative |
| evidence_state | enum text | verified or partial |
| data_as_of_utc / retrieved_at_utc | aware timestamps | Required retrieval metadata |
| completeness_score | integer | Deterministic non-decreasing update guard |
| content_fingerprint | text | SHA-256 canonical record hash |

Indexes cover season/time, each team/time, series/game number, and source identity.

## IndexedPlayerStat

Composite identity: `(game_id, player_id)`.

Fields include player/team canonical IDs and names, starter flag, position, minutes text/seconds when parseable, points, rebounds, assists, steals, blocks, turnovers, fouls, plus bounded JSON for additional numeric shooting metrics. Every row inherits the parent game's evidence and source identity. Re-import replaces a row only when its completeness is equal or better.

## IndexedPlay

Composite identity: `(game_id, provider_index)`. Fields mirror the existing canonical play model: optional sequence, period, clock remaining, event type, shooter/assister, shot type, points, score-after, and internal raw-text hash. No play row is synthesized from a terminal score marker.

## SourceDocument

| Field | Type | Rules |
|---|---|---|
| document_id | text | Stable hash/provider ID |
| title | text | Sanitized, max 500 characters |
| content | text | Sanitized and byte bounded |
| canonical_terms | text | Space-separated canonical team/player/season/game tokens |
| season_label / game_id | nullable | Optional exact constraints |
| entity_ids_json | JSON text | Deduplicated canonical IDs |
| source_ref | text | Internal provenance, never rendered directly |
| evidence_state | text | Always partial for web-search documents |
| published_utc / retrieved_at_utc | timestamps | Publication optional, retrieval required |
| content_fingerprint | text | Deduplication and update identity |

An external-content FTS5 table indexes `title`, `content`, and `canonical_terms`. Insert/update/delete triggers keep it synchronized. BM25 weights canonical terms and title more heavily than body content.

## Relationships

```text
IndexedGame 1 ─── * IndexedPlayerStat
IndexedGame 1 ─── * IndexedPlay
IndexedGame 1 ─── * SourceDocument (optional game_id)
Canonical entity IDs ─── SourceDocument.entity_ids_json/canonical_terms
```

## State and Update Rules

1. A public schedule row may enter as partial and later be enriched by a detail page.
2. A final game cannot be downgraded to scheduled/unknown or lose a known score.
3. A conflicting known final score is rejected unless an explicit authority policy is supplied; v1 has no automatic override.
4. New optional fields and additional stat lines increase completeness and may replace the stored projection.
5. Demo origin is rejected before any transaction begins.
6. Search results enter only SourceDocument and never produce an IndexedGame or IndexedPlayerStat.
7. Deletes cascade from a game to player stats and plays; source documents remain only when they have an independent source identity and their optional game link is cleared.
