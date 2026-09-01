# Tasks: 赛事回顾持久缓存与完整性

**Input**: Design documents from `/specs/002-persistent-highlights-cache/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: 本功能按 Constitution IV 明确采用 test-first；缓存、路由、重启和浏览器交互均需自动化覆盖。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可在不同文件上并行
- **[Story]**: 对应 spec.md 的用户故事

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 增加有界、可关闭的缓存配置和持久卷，不改变现有默认接口。

- [X] T001 Add validated highlights-cache path, TTL, entry and payload limits to `apps/api/src/config.py` and `.env.example`; cover invalid settings in `tests/unit/test_config.py`.
- [X] T002 [P] Create the writable `/app/data` mount point and named persistent volume in `Dockerfile` and `docker-compose.yml`; document removal semantics in `README.md`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 建立 SQLite cache contract、完整度比较和失败退化基础，阻塞全部用户故事。

- [X] T003 Write failing unit tests for schema creation, typed round-trip, expiry, corruption isolation, size/entry bounds, refresh leases and detail completeness monotonicity in `tests/unit/test_highlights_cache.py`.
- [X] T004 Implement the WAL SQLite cache, typed model registry, stable keys, counters, leases, bounded pruning and fail-open behavior in `apps/api/src/infrastructure/highlights_cache.py`.
- [X] T005 Wire one application-owned cache instance and safe lifecycle cleanup into `apps/api/src/main.py`, keeping injected test use cases compatible.

**Checkpoint**: SQLite 基础可独立读写、损坏可隔离、不可用时可退化。

---

## Phase 3: User Story 1 - 再次进入立即看到精彩回顾 (Priority: P1) 🎯 MVP

**Goal**: 最近五场和历史范围首次写入后可零 Provider 命中，并跨应用重启复用。

**Independent Test**: 同一数据库文件上启动两个应用实例；第一个写入，第二个在 Provider 超时时仍返回相同最近五场。

### Tests for User Story 1

- [X] T006 [P] [US1] Add contract tests for recent/date/range miss→write→zero-provider hit, stale historical response and current-day fresh-only policy in `tests/contract/test_highlights_cache.py`.
- [X] T007 [P] [US1] Add cross-app restart persistence and unavailable-database fallback tests in `tests/integration/test_highlights_cache_restart.py`.

### Implementation for User Story 1

- [X] T008 [US1] Add cache-first loaders, stale-while-revalidate background tasks and per-key refresh coalescing without changing public envelopes in `apps/api/src/api/highlights_routes.py`.
- [X] T009 [US1] Register cached games back into the server-owned selected-game registry so cached cards preserve chat context in `apps/api/src/api/highlights_routes.py` and `apps/api/src/application/highlights.py`.

**Checkpoint**: 最近五场/历史范围可跨重启即时复用，今日数据仍严格 fresh-only。

---

## Phase 4: User Story 2 - 查看尽可能完整的比赛详情 (Priority: P1)

**Goal**: 最近列表成功后后台预热最多五场详情，完整详情/PBP 可持久复用且不会数据倒退。

**Independent Test**: 首次最近五场请求完成后台任务后关闭 Provider；五场 detail 均从 SQLite 返回，完整版本不被低字段/PBP 响应覆盖。

### Tests for User Story 2

- [X] T010 [P] [US2] Add detail-cache, five-game bounded prefetch, no-downgrade and conflicting-score tests in `tests/contract/test_highlights_cache.py` and `tests/unit/test_highlights_cache.py`.

### Implementation for User Story 2

- [X] T011 [US2] Cache `HighlightDetailResponse` with deterministic completeness scoring and final/live TTL policy in `apps/api/src/api/highlights_routes.py` and `apps/api/src/infrastructure/highlights_cache.py`.
- [X] T012 [US2] Warm at most five completed-game details after a recent-list miss/refresh with bounded concurrency and failure isolation in `apps/api/src/api/highlights_routes.py`.
- [X] T013 [P] [US2] Preserve all currently available summary/leaders/PBP fields and explicitly document unavailable venue/duration semantics in `specs/002-persistent-highlights-cache/research.md` and `docs/solution.md`.

**Checkpoint**: 卡片列表与详情/PBP 都可持久命中，缺失元信息仍不猜测。

---

## Phase 5: User Story 3 - 缓存数据保持新鲜且不误导 (Priority: P2)

**Goal**: 快命中无闪烁，慢请求有一次加载反馈；缓存异常和实时过期不误导。

**Independent Test**: 浏览器 mock 50ms 最近响应不出现 loading；300ms 响应出现一个 loading 区域，完成后五场卡片正常显示。

### Tests for User Story 3

- [X] T014 [P] [US3] Update Playwright tests for fast-cache no-loading, delayed slow-loading and single loading announcement in `tests/e2e/test_highlights.spec.ts`.

### Implementation for User Story 3

- [X] T015 [US3] Delay history loading UI by 250ms, retain prior content until slow threshold, cancel stale timers and remove duplicate loading copy in `apps/web-demo/app.js`.
- [X] T016 [US3] Expose privacy-safe persistent cache health/counters only through internal health dependency state, without public cache keys or paths, in `apps/api/src/api/http_routes.py` and `apps/api/src/infrastructure/highlights_cache.py`.

**Checkpoint**: 用户能感知真实慢请求，但缓存命中无空白/闪烁且只显示一次加载文案。

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T017 [P] Align cache/source architecture and deployment operations in `README.md`, `docs/solution.md`, and `specs/002-persistent-highlights-cache/quickstart.md`.
- [X] T018 Run Ruff, unit/contract/integration/evaluation/Playwright gates, rebuild public live Compose, verify `/readyz`, prime the SQLite volume, recreate the container without deleting the volume, and record evidence in `specs/002-persistent-highlights-cache/quickstart.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 and blocks all user stories.
- US1 depends on Phase 2 and is the persistence MVP.
- US2 depends on the US1 loaders but remains independently testable through detail endpoints.
- US3 depends on the stable recent/range behavior from US1.
- Phase 6 requires desired user stories complete.

### Parallel Opportunities

- T001 and T002 touch different setup files except documentation and may be sequenced if README overlaps.
- T006/T007 can be prepared in parallel after T004's public cache API is fixed.
- T010 and T014 touch separate test suites.
- T013 can proceed alongside the detail implementation because it only records verified source limitations.

## Implementation Strategy

1. Define and test SQLite cache semantics before route changes.
2. Deliver recent/date/range persistence and restart proof as the MVP.
3. Add detail prefetch and monotonic completeness.
4. Add non-flashing frontend loading behavior.
5. Run full gates, deploy with a named volume, and verify persistence across recreate.

## Notes

- Every cache read re-validates the public Pydantic model.
- No task may persist Provider raw payloads, prompts, sessions, cookies or secrets.
- `docker compose down -v` is explicitly outside normal deployment because it deletes the cache volume.
