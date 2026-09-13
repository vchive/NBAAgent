# Implementation Plan: 种花 Agent

**Branch**: `001-nba-chat-agent` | **Date**: 2026-09-13 | **Spec**: [spec.md](spec.md)

**Input**: 将现有中文赛事问答产品迁移为“种花 Agent”，复用其会话、受控 Agent、在线检索、认证、SSE 和安全输出基础设施，并按原方案 PDF 的证据优先工程边界实现花卉领域。

## Summary

默认产品域改为花卉园艺。同步和 SSE 请求先经过本地花卉安全检查，再解析植物、地点、光照、容器、季节和症状，并写入同一逻辑会话。普通模式从带别名的离线花卉知识库生成完整、保守的建议；全智能模式把用户原问题、最近四个完整回合和已确认的种植上下文交给一个封闭的 Agent 运行时，该运行时只能调用 `flower_lookup`、`care_plan` 和 `flower_search`。搜索结果是经过清洗的补充观察，不能把疑似病害或化学建议升级为确定事实。模型或搜索不可用时保留可用的本地答案，并以供应商无关的 notice 告知用户。

现有 NBA 代码只作为显式兼容域保留；默认页面、默认路由和助手自述均为种花 Agent，且不会主动加载赛事数据。服务默认绑定回环地址，完成代码和测试后仍保持停止状态。

## Technical Context

**Language/Version**: Python 3.12；浏览器端 ES2022、HTML5、CSS3

**Primary Dependencies**: FastAPI、Pydantic v2、httpx、Uvicorn、锁定版本的 `hermes-agent==0.19.0`

**Storage**: 进程内 TTL 会话与搜索缓存；版本化 Python 花卉知识目录；原 SQLite 赛事索引仅供显式 NBA 兼容域使用，不承载精确住址或原始对话

**Testing**: pytest、pytest-asyncio、Ruff、Playwright

**Target Platform**: Linux 容器和现代桌面/移动浏览器；默认只监听 `127.0.0.1`

**Project Type**: 单仓库 Web 应用（FastAPI API + 零构建静态前端）

**Performance Goals**: 本地知识回答在正常机器上 1 秒内完成；搜索/模型成功、超时、空结果与额度错误均在请求总 deadline 10 秒内形成完整结果或明确提示；32 个并发请求和 100 条 SSE 连接不串会话

**Constraints**: 离线可用；用户问题最长 2,000 字；Agent 最多 4 次工具调用；搜索只能调用固定适配器和固定端点；不得暴露 URL、供应商、模型、提示词、工具名、密钥或内部 ID；危险化学/误食请求必须在任何检索或模型调用之前短路

**Scale/Scope**: 首版覆盖常见家庭开花植物与 10 类园艺意图；单实例 32 个在途请求、100 个 SSE 连接；每会话保留最近 4 个完整问答回合及 8 条有界种植观察

## Constitution Check

*GATE: Phase 0 前和 Phase 1 后均通过。*

| 原则 | 设计门槛 | 结果 |
|---|---|---|
| I. Specification-First Delivery | 行为先写入 005 spec，并保持 FR/SC 可追踪 | PASS |
| II. Evidence-First Gardening Guidance | 本地知识、搜索观察、可能原因和确定事实分层 | PASS |
| III. Plant, Human and Environmental Safety | 检索前短路混药、误食、暴露和不当处置 | PASS |
| IV. Contract- and Test-First Engineering | HTTP/SSE、运行时、搜索适配器均有契约和失败测试 | PASS |
| V. Observable, Reproducible and Simple Operations | 离线默认、隐私遥测、固定工具集、回环部署 | PASS |

Phase 1 复核：contracts 明确了本地知识与在线资料的证据级别；data model 不保存精确住址、密钥或无界转录；quickstart 包含离线、安全、上下文、故障和并发验证。无宪章豁免。

## Architecture

```text
Browser (flower UI)
  -> POST /api/v1/chat or /api/v1/chat/stream
  -> DomainRouter (flower by default; explicit NBA compatibility only)
  -> FlowerChatUseCase
       -> FlowerSafetyGuard                  # pre-retrieval hard stop
       -> FlowerParser + GardenContext       # bounded logical memory
       -> FlowerAssistantCore                # complete offline answer
       -> optional bounded Agent             # full-intelligence only
            -> flower_lookup / care_plan / flower_search
            -> curated knowledge / fixed search adapters
       -> public output cleaning + wire validation
  -> provider-neutral answer, context summary, notices and evidence state
```

The deterministic flower core is not a canned error fallback: it is a complete offline capability and the authoritative safety floor. The Agent may improve phrasing, choose tools and synthesise long-tail context, but it cannot obtain shell, filesystem, browser, arbitrary URL, MCP, native memory, skills or delegation. Conversation continuity is supplied by the application as four bounded complete turns; facts and safety are re-evaluated on every request.

## Project Structure

### Documentation (this feature)

```text
specs/005-flower-agent/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   ├── agent-runtime.md
│   └── search-provider.md
└── tasks.md
```

### Source Code (repository root)

```text
apps/api/src/
├── api/                         # public sync/SSE schemas and routes
├── application/
│   ├── domain_router.py
│   ├── flower_chat_use_case.py
│   ├── flower_knowledge.py
│   ├── flower_parser.py
│   ├── flower_safety.py
│   └── flower_service.py
├── domain/
│   ├── flower.py
│   └── safety.py
├── infrastructure/
│   ├── agent_tools.py
│   ├── hermes_agent_runtime.py
│   └── session_store.py
└── providers/                   # fixed-host search adapters

apps/web-demo/
├── index.html
├── app.js
├── api-client.js
└── styles.css

tests/
├── unit/
├── contract/
├── integration/
├── evaluation/
└── e2e/
```

**Structure Decision**: 保留现有单仓库分层结构，不新增服务或前端框架。花卉领域位于独立 domain/application 模块；共享 HTTP/SSE、会话、认证和受控运行时通过组合根接入，旧赛事模块不进入花卉默认路径。

## Delivery Phases

1. 建立花卉模型、知识库、解析器、安全规则和完整离线回答。
2. 在组合根中引入默认花卉路由，泛化受控 Agent 与三个花卉工具。
3. 复用固定搜索适配器，加入花卉域标记、清洗、超时/额度错误和本地降级。
4. 将静态 UI、公开文案、配置、包名与部署默认值迁移为种花 Agent。
5. 建立单元、契约、集成、浏览器、黄金题集与并发测试，输出测试报告。

## Traceability

| Requirement | Design | Primary verification |
|---|---|---|
| FR-001, SC-005 | DomainRouter + flower-only first paint | UI/HTTP contract + Playwright |
| FR-002, FR-004, SC-001 | curated profiles + FlowerAssistantCore | unit/evaluation golden set |
| FR-003, SC-002 | GardenContext + bounded app-owned history | multi-turn integration |
| FR-005 | symptom differential renderer | diagnosis safety tests |
| FR-006 | domain-tagged fixed search adapters | search contract tests |
| FR-007, SC-003 | typed public notices + local answer preservation | failure-path integration |
| FR-008 | closed flower runtime manifest | runtime contract tests |
| FR-009, SC-004 | FlowerSafetyGuard before search/Agent | call-counter safety tests |
| FR-010 | runtime sanitiser + wire guard | adversarial output tests |
| FR-011 | one ChatResult projected to sync/SSE | contract parity/idempotency tests |
| FR-012 | loopback compose/uvicorn defaults | configuration tests |
| FR-013 | hashed, content-free telemetry | telemetry privacy tests |
| FR-014, SC-006 | four test layers + concurrency cases | full regression/report |

## Complexity Tracking

No constitution violations require justification. Keeping the legacy NBA vertical is a temporary, explicit compatibility boundary; it does not alter the default product, toolset or UI and can be removed in a later migration after old links and tests are retired.
