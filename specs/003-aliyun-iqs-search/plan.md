# Implementation Plan: Managed Public Search Integration

**Branch**: `003-aliyun-iqs-search` | **Date**: 2026-09-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-aliyun-iqs-search/spec.md`

## Summary

Add Alibaba Cloud IQS UnifiedSearch as the first-choice public-search adapter for full-intelligence NBA questions. The adapter uses a fixed HTTPS endpoint, a server-side secret, `LiteAdvanced` retrieval, five bounded candidates, no paid enhanced summary, sanitization, typed failure mapping, and the existing provider/fallback and evidence boundaries. Structured NBA providers remain authoritative for scores and statistics.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, HTTPX, existing provider gateway and Agent tool runtime

**Storage**: Docker Secret/file-backed credential; existing in-memory news cache; no new persistent data store

**Testing**: pytest contract/integration suites, Ruff, existing evaluation harness, Playwright browser regressions

**Target Platform**: Linux container deployed with Docker Compose

**Project Type**: Python web service with a static browser client

**Performance Goals**: Search adapter completes inside a 5.5-second client timeout and the full response remains inside the existing 65-second request deadline

**Constraints**: Fixed HTTPS egress only; maximum five results and 800 KB response; no redirects; credentials and provider details never reach public output; web results remain partial evidence

**Scale/Scope**: One new search adapter, configuration and Compose wiring, passive diagnostics, contract/integration tests, and operator documentation; no public chat schema change

## Constitution Check

*GATE: Passed before research and re-checked after design.*

| Principle | Design evidence | Gate |
|---|---|---|
| Specification-first delivery | Feature spec, plan, research, data model, contracts, HLD, LLD, tasks and traceability are included in this feature directory. | PASS |
| Evidence-first NBA facts | Search results are `SEARCH` evidence with `partial=true`; structured NBA data keeps precedence for scores, standings, statistics and play-by-play. | PASS |
| Safety and respectful scope | Existing pre-retrieval safety remains unchanged; the adapter strips markup, controls, URLs from prose and prompt-injection patterns. | PASS |
| Contract- and test-first engineering | Provider contract and failure matrix are defined before adapter implementation; mocked HTTP tests precede production wiring. | PASS |
| Observable, reproducible and simple operations | Credential is file-backed, the endpoint is fixed, diagnostics are passive, fallback remains replaceable, and offline fixture tests remain available. | PASS |

Post-design review: PASS. No constitution waiver or additional service boundary is required.

## Project Structure

### Documentation (this feature)

```text
specs/003-aliyun-iqs-search/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── hld.md
├── lld.md
├── quickstart.md
├── contracts/
│   └── provider-adapter.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
apps/api/src/
├── config.py
├── main.py
├── api/http_routes.py
└── providers/
    ├── aliyun_iqs_adapter.py
    ├── search_augmented_provider.py
    └── __init__.py

tests/
├── contract/test_aliyun_iqs_search.py
├── contract/test_provider_mode.py
└── integration/test_full_intelligence.py

scripts/configure-aliyun-iqs-key.sh
docker-compose.siliconflow.yml
.env.example
docs/byok.md
```

**Structure Decision**: Reuse the existing provider port and search augmentation wrapper. The new adapter stays behind the same `NewsQuery -> ProviderResult[list[NewsItem]]` interface, so the Agent, public HTTP schemas and browser client do not gain vendor-specific logic.

## Complexity Tracking

No constitution violations or new public abstractions are introduced.

## Requirement Traceability

| Requirements | Design | Verification |
|---|---|---|
| FR-001–FR-003 | LLD configuration, request projection and response parser | Adapter success/request-shape contract tests |
| FR-004, FR-006, FR-009 | HLD evidence boundary and existing matchup relevance filter | Full-intelligence integration regressions |
| FR-005 | LLD sanitization pipeline | Injection, markup, oversize and duplicate tests |
| FR-007–FR-008 | LLD error mapping and fallback composition | Parameterized failure/fallback contract tests |
| FR-010, FR-012 | Secret-file configuration and public response boundary | Secret leak and public schema tests |
| FR-011 | Passive adapter availability state in readiness | Readiness before/after observed outcome tests |
| FR-013 | Tasks and quickstart quality gates | pytest, Ruff, diff check, eval and browser tests |
