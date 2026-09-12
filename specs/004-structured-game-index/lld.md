# Low-Level Design: Structured Game Index

## SQLite implementation

`GameIndex` owns a thread-safe SQLite connection with WAL, foreign keys, busy timeout, schema version metadata, normalized relational tables, and FTS5 triggers. All SQL predicates and FTS expressions are parameterized. Public methods catch storage errors and return safe miss/rejection values.

`upsert_bundle` canonicalizes Pydantic objects to stable JSON, computes completeness and SHA-256 fingerprints, validates public origin, checks final-score conflicts, and commits the game plus supplied stat/play rows in one transaction.

## Entity term generation

Recognized aliases map to stable lowercase IDs from the application parser. A query for `2026尼克斯-马刺` becomes a structured season filter `2025-26`, team filters `nyk` and `sas`, and FTS terms such as `nyk`, `sas`, and `season_2025_26`. Only `[A-Za-z0-9_:-]` canonical tokens enter the generated FTS expression; raw user syntax never becomes MATCH SQL.

## BM25 query

Documents use external-content FTS columns `(title, content, canonical_terms)`. Candidate SQL joins the content table, applies exact season/game/entity membership predicates, evaluates an OR expression over bounded canonical/text tokens, orders by `bm25(source_documents_fts, 3.0, 1.0, 6.0)`, and limits results. Exact canonical entity filters remain mandatory even when a high-scoring unrelated document exists.

## Provider composition

The live provider retains structured public data plus managed web search. `IndexedProvider` wraps that stack before `ProviderGateway`; the deterministic fixture profile is composed separately and is not an error fallback for live/hybrid queries. This order prevents fixture writes or public-answer substitution while making local public hits available to both regular queries and full-intelligence tool calls. The wrapper exposes two distinct narrative operations: `search_news` may combine structured sports news with web candidates, while `search_web` reads BM25 first and then calls only the managed web-search adapter. A pure `nba_search` therefore cannot burn an operation on an unrelated structured-news request.

## HTML adapter

`HupuAdapter` uses HTTPX with a fixed base URL and no redirects. A small standard-library HTML parser captures only schedule rows, scoreboard metadata, period tables, player-stat tables, and the bounded text-replay table at `/games/playbyplay/{numeric-game-id}`. Replay rows are projected to typed `PlayEvent` values (period, clock, away/home score, event type, explicit shooter/assist and shot type); the source description is retained as sanitized `action_text` (maximum 500 characters) so details such as an explicitly stated corner three or layup are not lost. HTML, control characters and implementation names are removed/rejected, and missing fields remain null. Known team slugs map to canonical IDs; numeric game IDs are validated before URL creation. Times on schedule pages are interpreted as Asia/Shanghai and converted to UTC.

## Importer

`warm-game-index.py` accepts a bounded season/date interval, known team slugs, optional detail fetch, and configured SQLite path. It de-duplicates by source game ID, upserts records, prints counts only, and never prints source payloads or credentials. Re-running is idempotent.

## Answer synthesis

The Agent observation receives a compact evidence digest: a structured-match summary followed by at most three ranked document facts. The system prompt requires a direct conclusion and two to four supporting points. If the model reproduces raw result labels, a deterministic compact synthesis uses the highest-ranked evidence rather than displaying a title/excerpt list.

Evidence scope remains attached to the wording contract. Series-level honors such as Finals MVP may be stated only as series outcomes, never as awards earned "in this game"; valid temporal or basis wording such as receiving the award after the game or earning it partly through that game's performance is preserved. A single-game recap may use game-specific score, player line, period or play evidence; season, postseason and series averages and unasked historical asides are omitted unless the user explicitly requests broader context, so they cannot be presented as causal proof for one result. Clause-level pruning keeps a supported score or player line when an adjacent award, aggregate, or historical-aside clause is out of scope.

Search-result titles are ranking metadata and never enter the numeric, proper-name, event, or causal fact-authority projection. Only bounded summary bodies may support a partial narrative claim. Sentence-level causal grounding requires affirmative support whose acting team and explicit game number/date match the current game; negated statements, opponent actions, and events from another game are excluded. Search-only tactical details are projected as analysis rather than verified facts.

If generation stops at a section heading or emits a tool-budget/termination status after
the observations have completed, an application-owned truncation detector discards that
prose and rebuilds the answer from the typed game observation plus a bounded document
synthesis. The output guard rejects the internal status even if it appears in a nested
block, so partial evidence remains explicitly labelled and cannot be presented as a
complete model response.

## Browser transport isolation

The browser records the data mode only after a successful health response. `live` and
`hybrid` modes keep network exceptions on the failed-envelope path, preserve the user's
message and idempotency key, and expose a retry button that resubmits to the configured
chat endpoint. They never call the local fixture composer. The fixture composer is
reachable only when health explicitly reports `fixture` or a standalone static page sets
`window.COURTSIDE_RUNTIME_MODE = "fixture"` before application bootstrap.
