# Tasks: Managed Public Search Integration

**Input**: Design documents from `/specs/003-aliyun-iqs-search/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/provider-adapter.md

**Tests**: Contract, integration, safety and browser regressions are required by FR-013.

**Organization**: Tasks are grouped by user story and executed test-first.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify repository safety boundaries before adding a credential-backed adapter.

- [x] T001 Verify secret and generated-file exclusions in `.gitignore` and `.dockerignore`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Preserve the existing typed search/evidence boundary.

- [x] T002 Confirm and document that no public schema change is needed in `specs/003-aliyun-iqs-search/contracts/provider-adapter.md`

**Checkpoint**: Existing `NewsQuery -> ProviderResult[list[NewsItem]]` port is ready for implementation.

---

## Phase 3: User Story 1 - Search current NBA information (Priority: P1) 🎯 MVP

**Goal**: Relevant public NBA searches use IQS as the first search source while retaining the existing Agent and evidence rules.

**Independent Test**: A mocked successful IQS response becomes no more than five sanitized, partial `NewsItem` candidates and the provider stack selects IQS first.

### Tests for User Story 1

- [x] T003 [P] [US1] Add successful request/response, bounds, sanitization and evidence contract tests in `tests/contract/test_aliyun_iqs_search.py`
- [x] T004 [P] [US1] Add configuration and first-choice composition tests in `tests/contract/test_provider_mode.py`

### Implementation for User Story 1

- [x] T005 [US1] Implement fixed-endpoint request projection and response parsing in `apps/api/src/providers/aliyun_iqs_adapter.py`
- [x] T006 [US1] Add bounded IQS settings and validation in `apps/api/src/config.py` and `.env.example`
- [x] T007 [US1] Export and compose IQS ahead of existing search fallbacks in `apps/api/src/providers/__init__.py` and `apps/api/src/main.py`

**Checkpoint**: User Story 1 passes adapter and provider-mode tests independently.

---

## Phase 4: User Story 2 - Continue safely during search failure (Priority: P2)

**Goal**: Provider errors, unsafe payloads, empty results and irrelevant fallbacks fail safely without degrading deterministic NBA answers.

**Independent Test**: Every documented error and malformed response maps to a safe outcome; a useful fallback can succeed, while unrelated matchup snippets never reach the answer.

### Tests for User Story 2

- [x] T008 [P] [US2] Add documented error-code, timeout, redirect, oversize, invalid-JSON, empty and fallback tests in `tests/contract/test_aliyun_iqs_search.py`
- [x] T009 [P] [US2] Add explicit-matchup irrelevant-fallback regression in `tests/integration/test_full_intelligence.py`

### Implementation for User Story 2

- [x] T010 [US2] Implement typed failures, fallback-on-error/empty and passive status updates in `apps/api/src/providers/aliyun_iqs_adapter.py`
- [x] T011 [US2] Make explicit-matchup relevance filtering fail closed in `apps/api/src/application/chat_use_case.py`

**Checkpoint**: User Stories 1 and 2 pass independently with no irrelevant fallback output.

---

## Phase 5: User Story 3 - Configure and diagnose search securely (Priority: P3)

**Goal**: Operators can mount the key safely and see passive search availability without exposing implementation details or spending quota on health checks.

**Independent Test**: Compose renders with a secret file, the container reads it as the service user, readiness reports unverified/ok/degraded passively, and no key or provider name appears in public chat output.

### Tests for User Story 3

- [x] T012 [P] [US3] Add passive readiness-state tests in `tests/contract/test_http_chat.py` and configuration-secret tests in `tests/contract/test_provider_mode.py`

### Implementation for User Story 3

- [x] T013 [US3] Expose implementation-neutral passive search state in `apps/api/src/providers/search_augmented_provider.py` and `apps/api/src/api/http_routes.py`
- [x] T014 [US3] Add Docker Secret wiring in `docker-compose.siliconflow.yml` and an interactive key helper in `scripts/configure-aliyun-iqs-key.sh`
- [x] T015 [US3] Document setup, trial limits and safe verification in `docs/byok.md` and `specs/003-aliyun-iqs-search/quickstart.md`

**Checkpoint**: All three stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Complete project-wide regression and live deployment validation.

- [x] T016 Run `pytest -q`, `ruff check .`, `git diff --check` and `make eval`
- [x] T017 Build/recreate the Compose service and verify `/readyz` plus key readability without printing the key
- [x] T018 Run the real NBA acceptance query from `specs/003-aliyun-iqs-search/quickstart.md` and record the outcome without provider or credential leakage

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup**: starts immediately.
- **Foundational**: follows setup and blocks implementation.
- **US1**: follows foundational work and supplies the adapter/configuration used by later stories.
- **US2**: follows US1 core adapter but its tests may be written in parallel.
- **US3**: follows US1 composition; readiness tests may be written in parallel.
- **Polish**: follows all user stories; live acceptance additionally requires the operator's valid secret.

### User Story Dependencies

- **US1**: no other story dependency.
- **US2**: depends on the US1 adapter surface, but remains independently testable with injected HTTP/fallback clients.
- **US3**: depends on the US1 composed provider, but remains independently testable without a paid network call.

### Parallel Opportunities

- T003 and T004 can be authored in parallel.
- T008 and T009 can be authored in parallel.
- T012 can run while US2 implementation is finalized.
- Documentation updates can follow the stable configuration contract without touching adapter code.

## Parallel Example: User Story 1

```text
Task T003: adapter request/response contract tests
Task T004: configuration/composition tests
```

## Implementation Strategy

1. Deliver US1 as the MVP using mocked provider tests.
2. Add US2 failure and relevance guarantees before any live credential is used.
3. Add US3 secret wiring and passive operations.
4. Run all offline gates.
5. Ask the operator to enter the key through the no-echo helper, then deploy and perform the single real acceptance query.
