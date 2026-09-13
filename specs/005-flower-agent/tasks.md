# Tasks: 种花 Agent

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: 本功能明确要求单元、契约、集成、浏览器和黄金题集验证；测试任务因此纳入每个用户故事。

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Confirm the feature branch and remote checkpoint before implementation in Git history (`1352fc9`) and preserve loopback-only service defaults.
- [X] T002 Record the flower feature specification and constitution references in `specs/005-flower-agent/spec.md` and `.specify/memory/constitution.md`.
- [X] T003 [P] Define the flower implementation plan and research decisions in `specs/005-flower-agent/plan.md` and `specs/005-flower-agent/research.md`.
- [X] T004 [P] Define entities and public contracts in `specs/005-flower-agent/data-model.md` and `specs/005-flower-agent/contracts/`.

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T005 [P] Implement bounded flower entities and enums in `apps/api/src/domain/flower.py` and `apps/api/src/domain/flower_models.py`.
- [X] T006 [P] Implement the curated alias-aware knowledge base in `apps/api/src/application/flower_knowledge.py`.
- [X] T007 [P] Implement flower parsing, context slot extraction and pronoun resolution in `apps/api/src/application/flower_parser.py`.
- [X] T008 [P] Implement pre-retrieval flower safety rules in `apps/api/src/application/flower_safety.py`.
- [X] T009 Generalize the bounded Agent tool registry and runtime allow-lists in `apps/api/src/infrastructure/agent_tools.py` and `apps/api/src/infrastructure/hermes_agent_runtime.py`.
- [X] T010 Extend public response provenance and capability notices without exposing implementation metadata in `apps/api/src/api/schemas.py`.
- [X] T011 Wire the flower default domain and explicit NBA compatibility route in `apps/api/src/application/domain_router.py` and `apps/api/src/main.py`.

## Phase 3: User Story 1 - 获取可执行的种花建议 (Priority: P1) 🎯 MVP

**Goal**: Give a Chinese home grower useful offline selection, care and symptom guidance with bounded multi-turn context.

**Independent Test**: Send `北阳台适合种什么花？` followed by `那绣球多久浇水？`, then `月季黄叶怎么办？`; answers must be flower-specific, actionable, context-aware and non-diagnostic.

### Tests for User Story 1

- [X] T012 [P] [US1] Add domain unit tests for aliases, selection, symptom separation and safety-neutral context in `tests/unit/test_flower_domain.py`.
- [X] T013 [P] [US1] Add multi-turn/session and offline HTTP integration tests in `tests/integration/test_flower_chat_use_case.py`.
- [ ] T014 [US1] Add golden-question scoring cases for selection, watering, light, soil, fertilising, pruning, propagation and seasonal plans in `tests/evaluation/flower_cases.jsonl`.

### Implementation for User Story 1

- [X] T015 [US1] Implement complete deterministic care, selection and symptom rendering in `apps/api/src/application/flower_service.py`.
- [X] T016 [US1] Implement session-safe orchestration, idempotency and context persistence in `apps/api/src/application/flower_chat_use_case.py`.
- [ ] T017 [US1] Add a safe public garden-context projection to sync/SSE `ChatResult` and `ChatResponse` in `apps/api/src/application/flower_chat_use_case.py`, `apps/api/src/application/chat_use_case.py` and `apps/api/src/api/schemas.py`.
- [X] T018 [US1] Ensure default browser copy and prompts describe flower roaming mode in `apps/web-demo/index.html`, `apps/web-demo/app.js` and `apps/web-demo/styles.css`.

## Phase 4: User Story 2 - 用新鲜资料补充长尾问题 (Priority: P2)

**Goal**: Add bounded current public material without turning search snippets into facts or breaking offline care.

**Independent Test**: Exercise a configured search success, empty result, timeout, auth failure and quota exhaustion for a city/month question; each response remains useful and labels uncertainty.

### Tests for User Story 2

- [X] T019 [P] [US2] Add flower Agent manifest, cross-domain rejection and observation-cleaning contract tests in `tests/contract/test_flower_hermes_runtime.py`.
- [ ] T020 [P] [US2] Add fixed-adapter domain-prefix, prompt-injection and bounded-output contract tests in `tests/contract/test_flower_search_provider.py`.
- [ ] T021 [US2] Add search failure, quota notice and local-answer preservation integration tests in `tests/integration/test_flower_failures.py`.

### Implementation for User Story 2

- [X] T022 [US2] Add a flower search provider/gateway with bounded caching and observation projection in `apps/api/src/application/flower_chat_use_case.py`.
- [ ] T023 [US2] Carry a server-owned flower domain marker through Baidu, Qianfan, Aliyun IQS and DDG adapters without adding an NBA prefix in `apps/api/src/providers/baidu_adapter.py`, `apps/api/src/providers/qianfan_search_adapter.py`, `apps/api/src/providers/aliyun_iqs_adapter.py` and `apps/api/src/providers/ddg_adapter.py`.
- [X] T024 [US2] Map model/search quota, auth, timeout and rate-limit outcomes to public notices and retain safe local guidance in `apps/api/src/application/flower_chat_use_case.py` and `apps/api/src/infrastructure/hermes_agent_runtime.py`.
- [ ] T025 [US2] Add source freshness and contradiction wording rules for search-only observations in `apps/api/src/application/flower_chat_use_case.py` and `apps/api/src/domain/flower.py`.
- [X] T026 [US2] Expose retryable/permanent capability notices in the browser reducer and error cards in `apps/web-demo/api-client.js` and `apps/web-demo/app.js`.

## Phase 5: User Story 3 - 安全、清晰且可控的交互 (Priority: P3)

**Goal**: Make safety, status, provenance and session boundaries understandable while keeping internal implementation private.

**Independent Test**: Send dangerous chemical, unknown ingestion, pet exposure, identity, cancellation and repeated-idempotency requests through sync and SSE; verify ordering, safe wording and no internal leakage.

### Tests for User Story 3

- [X] T027 [P] [US3] Add adversarial public-output and product-identity tests in `tests/contract/test_schemas.py` and `tests/unit/test_safety.py`.
- [ ] T028 [P] [US3] Add sync/SSE parity, cancellation, idempotency and malformed-event integration tests in `tests/integration/test_flower_failures.py`.
- [X] T029 [P] [US3] Add first-paint, flower prompt and quota-error browser smoke tests in `tests/e2e/test_flower_ui.spec.ts`.

### Implementation for User Story 3

- [X] T030 [US3] Keep dangerous requests ahead of search/model invocation and render immediate actions/help routes in `apps/api/src/application/flower_safety.py` and `apps/api/src/application/flower_chat_use_case.py`.
- [X] T031 [US3] Remove provider, runtime, model, prompt, tool, URL, key and raw-search leakage at Agent and wire boundaries in `apps/api/src/infrastructure/hermes_agent_runtime.py`, `apps/api/src/infrastructure/agent_tools.py`, `apps/api/src/domain/safety.py` and `apps/api/src/api/schemas.py`.
- [ ] T032 [US3] Add a privacy-aware telemetry projection for flower intent, outcome, latency and evidence without question text or precise address in `apps/api/src/infrastructure/telemetry.py` and `apps/api/src/application/flower_chat_use_case.py`.
- [X] T033 [US3] Keep default deployment private and document authenticated public override in `docker-compose.yml`, `.env.example`, `apps/web-demo/README.md` and `specs/005-flower-agent/quickstart.md`.

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T034 [P] Rebrand package metadata, scripts, compose labels and root documentation to flower while retaining explicit migration aliases in `pyproject.toml`, `README.md`, `Dockerfile`, compose files and `apps/__init__.py`.
- [ ] T035 [P] Add the flower evaluation runner and 63-case report in `tests/evaluation/flower_cases.jsonl`, `tests/evaluation/test_flower_cases.py` and `docs/flower-agent-test-report.md`.
- [ ] T036 [P] Add concurrency/session-isolation tests for 32 HTTP and 100 SSE requests in `tests/integration/test_flower_concurrency.py`.
- [ ] T037 Run `python3 -m compileall -q apps/api/src`, `ruff check apps/api/src tests`, the full pytest suite and Playwright smoke tests; record results in `docs/flower-agent-test-report.md`.
- [ ] T038 Run a read-only SDD consistency analysis using `/speckit-analyze`, resolve any coverage gaps and rerun tests.
- [ ] T039 Verify the service remains stopped and port 8000 is not publicly listening before hand-off; record `docker ps` and `ss -ltnp` results in the test report.

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 is documentation/setup and precedes all implementation.
- Phase 2 is foundational and blocks user stories.
- US1 can ship as the offline MVP after Phase 2.
- US2 depends on the flower context/tool contracts from US1 but can be tested independently with injected adapters.
- US3 depends on the shared envelopes from Phase 2 and may run alongside US2 after US1's safety boundary exists.
- Polish follows all desired stories and the final regression run.

### Parallel Opportunities

- T003/T004, T005–T008 and T012/T013 can run in parallel because they touch separate artifacts.
- T019/T020 and T027/T029 can run in parallel with their respective implementations.
- T034–T036 are independent cross-cutting tasks after core behavior stabilizes.

## Implementation Strategy

1. Preserve the pushed checkpoint and finish the P1 offline path first.
2. Validate the safety/context contract before enabling any online or model capability.
3. Add search and full intelligence as additive capabilities with explicit notices, never as silent replacements.
4. Complete UI/rebrand and compatibility cleanup, then run the full regression and SDD analysis.
5. Keep the service stopped and loopback-only at hand-off.

## Traceability Summary

| Story | Requirements | Tasks |
|---|---|---|
| US1 | FR-002–FR-005, SC-001–SC-002 | T012–T018 |
| US2 | FR-006–FR-008, SC-003 | T019–T026 |
| US3 | FR-009–FR-013, SC-004–SC-006 | T027–T033 |
| Polish | FR-001, FR-014 and all cross-cutting checks | T034–T039 |
