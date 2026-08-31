# Tasks: NBA Chat Agent

**Input**: Design documents from `/specs/001-nba-chat-agent/`

**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`,
`quickstart.md`, and the project constitution.

**Implementation stance**: fixture-first vertical slice. The default runnable milestone uses a
deterministic local provider and template composer. An opt-in, constrained SiliconFlow
OpenAI-compatible composer is now available for F/G analysis after its contract tests pass;
formal isolated Hermes sidecar deployment remains a separate hardening item.

**Status snapshot (2026-08-27)**: `[X]` means the implementation and a local check are present in
the current workspace. `[ ]` is intentionally retained for work that is missing, only partially
covered, or still requires deployment/acceptance evidence. The unchecked items are the next
delivery queue; they are not hidden prerequisites for running fixture mode.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: establish a reproducible Python API workspace without disturbing the existing UI
prototype.

- [X] T001 Create the Python package and test directory layout from `plan.md` in `apps/api/src/` and `tests/{unit,contract,integration,evaluation}/`.
- [X] T002 Add Python 3.12 project metadata, runtime and test dependencies, pytest configuration, and editable-install entry points in `pyproject.toml`.
- [X] T003 [P] Add environment/configuration templates (including `.env.example`) and a documented fixture default in `apps/api/src/config.py` and `.env.example`.
- [X] T004 [P] Add package markers and developer commands for API, tests, and evaluation in `apps/api/src/**/__init__.py`, `Makefile`, and `README.md`.

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: implement the contracts and safety boundaries shared by every user story.

**Checkpoint**: a typed request can be validated, classified, logged, and routed without any
provider or model call.

- [X] T005 [P] Define canonical value objects, entities, enums, and validation invariants from `data-model.md` in `apps/api/src/domain/models.py`.
- [X] T006 [P] Define typed application/provider/runtime protocols, request budgets, cancellation, and provider/runtime error types in `apps/api/src/application/ports.py` and `apps/api/src/domain/errors.py`.
- [X] T007 [P] Implement injectable UTC clock, Asia/Shanghai conversion, season labels, date ranges, and PBP time-window helpers in `apps/api/src/domain/time_policy.py`.
- [X] T008 [P] Implement bounded in-memory session/context storage, idempotency reservations, and TTL cache interfaces in `apps/api/src/infrastructure/session_store.py` and `apps/api/src/infrastructure/cache.py`.
- [X] T009 [P] Implement privacy-aware query telemetry and redaction in `apps/api/src/infrastructure/telemetry.py`.
- [X] T010 [P] Implement pre-retrieval `SafetyGuard` and post-composition `OutputGuard` policies/templates in `apps/api/src/domain/safety.py`.
- [X] T011 Implement public HTTP schemas, status/error envelopes, and SSE event serialization matching `contracts/http-api.md` in `apps/api/src/api/schemas.py` and `apps/api/src/api/sse.py`.
- [X] T012 [P] Add foundational unit tests for models, time/season rules, safety short-circuit, redaction, and schema validation in `tests/unit/test_models.py`, `tests/unit/test_time_policy.py`, `tests/unit/test_safety.py`, `tests/contract/test_schemas.py`, and `tests/unit/test_telemetry.py`.

## Phase 3: User Story 1 — Objective NBA information (Priority: P1) 🎯 MVP

**Goal**: answer common team/player/game/schedule/stat questions with verified fixture facts,
clear Beijing-time freshness, and the same result envelope for sync and SSE.

**Independent Test**: start the API in fixture mode, query a game/player question, and verify a
structured answer, evidence state, session ID, latency, and no internal provider fields.

### Tests for User Story 1

- [X] T013 [P] [US1] Add versioned objective game/player/schedule fixtures and expected envelopes in `apps/api/src/providers/fixtures/` and `tests/fixtures/`.
- [X] T014 [P] [US1] Add contract tests for `GET /healthz` and `POST /api/v1/chat` in `tests/contract/test_http_chat.py`.
- [X] T015 [P] [US1] Add integration tests for a successful objective request and session creation in `tests/integration/test_chat_objective.py`.

### Implementation for User Story 1

- [X] T016 [P] [US1] Implement fixture provider operations for games, summaries, player/team stats, standings, history, and news in `apps/api/src/providers/fixture_provider.py`.
- [X] T017 [P] [US1] Implement provider result normalization and evidence construction with null-preserving field mapping in `apps/api/src/providers/normalizer.py`.
- [X] T018 [US1] Implement deterministic fact verification and basic derivations (game totals, leaders, standings) in `apps/api/src/domain/verifier.py` and `apps/api/src/domain/derivation.py`.
- [X] T019 [US1] Implement deterministic Chinese intent/entity/time parsing and typed query planning in `apps/api/src/application/parser.py` and `apps/api/src/application/query_planner.py`.
- [X] T020 [US1] Implement official-style template composition for text, fact, table, evidence, and no-data blocks in `apps/api/src/application/template_composer.py`.
- [X] T021 [US1] Implement the shared synchronous use case state machine (safety → context → parse → retrieve → verify → derive → compose → guard) in `apps/api/src/application/chat_use_case.py`.
- [X] T022 [US1] Implement FastAPI app factory, health/liveness routes, and synchronous chat route in `apps/api/src/main.py` and `apps/api/src/api/http_routes.py`.
- [X] T023 [US1] Implement POST SSE chat route with bounded progress/delta/completed events and cancellation propagation in `apps/api/src/api/sse_routes.py`.
- [X] T024 [US1] Complete objective ASGI contract coverage for sync/SSE envelope equivalence and provider-call-free short circuits in `tests/contract/test_http_chat.py` and `tests/contract/test_sse.py`; dedicated integration expansion remains in T015.

## Phase 4: User Story 2 — Complex fact verification and key plays (Priority: P1)

**Goal**: derive series aggregates and last-five-second PBP facts from canonical records, and
politely correct false premises without turning missing data into a contradiction.

**Independent Test**: query a series total, a final-five-second window, and an intentionally
wrong winner premise; compare every number and event against the fixture records.

- [X] T025 [P] [US2] Add complete series/PBP fixtures covering sequence-valid, sequence-missing, overtime, duplicate, and null-field events in `apps/api/src/providers/fixtures/series.json` and `apps/api/src/providers/fixtures/pbp.json`.
- [X] T026 [P] [US2] Write unit tests for series aggregation, PBP window selection/order, null preservation, and premise correction in `tests/unit/test_derivation.py` and `tests/unit/test_verifier_claims.py`.
- [X] T027 [US2] Implement series win/aggregate derivation and PBP final-period/time-window algorithms in `apps/api/src/domain/derivation.py`.
- [X] T028 [US2] Implement premise claim extraction, verified correction objects, and public correction rendering in `apps/api/src/application/parser.py`, `apps/api/src/domain/verifier.py`, and `apps/api/src/application/template_composer.py`.
- [X] T029 [US2] Route PLAY_BY_PLAY, FACT_CHECK, and aggregate intents to the correct typed provider operations in `apps/api/src/application/query_planner.py` and `apps/api/src/application/chat_use_case.py`.
- [X] T030 [US2] Add integration cases for series/PBP/correction/partial-data outcomes in `tests/integration/test_complex_facts.py`.

## Phase 5: User Story 3 — Tactical and subjective analysis (Priority: P2)

**Goal**: provide conclusion-first analysis with 2–4 fact-backed reasons while keeping the
generative runtime unable to retrieve data, perform arithmetic, or bypass safety.

**Independent Test**: run tactical and subjective prompts with Hermes disabled and verify a safe
template response; run the mock Hermes adapter and verify the same fact IDs and output guard.

- [X] T031 [P] [US3] Add runtime/composer contract fixtures and capability-boundary tests in `tests/contract/test_runtime.py`.
- [X] T032 [P] [US3] Implement constrained `HermesRuntimeAdapter`/mock runtime with empty tools, no network/filesystem/memory, policy hash checks, and timeout fallback in `apps/api/src/infrastructure/hermes_runtime.py`.
- [X] T033 [US3] Implement hybrid runtime selection and fact-backed tactical/recap composition in `apps/api/src/application/runtime_selector.py` and `apps/api/src/application/analysis_composer.py`.
- [X] T034 [US3] Extend `OutputGuard` to reject untraceable numeric claims and internal/provider leakage in `apps/api/src/domain/safety.py`.
- [X] T035 [US3] Add tactical/recap/Hermes timeout and unsafe-output integration tests in `tests/integration/test_analysis_runtime.py`.

## Phase 6: User Story 4 — Multi-turn context (Priority: P2)

**Goal**: preserve one active game and bounded summaries inside a session, resolve shorthand
follow-ups, and never share context across sessions.

**Independent Test**: execute three turns in one session using “那场/最后那个球”, then repeat
the shorthand in a fresh session and require clarification.

- [X] T036 [P] [US4] Add context resolver and optimistic version/concurrency unit tests in `tests/unit/test_context.py`.
- [X] T037 [US4] Implement bounded turn summaries, active entity commit, shorthand resolution, and session-version conflict handling in `apps/api/src/application/context_manager.py` and `apps/api/src/infrastructure/session_store.py`.
- [X] T038 [US4] Add three-turn same-session and cross-session isolation integration/evaluation cases in `tests/integration/test_multi_turn.py` and `apps/api/src/evaluation/golden_cases.jsonl`.

## Phase 7: User Story 5 — Safety and failure feedback (Priority: P1)

**Goal**: short-circuit all red-line/out-of-scope requests before provider/cache access and give
clear retryable/non-retryable errors for admission and upstream failures.

**Independent Test**: replay every red-line category and each timeout/rate-limit/empty/invalid
fixture, asserting safe envelopes, zero downstream calls for short circuits, and no stale numbers.

- [X] T039 [P] [US5] Add red-line, out-of-scope, timeout, rate-limit, invalid-json, and empty-result fixtures/tests in `tests/integration/test_safety_failures.py`.
- [X] T040 [US5] Implement bounded admission controller, deadline budget, per-session lock, and cancellation cleanup in `apps/api/src/infrastructure/admission.py` and `apps/api/src/application/chat_use_case.py`.
- [X] T041 [US5] Implement typed provider failure mapping, retry policy, safe user messages, and zero-call telemetry assertions in `apps/api/src/providers/gateway.py` and `apps/api/src/application/chat_use_case.py`.
- [X] T042 [US5] Harden request limits, CORS allow-list, security headers, and redacted error handling in `apps/api/src/main.py` and `apps/api/src/api/http_routes.py`.
- [X] T043 [US5] Complete safety/failure integration tests and verify all red-line branches bypass provider, cache, and Hermes in `tests/integration/test_safety_failures.py`.

## Phase 8: Public provider and date-scoped highlights (Priority: P1/P2)

**Goal**: make live retrieval replaceable and expose a small “赛事焦点 / 精彩回顾” projection
without conflating it with the chat `HISTORY` intent.

- [X] T044 [P] [US1] Add ESPN-shaped response fixtures and adapter contract tests in `tests/contract/test_provider.py` and `apps/api/src/providers/fixtures/espn/`.
- [X] T045 [US1] Implement allow-listed, timeout-bounded ESPN adapter and normalizer mapping in `apps/api/src/providers/espn_adapter.py`.
- [X] T046 [US1] Add cache freshness, fallback selection, and public-data mode configuration in `apps/api/src/providers/gateway.py` and `apps/api/src/config.py`.
- [X] T047 [P] [US1] Add `GET /api/v1/highlights?date=YYYY-MM-DD&timezone=Asia/Shanghai` schema, fixture projection, and no-future-date validation in `apps/api/src/api/highlights_routes.py` and `apps/api/src/application/highlights.py`.
- [X] T048 [US1] Update the web demo left rail to a compact “赛事焦点 / 精彩回顾” mode with recent-five and custom-range controls, clearing stale games on empty/failed dates in `apps/web-demo/index.html`, `apps/web-demo/app.js`, and `apps/web-demo/styles.css`.
- [X] T049 [US1] Complete highlights/date-range contract and UI smoke coverage in
  `tests/contract/test_highlights.py` and `tests/e2e/test_highlights.spec.ts`.
- [X] T060 [US1] Add the bounded `GET /api/v1/highlights/availability` calendar projection with
  tri-state (`available` / `empty` / `unknown`) days, future-day handling, fixture coverage, and
  contract tests in `apps/api/src/api/highlights_routes.py`, `apps/api/src/application/highlights.py`,
  `apps/api/src/api/schemas.py`, and `tests/contract/test_highlights.py`.
- [X] T061 [US1] Add recent-five and bounded custom-range highlights endpoints, provider-slice
  aggregation, visible loading feedback, and browser/contract coverage in `apps/api/src/api/highlights_routes.py`,
  `apps/api/src/application/highlights.py`, `apps/api/src/api/schemas.py`, `apps/web-demo/api-client.js`,
  `apps/web-demo/app.js`, and `tests/e2e/test_highlights.spec.ts`.

## Phase 9: Evaluation, operations, and delivery

- [X] T050 [P] Add at least ten objective cases plus A–I coverage and expected facts in `apps/api/src/evaluation/golden_cases.jsonl` (18 cases, including 16 ALLOW objective cases, are present).
- [X] T051 [P] Implement repeated evaluation runner, seven-dimension scoring, safety veto, and JSON/Markdown report output in `apps/api/src/evaluation/runner.py` and `apps/api/src/evaluation/report.py`.
- [X] T052 Add evaluation and telemetry tests for repeated runs, TTFT/latency, redaction, and provider/cache zero-call invariants in `tests/evaluation/test_runner.py` and `tests/unit/test_telemetry.py`.
- [X] T053 [P] Add Docker/ASGI deployment profiles, health/readiness behavior, and local/public commands in `Dockerfile`, `docker-compose*.yml`, and `Makefile`.
- [X] T054 [P] Add Playwright chat E2E coverage for streaming, keyboard input, responsive layout, retry, and PBP replay in `tests/e2e/test_chat.spec.ts`.
- [X] T055 Run the clean-environment quickstart, all test layers, and update `README.md`, `docs/solution.md`, and `specs/001-nba-chat-agent/quickstart.md` with verified commands and deployment notes.

## Phase 10: SiliconFlow BYOK composer (opt-in)

- [X] T056 Add typed SiliconFlow settings with the fixed HTTPS endpoint, default
  `deepseek-ai/DeepSeek-V4-Flash` model, bounded tokens/response size, and optional secret-file
  loading in `apps/api/src/config.py`.
- [X] T057 Implement the constrained non-streaming SiliconFlow runtime, fact projection, prompt
  boundary, error mapping, cancellation and deterministic fallback in
  `apps/api/src/infrastructure/hermes_runtime.py` and wire it through `ChatUseCase`.
- [X] T058 Add MockTransport contract coverage for request shape, provenance/key redaction,
  missing-key/HTTP/timeout/schema/unsafe output handling, secret files and cancellation in
  `tests/contract/test_siliconflow_runtime.py`.
- [X] T059 Update health/readiness, environment templates, deployment docs and optional Compose
  secret override so default fixture mode remains offline and explicit live mode is auditable.

## Phase 11: Access control for public demo

- [X] T089 Add shared-password settings, Docker secret loader, opaque HttpOnly Cookie sessions,
  constant-time comparison, login-failure rate limiting, and fail-closed configuration in
  `apps/api/src/config.py` and `apps/api/src/infrastructure/auth.py`.
- [X] T062 Add login/status/logout routes and protect chat/highlights APIs while keeping static
  assets and health/readiness probes accessible in `apps/api/src/api/auth_routes.py` and
  `apps/api/src/main.py`.
- [X] T063 Add the login gate, credentialed same-origin transport, 401 recovery, and logout action
  to `apps/web-demo/index.html`, `apps/web-demo/app.js`, `apps/web-demo/api-client.js`, and
  `apps/web-demo/styles.css`.
- [X] T064 Add password configuration script, Compose auth profile, deployment targets, docs, and
  contract tests covering unauthenticated/authorized/logout/missing-secret behavior in
  `scripts/configure-app-password.sh`, `docker-compose.auth.yml`, `Makefile`, `docs/auth.md`,
  and `tests/contract/test_auth.py`.

## Phase 12: Controlled search and full intelligence (opt-in)

- [X] T065 Add typed intelligence-mode request/config support and full-mode runtime selection while preserving deterministic safety, verification, derivation, and fallback boundaries.
- [X] T066 Add the fixed-endpoint DuckDuckGo adapter with bounded query/results/response size, HTML/control/injection cleaning, partial search evidence, and a provider composition wrapper.
- [X] T067 Wire search and full-intelligence capabilities into the live composition root, health/readiness metadata, environment templates, and public Compose profile.
- [X] T068 Add the session-level web-demo “全智能分析（实验）” switch and propagate the selected mode through sync/SSE requests with model/fallback provenance labels.
- [X] T069 Add contract coverage for lowercase mode parsing, feature-flag enforcement, DDG sanitization, invalid JSON, and bounded search evidence.

## Dependencies & Execution Order

### Phase dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 and blocks every user story.
- US1 (Phase 3) is the MVP and must be green before live-provider or UI date work.
- US2 and US5 depend on the US1 use case and provider contracts; their unit fixtures can be
  prepared in parallel.
- US3 and US4 depend on foundational ports and the US1 session/use-case seam, but remain
  independently testable.
- Phase 8 depends on the fixture gateway and HTTP contract; Phase 9 depends on all desired slices.

### Parallel opportunities

- T003–T010 are parallel by file after package layout exists.
- T013–T015, T025–T026, T031, T036, T039, and T044 are independent test/fixture work.
- After T024, US2, US3, US4, and US5 can be staffed in parallel when they touch separate files;
  shared `chat_use_case.py` changes must be serialized.
- T050–T054 can be parallelized after the public envelope is stable.

## Implementation Strategy

1. Finish Setup and Foundational phases.
2. Deliver US1 with fixture provider, sync API, and SSE as the first runnable MVP.
3. Add deterministic PBP/series/correction behavior and safety/failure paths before enabling any
   generative runtime.
4. Add the constrained SiliconFlow composer behind the runtime port, then formalize an isolated
   Hermes sidecar before production use.
5. Add the date-scoped highlights projection and UI switch after the chat contract is stable.
6. Run evaluation and clean-environment checks before deployment or PDF export.

## Notes

- `[P]` means the task can run in parallel without editing an incomplete dependency's file.
- Every task has an exact path and must be marked `[X]` when complete.
- Fixture mode is the default; no task may require a real API key to pass local tests.
- The date switch is a scoreboard projection (`view_date`/`date_range`), not a new chat intent.

## Phase 13: User Story 7 — Official Hermes Agent and bounded NBA tools (Priority: P1)

**Goal**: make full-intelligence mode enter the official Hermes Agent before deterministic parsing,
allow only bounded NBA query/schedule/news tools, explain empty schedules naturally, and preserve the
existing deterministic pipeline as the evidence owner and fallback.

**Independent Test**: with full mode enabled, submit `nihao`, `下周有比赛买`, and `下周有比赛吗`;
the first responds naturally without a tool, the latter two use a bounded schedule tool and return
the resolved Beijing date range instead of generic clarification/no-data text. A red-line prompt has
zero Agent/tool calls, and timeout/repeated-tool cases fall back safely.

### Tests for User Story 7

- [X] T070 [P] [US7] Add domain/config contract tests for `EMBEDDED_AGENT`, agent budgets, exact NBA tool allow-list, composition provenance, and invalid settings in `tests/unit/test_models.py`, `tests/unit/test_config.py`, and `tests/contract/test_hermes_agent_runtime.py`.
- [X] T071 [P] [US7] Add task-bridge tests for tool schemas, request lookup, duplicate argument rejection, deadline/cancellation cleanup, output size bounds, and provider-free errors in `tests/unit/test_agent_tools.py`.
- [X] T072 [P] [US7] Add integration tests for greeting, typo-tolerant schedule, empty schedule explanation, deterministic fallback, multi-turn hints, and pre-Agent safety zero-call behavior in `tests/integration/test_full_intelligence.py` and `tests/integration/test_agent_safety.py`.

### Implementation for User Story 7

- [X] T073 [US7] Lock the official Hermes dependency and add validated Agent configuration/capability fields in `pyproject.toml`, `apps/api/src/config.py`, `.env.example`, and `docker-compose.siliconflow.yml`.
- [X] T074 [US7] Implement the process-global schema/task-local bridge for `nba_query`, `nba_schedule`, and `nba_news`, including budgets, deduplication, sanitized observations, ASGI-loop dispatch, and cleanup in `apps/api/src/infrastructure/agent_tools.py`.
- [X] T075 [US7] Implement the lazy official `run_agent.AIAgent` integration, exact `nba` toolset self-test, server-owned prompt, bounded worker execution, cancellation, usage and result normalization in `apps/api/src/infrastructure/hermes_agent_runtime.py`.
- [X] T076 [US7] Route full mode after SafetyGuard/context but before deterministic parsing, implement internal-tool non-recursive queries and fallback, and commit bounded context in `apps/api/src/application/chat_use_case.py` and `apps/api/src/application/runtime_selector.py`.
- [X] T077 [US7] Add Agent output validation, unobserved-number checks, tool/iteration telemetry and provider-neutral `agent/used` projection in `apps/api/src/domain/safety.py`, `apps/api/src/infrastructure/telemetry.py`, `apps/api/src/domain/models.py`, and `apps/api/src/api/schemas.py`.
- [X] T078 [US7] Update SSE progress/provenance rendering so the UI distinguishes Agent planning, NBA tool lookup, Agent completion and deterministic fallback in `apps/api/src/api/sse_routes.py`, `apps/web-demo/app.js`, and `apps/web-demo/index.html`.

### Delivery and validation for User Story 7

- [X] T079 [P] [US7] Add the three acceptance prompts plus timeout/repeat/injection cases to the golden evaluation and regression coverage in `apps/api/src/evaluation/golden_cases.jsonl`, `tests/evaluation/test_agent_cases.py`, and `tests/e2e/test_chat.spec.ts`.
- [X] T080 [US7] Update architecture, BYOK, quickstart, deployment and reviewer-facing solution documentation for the official Hermes Agent path in `specs/001-nba-chat-agent/{hld.md,lld.md,quickstart.md}`, `docs/byok.md`, `docs/solution.md`, `README.md`, and `Makefile`.
- [X] T081 [US7] Run unit/contract/integration/evaluation/E2E gates, rebuild the public live profile, verify `/readyz` plus the three acceptance prompts, and record the deployment evidence in `specs/001-nba-chat-agent/quickstart.md`.

### Phase 13 dependencies

- T070–T072 are test-first and may be prepared in parallel.
- T073 blocks T075 and the live container build.
- T074 blocks T075–T076; T075 and T077 must complete before T076 integration is accepted.
- T078–T080 depend on the response and telemetry contract from T076–T077.
- T081 is the final gate and requires T070–T080 complete.

## Phase 14: Capability-intent regression hardening

- [X] T082 [US7] Recognize identity/capability questions and common pinyin aliases as
  zero-tool turns, provide a local capability prompt when Hermes is unavailable, and apply a
  conservative terminal-`买` → `吗` correction for unambiguous schedule questions in
  `apps/api/src/application/chat_use_case.py`; add integration coverage in
  `tests/integration/test_full_intelligence.py`.

## Phase 15: Regression convergence

- [X] T083 [US1] Resolve `本周`、`下周` and bounded `未来 N 天` schedule scopes as
  Beijing-time half-open date ranges per FR-009 and SC-015 (partial) in
  `apps/api/src/domain/time_policy.py` and `apps/api/src/application/parser.py`, with parser/time
  policy regression coverage.
- [X] T084 [US2] Resolve “最近一场比赛的关键回合” through the latest completed game and its
  verified play-by-play without requiring a manual card selection per FR-016 and US2/AC2
  (partial) in `apps/api/src/application/{parser.py,query_planner.py,chat_use_case.py}`, with
  unit and integration coverage.
- [X] T085 [US7] Add a validated low-latency reasoning policy for the fixed SiliconFlow Hermes
  runtime, disable unnecessary model thinking by default, and verify the official runtime request
  contract plus live timeout/fallback behavior per SC-007 and SC-016 (partial) in
  `apps/api/src/{config.py,infrastructure/hermes_agent_runtime.py}`, deployment configuration,
  documentation, and contract tests.

## Phase 16: Agent tool-result consistency

- [X] T086 [US7] Reject a successful full-mode answer when its observations are semantically
  unrelated to a high-risk play-by-play request (for example, an empty schedule result for
  “最近一场关键回合”), then fall back to the verified deterministic path per FR-016 and SC-016
  (partial) in `apps/api/src/application/chat_use_case.py`, with integration regression coverage.

## Phase 17: General Agent observation consistency

- [X] T087 [US7] Extend Agent observation relevance checks from play-by-play-only to schedule,
  news, player/stat and tactical/recap questions; reject schedule/news-only observations for an
  unrelated request and fall back to the deterministic fact pipeline. Add integration coverage for
  wrong-tool answers and document the guard in the feature artifacts.

## Phase 18: Provider composition regression

- [X] T088 [US1] Preserve wrapped provider calendar-slice capabilities and optional standings/news
  call signatures so the public search composition can classify empty calendar days correctly and
  keep gateway fallbacks compatible per FR-027/FR-029 (partial); add provider-composition contract
  coverage in `apps/api/src/providers/search_augmented_provider.py` and
  `tests/contract/test_provider_composition.py`.

## Phase 19: Recent highlights latency and completeness

- [X] T090 [US1] Bound live recent-game scanning to a short window, fill an off-season recent-five
  projection from the configured historical snapshot, and filter the result to completed games per
  FR-027/SC-011 (partial); add hybrid regression coverage in
  `apps/api/src/application/highlights.py` and `tests/contract/test_highlights.py`.

## Phase 20: Selected-game chat context convergence

- [X] T091 [US4/US6] Propagate the selected highlights card as a validated `selected_game_id` on
  sync/SSE chat requests; resolve it through the server-owned highlights registry, bind shorthand
  and matching-team questions to that game, preserve explicit-game precedence, and add API,
  integration, and time-query coverage in `apps/api/src/domain/models.py`,
  `apps/api/src/application/chat_use_case.py`, `apps/api/src/application/highlights.py`,
  `apps/api/src/api/highlights_routes.py`, `apps/api/src/main.py`,
  `apps/web-demo/{app.js,api-client.js}`, and the corresponding tests/docs.

## Phase 21: Selected-game answer quality regression

- [X] T092 [US2/US4] Keep clicked-game questions on the verified game snapshot, preserve
  ranking intent (for example “得分第三”), add bounded final-minute evidence to tactical
  answers, and state missing terminal shooter/location fields without guessing; cover parser,
  multi-turn, full-intelligence, HTTP, and browser regression gates.
