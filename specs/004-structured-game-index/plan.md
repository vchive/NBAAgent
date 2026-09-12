# Implementation Plan: Structured Game Index and Retrieval

**Branch**: `004-structured-game-index` | **Date**: 2026-09-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-structured-game-index/spec.md`

## Summary

Add a persistent, queryable NBA evidence store beside the existing exact response cache. Canonical games and player lines use relational filters; public narrative documents use SQLite FTS5 BM25 after entity/season constraints. A provider wrapper reads the index before live sources, persists safe public results, enriches known games on demand from an allow-listed Hupu adapter, and never admits demo snapshots into the public index. Pure Agent web searches use a dedicated `search_web` operation so the structured sports-news endpoint is not called as a prerequisite. The chat boundary carries verified series candidates through natural recommendations and follow-ups, preserves intelligent synthesis for open questions, and uses scoped deterministic recovery only after a runtime or output failure. Public/browser projections never turn a transport failure into a fixture answer.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, HTTPX, Python `sqlite3`, standard-library HTML parsing, existing provider gateway and full-intelligence Agent runtime

**Storage**: Existing SQLite file with new normalized game/stat/play/document tables and an FTS5 external-content index; exact highlights response cache remains separate by table

**Testing**: pytest unit/contract/integration/restart suites, Ruff, isolated fixture and live-intelligence evaluation suites, Playwright browser regressions, authenticated post-deploy black-box journeys

**Target Platform**: Linux container deployed with Docker Compose

**Project Type**: Python web service with a static browser client and offline warm CLI

**Performance Goals**: Indexed matchup/document lookup p95 under 300 ms for one season; import requests bounded by existing provider time and response-size limits; fixture latency and real model/search latency reported separately

**Constraints**: No new database service; fixed HTTPS hosts only; no arbitrary URL fetches; exact filters precede BM25; demo and partial-search evidence cannot become verified facts; fail open to the existing provider stack

Runtime/search capability failures remain typed and request-scoped across fallbacks. A successful
fallback can return data plus a provider-neutral notice, while capability issues are stripped before
shared cache writes; no-evidence failures remain technical errors rather than ordinary empty data.

**Scale/Scope**: One full NBA season schedule, on-demand details/player lines/play-by-play where published, bounded narrative documents, one local provider wrapper, one public HTML adapter, importer CLI, contextual series conversations, public demo isolation, and regression coverage

## Constitution Check

*GATE: Passed before research and re-checked after design.*

| Principle | Design evidence | Gate |
|---|---|---|
| Specification-first delivery | Feature spec, plan, research, data model, contracts, HLD, LLD, tasks and traceability are delivered together. | PASS |
| Evidence-first NBA facts | Relational rows retain source/freshness/evidence state; partial documents never become verified score/stat rows; conflicts are rejected. | PASS |
| Safety and respectful scope | Existing pre-retrieval safety remains; importer hosts are fixed, documents are bounded/sanitized, and arbitrary URLs are disallowed. | PASS |
| Contract- and test-first engineering | Storage/provider/import contracts and failure cases precede implementation; deterministic facts remain outside generation. | PASS |
| Observable, reproducible and simple operations | SQLite/FTS5 uses the existing volume and standard library; fixture tests are offline; counters expose safe diagnostics. | PASS |

Post-design review: PASS. BM25 is justified only for narrative evidence; canonical game identity remains relational, which is simpler and prevents cross-game similarity errors.

## Project Structure

### Documentation (this feature)

```text
specs/004-structured-game-index/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── hld.md
├── lld.md
├── quickstart.md
├── contracts/
│   └── game-index-provider.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
apps/api/src/
├── config.py
├── main.py
├── application/
│   ├── chat_use_case.py
│   ├── parser.py
│   └── session_meta.py
├── infrastructure/
│   ├── game_index.py
│   └── hermes_agent_runtime.py
└── providers/
    ├── hupu_adapter.py
    ├── indexed_provider.py
    └── __init__.py

scripts/
└── warm-game-index.py

tests/
├── unit/test_game_index.py
├── contract/test_hupu_adapter.py
├── contract/test_indexed_provider.py
├── integration/test_game_index_chat.py
├── integration/test_full_intelligence.py
└── e2e/test_chat.spec.ts
```

**Structure Decision**: Keep persistence in infrastructure and source-specific parsing in providers. The wrapper implements the existing provider port, so query planning, the Agent tools, highlights, and public HTTP schemas reuse one retrieval path without vendor logic.

## Complexity Tracking

No constitution violations. FTS5 adds one internal virtual table but avoids a separate vector/database service and is gated by measured retrieval requirements.

## Requirement Traceability

| Requirements | Design | Verification |
|---|---|---|
| FR-001–FR-004 | Data model, HLD retrieval stages, LLD canonical term builder and FTS query | Index unit tests for structure, aliases, filters and rank |
| FR-005–FR-006 | Provider wrapper contract and composition root | Provider contract and chat integration tests |
| FR-007–FR-010 | Origin/evidence fields, conflict and completeness policy; search-title metadata is excluded from fact authority and search-summary claims retain partial scope | Pollution, conflict, idempotency, title/body authorization and restart tests |
| FR-011–FR-012 | Fixed-host Hupu adapter, warm CLI and fail-open wrapper | Adapter failure matrix and restart/fallback tests |
| FR-013 | Bounded document projection into Agent observation; recovery of truncated/budget-status Agent prose; preservation of polarity, acting team, game identity, and single-game versus series fact scope with clause-level pruning | Full-intelligence synthesis, truncation, award-scope, negation/actor/game-scope, mixed-clause and recap-relevance regressions |
| FR-014 | Index counters/readiness projection | Readiness contract test |
| FR-015 | Test-first tasks and quickstart gates | Full pytest, lint, evaluation and browser suites |
| FR-016 | Dedicated `search_web` provider operation and local-first BM25 path | Provider-composition, indexed-provider, and full-intelligence regressions |
| FR-017–FR-019 | Shared contextual selection semantics, request-scoped verified candidate map, guarded active-game commit, scoped observation recovery | Paraphrase/polarity parser matrix, exact two-turn and four-turn recommendation journeys, timeout/generic-clarification/wrong-tool regressions |
| FR-024 | Typed runtime/search capability issues, fallback merge, cache stripping, Agent-tool projection, HTTP/SSE notices | Runtime and three search-adapter contracts, Gateway/Agent-tool tests, full-intelligence and HTTP/SSE regressions |
| FR-020 | Public/demo separation in providers, selected-game registry, and browser transport handling | Fixture-ID pollution, demo-isolation, API failure, and Playwright network-error tests |
| FR-021–FR-022 | System prompt style boundary, public output guard, and deterministic pre-retrieval safety response | Prompt nondisclosure/tone contract tests, schema projection tests, insulting-reference safety tests |
| FR-023 | Isolated evaluation suites and release evidence report | Fixture A–I run, repeated live-runtime matrix, data coverage query, authenticated black-box journey and latency table |
| FR-025 | Reattach the canonical game projection to PBP-only results and render score-after pairs with team labels | Deterministic selected-game and full-intelligence away-winner PBP regressions |
