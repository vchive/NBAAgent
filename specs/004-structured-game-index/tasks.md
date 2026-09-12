# Tasks: Structured Game Index and Retrieval

**Input**: Design documents from `/specs/004-structured-game-index/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/game-index-provider.md

**Tests**: Unit, contract, integration, restart, safety and golden-query tests are required by FR-015.

**Convergence labels**: Historical `(partial)` and `(contradicts)` suffixes record the
pre-fix audit classification that created each convergence task. A checked task is fully
resolved; these suffixes do not describe the current implementation state.

**Organization**: Tasks are grouped by user story and executed test-first.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the new index configuration without changing public contracts.

- [x] T001 Verify existing `.gitignore` and `.dockerignore` protect SQLite runtime files and generated import artifacts
- [x] T002 Add bounded game-index and Hupu source settings to `apps/api/src/config.py`, `.env.example`, and `docker-compose.siliconflow.yml`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the canonical persistence and source parsing boundaries used by every story.

- [x] T003 Add reusable canonical team aliases/slugs in `apps/api/src/application/parser.py`
- [x] T004 [P] Add index schema, origin, conflict, idempotency, filter and BM25 tests in `tests/unit/test_game_index.py`
- [x] T005 [P] Add fixed-host schedule/detail parsing and failure tests in `tests/contract/test_hupu_adapter.py`
- [x] T006 Implement normalized SQLite tables, FTS5 triggers and safe storage operations in `apps/api/src/infrastructure/game_index.py`
- [x] T007 Implement the bounded schedule and box-score adapter in `apps/api/src/providers/hupu_adapter.py`

**Checkpoint**: Canonical public records and partial documents can be persisted and queried independently.

---

## Phase 3: User Story 1 - Find cached games by natural-language matchup (Priority: P1) 🎯 MVP

**Goal**: Natural-language matchup and game queries use persisted structured records across restarts.

**Independent Test**: Warm Knicks–Spurs G1–G5, restart, query the matchup and G5 player line with zero network access.

### Tests for User Story 1

- [x] T008 [P] [US1] Add local-first provider and fail-open contract tests in `tests/contract/test_indexed_provider.py`
- [x] T009 [P] [US1] Add Knicks–Spurs matchup, game detail and restart chat tests in `tests/integration/test_game_index_chat.py`

### Implementation for User Story 1

- [x] T010 [US1] Implement the existing ProviderPort over local-first records in `apps/api/src/providers/indexed_provider.py`
- [x] T011 [US1] Compose and lifecycle-manage the index in `apps/api/src/main.py` and `apps/api/src/providers/__init__.py`
- [x] T012 [US1] Expose safe index readiness counters in `apps/api/src/api/http_routes.py`

**Checkpoint**: Known games are answerable from SQLite by canonical matchup after restart.

---

## Phase 4: User Story 2 - Retrieve relevant cached public evidence (Priority: P2)

**Goal**: Narrative questions use entity-constrained BM25 evidence and produce a concise synthesis.

**Independent Test**: Index mixed-team Chinese articles, query Knicks–Spurs, and verify relevant ranked evidence is synthesized without raw result-list output.

### Tests for User Story 2

- [x] T013 [P] [US2] Add canonical short-Chinese-token and BM25 ranking tests in `tests/unit/test_game_index.py`
- [x] T014 [P] [US2] Add full-intelligence indexed-document synthesis and evidence-tier tests in `tests/integration/test_game_index_chat.py`

### Implementation for User Story 2

- [x] T015 [US2] Merge and persist partial document candidates in `apps/api/src/providers/indexed_provider.py`
- [x] T016 [US2] Improve bounded evidence digestion and stable synthesis repair in `apps/api/src/application/chat_use_case.py`

**Checkpoint**: Relevant cached documents improve answers without becoming verified structured facts.

---

## Phase 5: User Story 3 - Fill and preserve a trustworthy reusable index (Priority: P3)

**Goal**: Operators can warm a full season and details idempotently while demo data remains excluded.

**Independent Test**: Run the importer twice, verify stable counts/details, then exercise fixture mode and confirm zero public-index pollution.

### Tests for User Story 3

- [x] T017 [P] [US3] Add importer date/team/detail/idempotency tests in `tests/contract/test_hupu_adapter.py`
- [x] T018 [P] [US3] Add demo-pollution and public-conflict regression tests in `tests/contract/test_indexed_provider.py`

### Implementation for User Story 3

- [x] T019 [US3] Implement bounded full-season and detail warming in `scripts/warm-game-index.py`
- [x] T020 [US3] Document indexing, warm and recovery operations in `README.md` and `docs/solution.md`

**Checkpoint**: A reusable public season index can be filled safely and reproduced.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Complete traceability, quality gates and deployment acceptance.

- [x] T021 Run focused tests and resolve all failures from `specs/004-structured-game-index/quickstart.md`
- [x] T022 Run `pytest -q`, `ruff check .`, `git diff --check`, `make eval`, and `npx playwright test`
- [x] T023 Warm the deployed 2025-26 season index, recreate the service, and verify the acceptance queries without exposing internal provider/index names

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup** begins immediately.
- **Foundational** follows setup and blocks all stories.
- **US1** follows foundational and supplies the provider composition used by later stories.
- **US2** follows US1 provider composition; its tests can be written alongside US1.
- **US3** follows the storage and adapter foundation; documentation can proceed after contracts stabilize.
- **Polish** follows all stories; live warming requires network availability.

### User Story Dependencies

- **US1** depends only on the foundational index and adapter.
- **US2** depends on the US1 provider wrapper but is independently testable with indexed documents.
- **US3** depends on foundational storage/adapter only; provider pollution tests additionally exercise US1.

### Parallel Opportunities

- T004 and T005 target different contracts.
- T008 and T009 target provider versus chat integration.
- T013 and T014 target storage versus Agent synthesis.
- T017 and T018 target importer versus pollution/conflict behavior.

## Parallel Example: User Story 1

```text
Task T008: IndexedProvider contract and fail-open behavior
Task T009: persistent chat acceptance journey
```

## Implementation Strategy

1. Build and test normalized storage and the fixed-host parser.
2. Deliver US1 as the local-first structured retrieval MVP.
3. Add US2 BM25 documents and stable answer synthesis.
4. Add US3 full-season warm operations and pollution protection.
5. Add US4 contextual recommendation and multi-turn active-game continuity.
6. Run offline and browser gates, warm the deployment, restart, and execute the golden queries.

## Phase 7: Convergence

- [x] T024 Add subject-aware topic ranking and regression coverage so documents whose requested topic is centered on the queried team outrank cross-team drift per FR-004, US2/AC1, and SC-007 (partial)

## Phase 8: Convergence

- [x] T025 Replace the mixed structured-miss/search list fallback with a concise topic-centered synthesis and regression coverage per FR-013 and SC-007 (partial)
- [x] T026 Derive the final Agent evidence state from completed observations so partial public documents cannot surface as no-evidence answers per FR-007 and FR-009 (partial)

## Phase 9: Convergence

- [x] T027 Preserve the original user query as the authoritative typed-retrieval input so an Agent planning paraphrase cannot change the requested metric or game scope per FR-005 and US1/AC2 (partial)

## Phase 10: Convergence

- [x] T028 Add regression coverage for a persistent full-intelligence conversation that resolves `2026-06-14 08:30 这场比赛是怎么个过程，能给我讲讲吗` to the indexed Knicks–Spurs G5, and rejects a web-search-only answer for the game recap per FR-002, FR-003, FR-005, FR-006, and US1/AC2 (partial)
- [x] T029 Recognize natural Chinese single-game recap wording, preserve the active matchup/date scope, and require typed game evidence before web context can supplement a recap per FR-006, FR-007, FR-013, and SC-007 (partial)
- [x] T030 Run focused and full quality gates, deploy, and repeat the original two-turn conversation against the live service per SC-001 and SC-006 (partial)

## Phase 11: Convergence

- [x] T031 Add full-intelligence regressions proving the original user question reaches the Agent unchanged, an Agent-selected web query is not replaced by another tool/query, and selected/session game context is attached as typed retrieval scope per FR-005, FR-013, and the plan's local-first provider wrapper
- [x] T032 Remove service-owned Agent tool substitution and automatic generated follow-up searches; preserve Agent iteration/tool choice while passing validated entity scope separately to public retrieval per FR-005, FR-007, FR-009, and Constitution V (partial)
- [x] T033 Preserve Agent-authored synthesis for open-ended matchup and recap questions while retaining deterministic relation guards for score, winner, player statistics, and play-by-play claims per FR-013, US2/AC3, and SC-007 (partial)
- [x] T034 Run full unit/contract/integration, lint, evaluation and browser gates; deploy and validate the original matchup/recap conversation plus hard-fact adversarial cases per FR-015 and SC-006 (partial)

## Phase 12: Convergence

- [x] T035 Persist remote play-by-play responses back into the indexed game bundle and fetch incomplete indexed summaries from the primary detail source so on-demand venue, coach, box-score and PBP fields are not stranded at schedule-only completeness (FR-006, FR-011, FR-012)
- [x] T036 Share one request-scoped provider budget across all full-intelligence tool calls and nested deterministic lookups, with regression coverage preventing tool fan-out from multiplying the configured upstream operation cap (FR-015, Constitution V)

## Phase 13: Convergence

- [x] T037 Add a dedicated `search_web` provider contract so pure Agent web lookups bypass the structured sports-news call while preserving bounded fallbacks and legacy injected adapters (FR-016)
- [x] T038 Add contract/integration coverage for local BM25 web hits, remote partial-document persistence, and the no-ESPN-news invariant (FR-016)

## Phase 14: Convergence

- [x] T039 Detect incomplete Agent prose after completed structured/search observations and recover a complete mixed-evidence recap instead of returning a dangling heading per FR-013 and SC-007 (partial)
- [x] T040 Block and remove user-visible tool-budget, retry, iteration, truncation, and internal execution narration per FR-014 and Constitution III (contradicts)
- [x] T041 Add full-intelligence regressions for a tool-budget disclosure and a recap truncated after a Markdown heading, then rerun the complete quality and deployment gates per FR-015 and SC-006 (partial)

## Phase 15: Public answer projection convergence

- [x] T042 Keep search/cache/index observations internal to the public answer boundary; remove supplemental clue headings, copied snippets, and workflow caveats while preserving evidence state in metadata per FR-013, FR-014, and Constitution III.
- [x] T043 Prefer a complete Agent-authored synthesis for open-ended turns before structured/search recovery, and add regressions for the Knicks–Spurs recap flow and provider-shaped search output per FR-013 and SC-007.
- [x] T044 Extend browser-side projection defense for legacy cross-verification caveats and run focused unit, integration, contract, and E2E checks.

## Phase 16: Convergence

- [x] T045 Keep provider-shaped search sections hidden across unbulleted titles and excerpts until an explicit composed-answer boundary in the API and browser projections per FR-013, FR-014, and SC-007 (partial)
- [x] T046 Add backend and browser regressions for unbulleted search evidence followed by an explicit conclusion, then run focused and full quality gates per FR-015 and SC-006 (partial)

## Phase 17: Convergence

- [x] T047 Treat a bare two-team matchup as an Agent-synthesis request and inherit the prior matchup and season for an immediate recent/last-game follow-up without rewriting the user's query per FR-005, FR-006, FR-013, and US1/AC2 (partial)
- [x] T048 Require typed evidence for win/how-they-won questions, reject Agent winner/score relation inversions, and add an exact handle-level Knicks–Spurs two-turn regression per FR-007, FR-013, FR-015, Constitution II, and SC-006 (partial)

## Phase 18: Public answer polish convergence

- [x] T049 Preserve complete, honestly qualified Agent analysis while removing retrieval/cache/tool narration from final and streaming UI projections; support natural synthesis boundaries and add backend/browser regressions per FR-013, FR-014, FR-015, and SC-007 (partial)

## Phase 19: Matchup overview polish convergence

- [x] T050 Project a bare two-team series observation into concise natural facts before Agent synthesis, preventing a full relational table echo while retaining every winner/score relation and adding unit, integration, and live two-turn acceptance coverage per FR-006, FR-013, FR-015, Constitution II, and SC-007 (partial)

## Phase 20: Evidence-scope polish convergence

- [x] T051 Preserve polarity, acting-team, game/date, and single-game-versus-series evidence scope in Agent synthesis; exclude search titles from fact authority; normalize impossible single-game wording for series awards without changing valid post-game/basis wording; remove only unsupported causal, award, aggregate, or unasked historical-aside clauses; keep search-only tactics analytical; and add prompt/projection/live acceptance coverage per FR-007, FR-009, FR-013, FR-015, Constitution II, and SC-007 (partial)
- [x] T052 Harden final, streaming, fact and table public projections against paraphrased retrieval/cache/tool narration and forbidden runtime/provider identifiers while preserving the composed basketball conclusion, with backend and browser regressions per FR-014, FR-015, Constitution III, and SC-006 (partial)

## Phase 21: Convergence

- [x] T053 CRITICAL isolate fixed-fixture game-number aliases from live/hybrid public retrieval and resolve G1–G7 only through the explicitly named or session-scoped matchup, season, and stage, with ambiguity clarification and pollution regressions per Constitution II, FR-003, FR-007, FR-008, and SC-004 (contradicts)
- [x] T054 CRITICAL replace implementation-detail meta answers with a generic product-capability response and add public-projection regressions that forbid model, runtime, prompt, tool, provider, cache, database, and retrieval disclosure per Constitution III, FR-014, and SC-006 (contradicts)
- [x] T055 Add PDF-aligned failing acceptance tests for contextual series recommendation, natural player comparison, bounded tactical analysis, session-memory variants, explicit status semantics, and the exact `2026尼克斯-马刺` → `你觉得最精华的是哪一场` journey per FR-005, FR-006, FR-013, FR-015, US4/AC1, and SC-007 (partial)
- [x] T056 Implement one shared contextual subjective-selection classifier for `最精华/最精彩/最好看/最经典/最值得看或回看/推荐哪场` and reuse it in parsing, Agent synthesis detection, typed retrieval, and search-scope inheritance without rewriting the original user query per FR-005, FR-006, FR-013, and US4/AC1 (partial)
- [x] T057 Propagate nested clarification/no-data states truthfully, reject a generic clarification as a successful answer to an already scoped question, and constrain recovered/search evidence to the inherited teams, season, series, game, and requested topic per FR-003, FR-013, and SC-007 (contradicts)
- [x] T058 Keep full-intelligence player-comparison, recommendation, recap, and tactical questions on Agent synthesis after evidence retrieval, producing concise comparison dimensions or two-to-four bounded analytical reasons instead of raw snippets or unnecessary clarification per FR-013, FR-015, PDF categories G/H, and SC-007 (partial)
- [x] T059 Expand deterministic session-meta recognition for natural count and ordinal-history variants while ensuring basketball fact references remain on the verified NBA path per FR-015 and SC-006 (partial)
- [x] T060 Strengthen the golden runner with expected statuses plus required/forbidden semantic assertions so clear questions cannot pass on `needs_clarification`, `no_data`, or generic templates; run focused/full gates, deploy, and repeatedly black-box the PDF A–I matrix and exact user journeys per FR-015 and SC-006 (partial)

## Phase 22: PDF acceptance convergence

- [x] T061 Prevent live/public browser transport failures from silently substituting fixed fixture answers, while preserving explicit fixture development mode and adding browser failure regressions per FR-020 and SC-009 (contradicts)
- [x] T062 Enforce professional Simplified-Chinese address and private-runtime-brand-free prompts, and return the PDF-specific non-mapping response for insulting player references before retrieval per FR-021, FR-022, Constitution III, and SC-009 (partial)
- [x] T063 Audit and improve warmed season detail completeness for venue, coaches, duration, attendance, player lines, and play-by-play; document measured coverage without inventing missing fields per FR-006, FR-011, FR-023, US3/AC2, and SC-010 (partial)
- [x] T064 Separate deterministic fixture scores from repeated real-runtime/search acceptance results, record end-to-end latency and residual evidence gaps, deploy, and publish an honest dated PDF A–I test report per FR-015, FR-023, SC-006, and SC-010 (partial)
- [x] T065 Synchronize the feature specification and implementation plan with the contextual recommendation, scoped recovery, public/demo isolation, prompt/output, safety, and evaluation boundaries implemented in convergence phases per FR-017–FR-023 (contradicts)

## Phase 23: Game-scoped recap recovery convergence

- [x] T066 Add the reported Knicks–Spurs G5 failure as a regression; prevent a complete game-scoped typed explanation from being followed by noisy, cross-game search-summary prose; reject truncated emphasis/dangling analytical clauses; keep search evidence internal and make the deterministic closing explanation natural per FR-013, FR-018, SC-006, and SC-007.

## Phase 24: Runtime truthfulness and recent-game availability convergence

- [x] T067 Query the persistent local game index before spending the recent-highlights remote-slice budget, and never cache a budget-exhausted partial empty list as an authoritative recent result, with restart/live-window regressions per `001/FR-027`, `001/SC-011`, and SC-006 (contradicts).
- [x] T068 Preserve model and search quota/authentication/timeout failure classes across fallback and full-intelligence boundaries, returning a truthful retryable technical state when no usable observation exists instead of `no_data`, with adapter, Agent-runtime, HTTP, and SSE regressions per `001/FR-022`, `001/FR-034`, SC-006, and SC-009 (contradicts).

## Phase 25: Conversational identity and detail-quality convergence

- [x] T069 CRITICAL Persist a uniquely resolved objective game from a full-intelligence typed observation as the canonical active game, and require same-game typed evidence for event/metadata hard-fact follow-ups so mixed or web-only observations cannot answer “最后谁投篮” with another game per Constitution II, FR-003, FR-006, FR-019, US4/AC2, and SC-008 (contradicts).
- [x] T070 Preserve the previously published, request-verified series recommendation when the user challenges that same choice; prevent a later model draft from changing the active recommendation and re-query the same canonical game for subsequent pronouns, with the exact live-like four-turn regression per FR-017, FR-019, US4/AC2, and SC-008 (partial).
- [x] T071 Return available selected-game box-score leaders for natural scoring-leader questions in both full and deterministic paths, with a warmed-index G5 regression proving Jalen Brunson's 45-point line is not reduced to a score-only summary per FR-005, FR-006, US1/AC3, and SC-006 (partial).
- [x] T072 Preserve request-local quota/auth/rate-limit/timeout notices emitted by a successful nested typed Agent query through the tool bridge and HTTP/SSE response without exposing provider details per FR-024 and SC-011 (partial).
- [x] T073 Normalize known Hupu player-name/source typos without collapsing Mikal Bridges into Michael Jordan, retain the original action text only as non-authoritative context, and add adapter regressions per FR-002, FR-006, Constitution II, and SC-004 (partial).
- [x] T074 Correct terminal-game HUD projection so FINAL games show 00:00 and expose verified per-period scores when available while leaving genuinely absent period data explicit, with browser/contract regressions per FR-006, FR-015, and SC-006 (partial).

## Phase 26: Play-by-play score semantics convergence

- [x] T075 Reattach the server-validated canonical game to PBP-only results and label score-after values with both teams in tables, event narration, direct last-shot answers, and tactical facts; cover an away-winner G5 in deterministic and full-intelligence regressions per FR-025, SC-006, and SC-012 (contradicts).
