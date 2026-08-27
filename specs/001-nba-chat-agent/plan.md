# Implementation Plan: NBA Chat Agent

**Branch**: `001-nba-chat-agent`
**Date**: 2026-08-26
**Spec**: [spec.md](spec.md)

**Input**: 4 页 NBA Chat Agent 笔试题及其需求规格

## Summary

交付一个可在线访问的中文 NBA Chat Agent，并配套简要方案说明 PDF。系统采用分层、可
替换的数据访问架构：Web 聊天入口 → 检索前安全门 → 意图/实体/赛季解析 → 准入预算 →
公开数据适配器 → 归一化与事实核验 → 确定性聚合/PBP 推导 → 模板或 Hermes-lite 表达 →
输出守卫。HLD 与 LLD 分别记录系统边界和可实现契约；黄金题集负责验证 PDF 的事实、
安全、多轮和性能评分维度。Hermes-lite 是可关闭的 Composer/Runtime 适配器，不拥有
Provider、缓存、安全决策或 NBA 领域事实。

## Technical Context

**Language/Version**: Python 3.12 for API/domain/evaluation; dependency-free HTML/CSS/ES2022
for the current Web Demo (a React/Next.js migration remains optional after the fixture MVP)
**Primary Dependencies**: FastAPI/ASGI, Pydantic v2, httpx, browser `fetch`/ReadableStream for
POST-SSE, pytest；可选 Hermes-lite runtime（版本锁定、sidecar 优先）
**Storage**: 首版无 NBA 内部数据库；会话与 TTL 缓存采用可替换的轻量存储，评测 fixture 使用版本化 JSON
**Testing**: pytest（单元/集成/契约）、Playwright（Web E2E）、黄金题回放与时延采集
**Target Platform**: Linux 容器；公开 Web/API 服务，支持本地 fixture/mock 模式
**Project Type**: Web application with API service and evaluation CLI
**Performance Goals**: 项目目标为正常查询 90% 在 5 秒内完成；记录 TTFT 与完整响应时延。PDF 未规定数字阈值
**Constraints**: 公开互联网取数、不得依赖内部 NBA DB；UTC+8 展示；敏感请求检索前短路；凭据不入库；数据源可替换；Hermes 仅接收结构化已核验事实，生产只允许受限 sidecar
**Scale/Scope**: 面试演示级 v1；单会话至少支持三轮同场追问；A–I 作为黄金评测覆盖建议，不把题型数量当 PDF 硬性规模约束

## Constitution Check — before design

| Gate | Result | Evidence |
|---|---|---|
| Specification-first | PASS | `spec.md` defines scope, scenarios, FR-001–027 and SC-001–011 |
| Evidence-first facts | PASS | Provider → Normalizer → Verifier → Derivation chain in HLD/LLD |
| Safety before retrieval | PASS | Safety Guard is the first orchestrator branch; no-retrieval test required |
| Contract/test-first | PASS | API/provider/evaluation contracts and traceability matrix planned |
| Observable/reproducible | PASS | structured telemetry, fixtures, mock mode and quickstart planned |

No gate violations identified.

## Phase 0 — Research outputs

完成的研究与决策见 [research.md](research.md)，包括：

- ESPN-first、可替换公开数据适配器及 fallback 风险；
- 安全、事实核验和生成分析的分层边界；
- UTC+8 与跨年赛季策略；
- 同步 HTTP + SSE 的入口取舍；
- 黄金题集和七维评分模型；
- 内部 provenance 与用户可见信息的边界。

## Phase 1 — Design outputs

- [hld.md](hld.md)：系统上下文、容器/组件、数据流、部署、NFR、风险和需求追踪。
- [lld.md](lld.md)：包级设计、状态机、schema、Provider 协议、算法、错误和测试细节。
- [data-model.md](data-model.md)：规范领域实体、字段、关系、校验和生命周期。
- [contracts/http-api.md](contracts/http-api.md)：同步/SSE 外部接口契约。
- [contracts/provider-adapter.md](contracts/provider-adapter.md)：公开数据适配器契约和证据等级。
- [contracts/evaluation.md](contracts/evaluation.md)：黄金题、评分和重复采集契约。
- [quickstart.md](quickstart.md)：本地 fixture、联网模式、测试和部署探活步骤。
- [../../docs/solution.md](../../docs/solution.md)：面向评审的简要方案说明源文档，后续导出为 PDF。

## Project Structure

```text
specs/001-nba-chat-agent/
├── spec.md
├── plan.md
├── research.md
├── hld.md
├── lld.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   ├── provider-adapter.md
│   └── evaluation.md
├── checklists/requirements.md
└── tasks.md                         # Phase 2: speckit-tasks

apps/
├── api/
│   ├── src/
│   │   ├── api/                     # HTTP/SSE routes and schemas
│   │   ├── application/             # orchestration use cases
│   │   ├── domain/                  # entities, policies, ports
│   │   ├── providers/               # public-source adapters
│   │   ├── infrastructure/         # cache, session, model, observability
│   │   └── evaluation/              # runner and report generation
└── web-demo/
    ├── index.html                   # zero-build chat/HUD layout
    ├── styles.css                   # responsive broadcast-style visual system
    ├── app.js                       # UI state, PBP replay and fixture fallback
    └── api-client.js                # optional FastAPI/SSE/highlights transport

tests/
├── unit/
├── contract/
├── integration/
├── e2e/
└── evaluation/

docs/
├── solution.md                      # source for the brief solution explanation
└── solution.pdf                     # exported submission artifact (generated before final delivery)
```

根目录还提供 `Dockerfile`、`docker-compose.yml` 和 `.dockerignore`，用于 fixture 默认的
本地 ASGI profile；评测 CLI 位于 `apps/api/src/evaluation/cli.py`。

**Structure Decision**: 采用一个仓库、API 与 Web 两个应用目录，领域和 Provider 通过
端口隔离；测试按单元/契约/集成/E2E/评测分层。方案文档与实现文档均位于 Feature 目录，
便于从需求追踪到任务和验证。

## Requirement-to-design-to-test traceability

每条需求和成功标准都有独立落点；实现阶段再由 `speckit-tasks` 将测试 ID 展开为任务。

| ID | Design artifact / section | Planned verification |
|---|---|---|
| FR-001 | HLD UI/deployment；HTTP contract | `E2E-001`, `OPS-001` 在线探活 |
| FR-002 | HLD context；LLD session policy | `INT-001`, `E2E-002` 会话隔离 |
| FR-003 | HLD UI；HTTP/SSE contract | `E2E-003` loading/stream/error |
| FR-004 | HLD answer policy；LLD composition | `UNIT-STYLE-001` 中文/称呼 |
| FR-005 | HLD design goals；LLD answer policy | `UNIT-STYLE-002` 中立官方语气 |
| FR-006 | HLD UI；LLD composition | `E2E-004` 结构、加粗和简洁 |
| FR-007 | HLD trust boundary；HTTP contract | `CONTRACT-API-001` 内部字段不泄露 |
| FR-008 | HLD scope；LLD intent mapping | `EVAL-A-I-*` 核心/扩展题型 |
| FR-009 | LLD parsing/entity resolution；data model | `UNIT-PARSE-001` 槽位和歧义 |
| FR-010 | LLD time policy；data model | `UNIT-TIME-001` UTC+8 跨日 |
| FR-011 | LLD SeasonClock；research | `UNIT-TIME-002` `YYYY-YY` 映射 |
| FR-012 | LLD latestness rule；Provider contract | `EVAL-C-002` 最新冠军核验 |
| FR-013 | HLD data-source strategy；provider contract | `CONTRACT-PROVIDER-001` 公网/fixture |
| FR-014 | LLD verification；data model | `EVAL-A/B-001` 可靠事实 |
| FR-015 | LLD derivation | `UNIT-DERIVE-001` 逐场累计 |
| FR-016 | LLD PBP algorithm | `UNIT-DERIVE-002` 最后 5 秒 |
| FR-017 | LLD premise verification | `INT-CORRECT-001` 错误前提纠正 |
| FR-018 | LLD analysis composition | `EVAL-F/G-001` 事实/推断分层 |
| FR-019 | HLD Safety Guard；LLD safety policy | `SEC-REDLINE-001..010` 检索前拦截 |
| FR-020 | LLD refusal template | `SEC-REDLINE-011` 1–2 句拒答 |
| FR-021 | LLD allow boundary | `SEC-ALLOW-001` 合规预测不误拦截 |
| FR-022 | LLD error/retry；provider contract | `INT-ERROR-001..006` timeout/429/空 |
| FR-023 | HLD observability；LLD telemetry | `OPS-TELEM-001` 脱敏和 provider/cache=0 |
| FR-024 | HLD delivery；evaluation contract | `DOC-001` 方案 PDF 清单 |
| FR-025 | Quickstart；evaluation contract | `DOC-002`, `EVAL-RUN-001` 可复现 |
| FR-026 | Evaluation contract | `EVAL-REPORT-001` 七维汇总 |
| FR-027 | HLD highlights projection；HTTP contract；Web Demo | `CONTRACT-HIGHLIGHTS-001`, `E2E-HIGHLIGHTS-001` 日期/空状态/隔离 |
| SC-001 | HLD deployment；quickstart | `OPS-001` 公网 URL/HTTPS 探活 |
| SC-002 | Evaluation contract | `EVAL-COVERAGE-001` A–I 覆盖报告 |
| SC-003 | HLD multi-turn；LLD context | `EVAL-H-001` 三轮一致 |
| SC-004 | HLD safety flow；LLD telemetry | `SEC-REDLINE-012` provider/cache calls=0 |
| SC-005 | LLD verifier/composer | `EVAL-D-001` 纠偏与无编造 |
| SC-006 | Evaluation contract | `EVAL-REPORT-002` 权重/零分/重复 |
| SC-007 | HLD performance target | `EVAL-LATENCY-001` TTFT/完整时延 |
| SC-008 | Spec golden target；evaluation contract | `EVAL-ACCURACY-001` ≥10 客观题、80% 一致 |
| SC-009 | Quickstart/config contract | `DOC-003` clean-environment run |
| SC-010 | HLD observability；HTTP contract | `OPS-TELEM-002` 时间/状态可审计 |
| SC-011 | HLD highlights projection；quickstart | `CONTRACT-HIGHLIGHTS-001`, `E2E-HIGHLIGHTS-001` |
| ARCH-HERMES-001 | HLD §5.1；LLD §1.3/§4.4 runtime boundary | `SEC-HERMES-001`, `INT-HERMES-001` |
| ARCH-CAPACITY-001 | HLD §9.2；LLD §3.1/§10 admission budget | `CAP-ADMISSION-001`, `E2E-SSE-001` |
| ARCH-FAILURE-001 | HLD failure matrix；LLD §10 errors/cancellation | `CHAOS-UPSTREAM-001`, `INT-CANCEL-001` |
| ARCH-DEPLOY-001 | HLD §9 profiles/health/drain | `OPS-HEALTH-001`, `OPS-DRAIN-001` |
| ARCH-OBS-001 | HLD observability；LLD §11 telemetry | `OPS-TELEM-001`, `SEC-NO-EGRESS-001` |

## Constitution Check — after design

| Gate | Result | Evidence |
|---|---|---|
| Specification-first | PASS | Every design section links to FR/SC; no behavior silently added |
| Evidence-first facts | PASS | `FactAssertion` requires evidence; deterministic derivation precedes composition |
| Safety before retrieval | PASS | State machine and provider-call=0 contract explicitly enforce ordering |
| Contract/test-first | PASS | Versioned HTTP, SSE, Provider and Evaluation contracts plus test matrix |
| Observable/reproducible | PASS | Redacted telemetry, fixture mode, clock injection and quickstart |

**Fixture MVP gate: PASSED.** The local fixture path (including the shared sync/SSE envelope,
core safety/fact/context flow, and quickstart) is runnable and reproducible.

**Final delivery gates: PENDING.** `tasks.md` still tracks integration/evaluation coverage,
deployment/public URL evidence, and the final solution PDF. These pending delivery gates do not
block the local fixture MVP. Provider and hosting choices remain explicitly replaceable decisions,
not hidden assumptions.

## Complexity Tracking

无 Constitution 违规项。Provider Gateway、确定性 Derivation 和 Evaluation Runner 是为
PDF 的联网事实、逐回合核验、安全否决和重复评测要求所必需；每项均有独立契约和测试。
Hermes-lite 是可关闭的可选表达运行时，增加 sidecar/capability self-test、准入和回退
契约的理由是验证首版开发速度收益，同时防止通用 Agent 能力破坏安全/事实不变量；其
embedded 模式仅限 fixture Spike，生产可回滚到 `template` 而不改变领域层。
