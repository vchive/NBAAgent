# NBA Chat Agent — Low-Level Design (LLD)

**Feature**: [spec.md](spec.md)
**HLD**: [hld.md](hld.md)
**Status**: Proposed
**Date**: 2026-08-26

本文把 HLD 的组件落到可实现的模块、类型、状态、协议和测试。字段名是内部契约示例；
除明确标注为用户字段的内容外，不得原样回传浏览器。

## 1. Implementation baseline

### 1.1 Runtime choices

首版实现采用以下可替换基线：

- API/domain/evaluation：Python 3.12、FastAPI/ASGI、Pydantic v2、httpx。
- Web：TypeScript、React/Next.js；POST SSE 由 `fetch()` + `ReadableStream` 客户端读取。
  原生 `EventSource` 仅支持 GET，不用于本契约。
- 测试：pytest（单元、契约、集成）、Playwright（E2E）。
- 本地运行：Docker Compose；无凭据时使用 fixture/mock provider 和 mock composer。

这些选择服务于快速交付和可测试性，不是 PDF 的强制技术栈。所有外部依赖均通过端口
（Protocol）隔离。

### 1.2 Package layout

```text
apps/api/src/
├── api/
│   ├── http_routes.py              # /healthz, /api/v1/chat
│   ├── sse_routes.py               # /api/v1/chat/stream
│   └── schemas.py                  # request/response wire schemas
├── application/
│   ├── chat_use_case.py            # sync/SSE 共用的用例
│   ├── orchestrator.py             # 状态机与阶段事件
│   └── ports.py                    # Safety/Context/Provider/Composer ports
├── domain/
│   ├── entities.py                 # Game, Player, Team, PlayEvent, Evidence
│   ├── value_objects.py            # Season, TimeContext, QueryIntent
│   ├── policies.py                 # style, safety, freshness policies
│   ├── verifier.py                 # fact and premise verification
│   └── derivation.py               # deterministic aggregations
├── providers/
│   ├── espn_adapter.py             # public web adapter
│   ├── fallback_adapter.py         # optional secondary source
│   ├── normalizer.py
│   └── router.py
├── infrastructure/
│   ├── http_client.py               # allow-list, timeout, retry, circuit breaker
│   ├── cache.py
│   ├── session_store.py
│   ├── model_composer.py
│   └── telemetry.py
└── evaluation/
    ├── runner.py
    ├── golden_cases.jsonl
    └── report.py

apps/web/
├── app/chat/page.tsx
├── components/{ChatWindow,Message,Status,FactCard,ErrorState}.tsx
└── lib/chat-client.ts

tests/{unit,contract,integration,e2e,evaluation}/
```

## 2. Domain value objects and schemas

### 2.1 Query and context

请求、意图和会话的 canonical 字段唯一以 [data-model.md](data-model.md) §1/§4 为准。
实现层只增加传输元数据（服务端生成的 `request_id`、trace 和幂等键），不得复制或改名
领域字段。`clock_window` 默认 `scope=GAME_END`；用户明确某一节时使用 `PERIOD_END`。

`category` 对应参考题型：A 单场/球员数据、B 赛程赛果、C 历史纪录、D 事实纠偏、E
逐回合关键球、F 战术/假设、G 主观复盘、H 多轮追问、I 安全拦截。I 只表示被拦截的
安全类别；未知题型使用 `OUT_OF_SCOPE`。

`category` 是评测标签，`intent_name` 是唯一的内部路由字段，固定映射如下：

| category | intent_name | 主要数据能力 |
|---|---|---|
| A | `DATA` | 球员/球队/单场统计 |
| B | `SCHEDULE_RESULT` | 赛程、赛果、排名 |
| C | `HISTORY` | 历史纪录、冠军 |
| D | `FACT_CHECK` | 前提核验、纠偏 |
| E | `PLAY_BY_PLAY` | 逐回合关键球 |
| F | `TACTICAL` | 有证据的战术假设 |
| G | `RECAP` | 有证据的主观复盘 |
| H | `FOLLOW_UP` | 会话上下文追问 |
| I | `SAFETY` | 检索前拒答 |

### 2.2 Canonical entities

完整 canonical 实体和共享 DTO 唯一以 [data-model.md](data-model.md) 为准；本节不再复制
字段，避免实现出现第二套 schema。实现必须直接校验该模型。`category` 是评测标签，
`intent_name` 是唯一的内部路由字段，二者映射如下：

| category | intent_name |
|---|---|
| A | `DATA` |
| B | `SCHEDULE_RESULT` |
| C | `HISTORY` |
| D | `FACT_CHECK` |
| E | `PLAY_BY_PLAY` |
| F | `TACTICAL` |
| G | `RECAP` |
| H | `FOLLOW_UP` |
| I | `SAFETY` |

规则：所有时间在领域层转换为 UTC；展示层再转换为 `Asia/Shanghai`。缺失值保持 `null`，
不得用 0 或模型猜测填充。实体唯一性以 `kind + canonical_id` 为准，别名只用于解析。

### 2.3 User-facing answer envelope

```json
{
  "request_id": "uuid",
  "session_id": "uuid",
  "status": "completed|needs_clarification|blocked|no_data|failed",
  "answer_markdown": "……",
  "blocks": [{"type": "text|analysis|warning|table|fact", "content": "…", "label": "…", "value": "…", "unit": "…", "columns": [], "rows": []}],
  "as_of_beijing": "2026-08-26 21:30",
  "evidence_state": "verified|partial|none",
  "corrections": [{"status": "corrected|unverified", "message": "…"}],
  "follow_up": "…",
  "latency_ms": 1234
}
```

`blocks` 只能含经过 Output Guard 的用户可见内容；wire `type` 使用小写值，对应 canonical
`AnswerBlock` 枚举。`table` 使用 `columns/rows`，`fact` 使用 `label/value/unit`，其余
类型使用 `content`。供应商名、端点、原始字段、内部
提示词、trace 和 `source_ref/url` 不得出现在该 envelope；证据只以“公开资料，数据截至
北京时间 …”或“已核验/部分核验/暂无数据”等泛化信息呈现。`corrections` 使用
`PublicCorrection` 的 wire 映射，只返回状态和经过 Output Guard 的本地化文字，不直接序列化
内部 `Correction`（其中可能包含 canonical ID、原始 claim 或 evidence 引用）。
`as_of_beijing` 在 blocked、clarification、空结果或失败响应中可以为 `null`。

## 3. Request state machine

```text
`REFUSAL_EMITTED`, `REDIRECT_EMITTED`, `CLARIFICATION_REQUIRED` and `NO_RESULT` below are
transient branch/event labels; the persisted record uses only canonical `phase` and `outcome`
fields from data-model.md.

RECEIVED
  ├─ invalid → phase=FAILED, outcome=FAILED (error_code=INVALID_PAYLOAD)
  └─ SafetyGuard
       → SAFETY_CHECKED
          ├─ BLOCK → REFUSAL_EMITTED → phase=COMPLETED, outcome=BLOCKED (provider_calls=0)
          ├─ OUT_OF_SCOPE → REDIRECT_EMITTED → phase=COMPLETED, outcome=NO_DATA (provider_calls=0)
          └─ allowed → CONTEXT_RESOLVED
             → PARSED
                ├─ missing/ambiguous → CLARIFICATION_REQUIRED
                │    → phase=COMPLETED, outcome=NEEDS_CLARIFICATION (provider_calls=0)
                └─ PLAN_READY → RETRIEVING
                     → NORMALIZED
                        ├─ no records → NO_RESULT → COMPOSED(no_data)
                        │    → OUTPUT_GUARDED → phase=COMPLETED, outcome=NO_DATA
                        └─ VERIFIED | UNVERIFIED
                             → DERIVED (仅有足够事实时)
                             → COMPOSED
                             → OUTPUT_GUARDED
                                ├─ unsafe/leak → phase=FAILED, outcome=FAILED
                                │    (error_code=OUTPUT_BLOCKED)
                                └─ safe → phase=COMPLETED, outcome=COMPLETED
```

所有状态转移写入内部 telemetry；向 SSE 暴露的仅是友好阶段文本，例如“正在核对比赛
数据”。客户端断开时取消下游 HTTP/模型任务，并记录 `CLIENT_DISCONNECTED`，不改变已
保存的会话事实。

## 4. Ports and provider protocols

### 4.1 Application ports

```python
SafetyPort.classify(text: str) -> SafetyDecision
ContextPort.load(session_id: UUID) -> ConversationContext | None
ContextPort.save(context: ConversationContext) -> None
ProviderPort.search_games(filters: GameFilters) -> ProviderResult[list[Game]]
ProviderPort.get_game_summary(game_id: str) -> ProviderResult[GameBundle]
ProviderPort.get_play_by_play(game_id: str) -> ProviderResult[PlayByPlayBundle]
ProviderPort.get_player_stats(query: StatsQuery) -> ProviderResult[list[StatLine]]
ProviderPort.get_team_stats(query: StatsQuery) -> ProviderResult[list[StatLine]]
ProviderPort.get_standings(season: SeasonLabel) -> ProviderResult[list[Standing]]
ProviderPort.get_history(query: HistoryQuery) -> ProviderResult[list[HistoryRecord]]
ProviderPort.search_news(query: NewsQuery) -> ProviderResult[list[NewsItem]]
ComposerPort.compose(facts: FactBundle, intent: QueryIntent) -> DraftAnswer
```

Provider 方法只接收 typed filters，不接收用户任意 URL，防止 SSRF。只允许 GET/幂等请求
自动重试；400/401/403 不重试，429/5xx 使用有上限的指数退避和熔断。

### 4.2 ProviderResult

```text
ProviderResult[T]
  data: T | None
  evidence: list[Evidence]
  partial: bool
  error: ProviderError | None
  retrieved_at_utc: Instant

ProviderError
  kind: TIMEOUT | RATE_LIMITED | AUTH | HTTP | INVALID_JSON | SCHEMA_MISMATCH | NOT_FOUND
  retryable: bool
  safe_message: string
  retry_after_seconds: int | None
```

`StatsQuery`、`HistoryQuery` 和 `HistoryRecord` 的 canonical 字段见 [data-model.md](data-model.md)。

Normalizer 将不同来源映射到 canonical entities；不识别的字段丢弃并记录 schema warning，
不填默认事实。高风险事实（纠偏、冠军/最新性、PBP）至少需要一份高可信证据；若来源
冲突或只有低可信资料，返回 `PARTIAL/UNVERIFIED` 并禁止确定性断言。

### 4.3 ESPN adapter mapping (baseline)

适配器默认使用公开 Web API（具体 URL 仅在 adapter 内部配置）：

| 能力 | 输入 | 归一化输出 |
|---|---|---|
| scoreboard | 北京日期转换后的源日期、球队/赛季过滤 | `Game[]` |
| summary | `game_id` | `GameBundle`、box score、leaders、series context |
| play-by-play | `game_id` | `PlayByPlayBundle`，含 events、period/clock/participants/score |
| roster/athlete | entity 查询 | `Player`/`Team` profile |
| season stats | entity + season/scope | `StatLine[]`（subject kind 为 PLAYER 或 TEAM） |

适配器必须带 `User-Agent`、超时和响应大小上限，保存 fixture 时去除凭据和不必要原始内容。
Provider 健康检查只验证允许的端点，不把上游 URL 发送给用户。

## 5. Parsing, time and entity resolution

1. `SafetyGuard` 先对原始文本做本地规则词典（首版必选）+可选本地分类器判定；首版
   不调用远程分类服务。未来若引入远程分类器，必须单独经过隐私审查，并明确其不属于
   sports Provider 调用、不能发送未脱敏敏感文本。
2. `IntentParser` 识别 A–I、客观/纠偏/分析模式、实体、指标、日期、赛季、比赛编号、
   节次和时钟窗口，并返回置信度。
3. `ContextResolver` 仅从当前 `session_id` 的 `ConversationContext` 补全“那场/最后那个球”
   等省略表达；若无唯一活动实体，生成澄清问题。
4. `SeasonClock` 接收可注入 `Clock`：
   - 首轮有效 `client_timezone` 写入当前 session；后续省略时沿用该 session timezone，新的
     session 缺省为 `Asia/Shanghai`。该时区用于解析“今天”等相对日期；解析出的
     区间立即转换为 UTC，赛季和用户输出仍以北京时间/官方赛历为准；
   - 10 月至次年 6 月归入跨年赛季 `YYYY-YY`；官方 active season 可用时，“本赛季/今年”
     选择它；休赛期没有 active season 时，“本赛季”选择即将开始赛季并标注“即将开始”，
     “最近/最近一次夺冠/卫冕冠军”固定查询最近完成的 Finals/赛季；
   - 任何边界规则必须有固定时钟单测。
5. `EntityResolver` 使用规范 ID、别名、球队迁移和年份约束消歧；候选多于一个时不得猜。

## 6. Verification and deterministic derivation

### 6.1 Fact verification

- 检查 evidence freshness、trust、实体/日期/赛季一致性和响应完整性。
- 纠偏问题将 `premise_claims` 与 canonical facts 比较，生成 `Correction`；“无结果”只
  表示无法核实，不等于证明用户错误。
- 对比分、排名、球员/球队统计，优先高可信来源；关键 PBP/纠偏可配置双源交叉核对。
- 任何 `UNVERIFIED` 数值在 Composer 中只能显示“暂无数据/尚待核实”，不能显示猜测。

### 6.2 Derivation algorithms

- **系列赛大比分**：按 `season + series_id + game_id` 去重，只纳入 `FINAL` 且双方为非负
  且不平分的比赛，逐场统计胜者，输出胜场计数和每场依据；延期、取消、LIVE、未知胜者
  跳过并标记 partial；不从最终摘要中的文字直接心算。
- **最近 N 场/连胜**：按比赛开始时间 UTC 排序，过滤 `FINAL`，逐场计算状态；N 无效时
  追问或使用明确默认并说明。
- **累计统计**：仅汇总已核验的 game/series facts；任一必需场次缺失则标记 partial。
- **最后 5 秒关键球**：默认解释为全场结束（含加时）最后 5 秒；若用户明确“第四节/某节”，
  使用 `TimeWindow.scope=PERIOD_END`。先从完整 `PlayByPlayBundle` 确定窗口节次，再将事件的
  节次时钟解析为“该节剩余秒数”并按闭区间
  `clock_seconds_remaining ∈ [start_seconds, end_seconds]` 筛选：`GAME_END` 只取完整 bundle
  中最大的 `period`（常规第 4 节或最后一个加时），`PERIOD_END` 只取用户指定节；“每节最后
  5 秒”显式为每个节次生成一个 `PERIOD_END` 窗口，不跨节拼接。若无法从完整 bundle 确定
  最终节次，返回 `NO_DATA/PARTIAL`，不得退回上一节。筛选后若 `sequence_valid=true`，按
  `(period ASC, sequence ASC, provider_index ASC)` 排序；否则按
  `(period ASC, clock_seconds_remaining DESC, provider_index ASC)` 排序。`sequence_valid=true`
  要求相关事件的 sequence 非空且可用，`provider_index` 始终作为稳定 tie-breaker。常规节和
  加时保留原始 `period` 编号，0 和 5.0 秒均包含；缺少出手者、参与者、得分值或比分的事件
  可以列出，但不能单独形成已核实的关键球断言。
- **分析理由**：Composer 只能引用 `VERIFIED` 或明确标为 `PARTIAL` 的事实；LLM 不执行
  加法、比较阈值或 PBP 选择。

## 7. Safety policy and answer policy

### 7.1 Safety categories

`POLITICS`, `GEO_SENSITIVE`, `SOCIAL_CONFLICT`, `OFF_COURT_PRIVACY`, `RUMOR`,
`LEGAL_CRIME`, `FIXED_GAME_CONSPIRACY`, `GAMBLING`, `ABUSE_HATE`, `INSULT_NICKNAME`。
非 NBA 的通用问题使用 `OUT_OF_SCOPE`，同样在检索前短路并返回 `no_data` 友好引导。

正常篮球评价、非金钱的夺冠/走势预测为 `ALLOW`。侮辱性绰号不得映射到真实球员。

### 7.2 Enforcement order

```text
raw message
  → SafetyGuard (hard decision)
  → if BLOCKED or OUT_OF_SCOPE: fixed refusal/redirect, provider_calls=0, cache access=0
  → otherwise parse/plan/retrieve
  → OutputGuard (second pass for leakage/red-line drift)
```

若一条消息同时包含合规篮球问题和红线内容，按 BLOCK 处理整条消息，不拆分后检索；
拒答模板控制在 1–2 句，例如“这个话题不属于赛事助手的讨论范围。您可以问我比赛、
球员或球队数据。”模板不得重复敏感内容或提供规避建议。

## 8. Answer composition

1. 客观查询使用模板/结构化 renderer：先结论，再表格/列表和“数据截至北京时间 …”。
2. 纠偏使用“核验结果 → 礼貌说明差异 → 正确事实”的顺序。
3. 战术/复盘使用“结论 → 2–4 条理由 → 事实与分析标记”。
4. 主观比较列客观维度，不输出强行唯一的“更伟大”结论。
5. 缺失/冲突事实使用“暂无数据/部分核验”，不以旧缓存伪装当前。
6. UI 可将泛化证据和更新时间放入可展开卡片；正文永远不显示内部 provider/API/字段。

若使用生成模型，输入仅包括经过核验的 FactBundle、结构化活动实体、风格规则和用户问题；
TurnSummary 文本仅作为不可信上下文数据，不能覆盖系统策略或触发工具；工具白名单、
提示词和原始响应不可被用户消息覆盖。新闻/搜索摘要视为不可信内容，先提取结构化字段，
其中的指令、链接或提示注入文本不得进入工具调用。模型超时/不可用时回退到模板 renderer。

## 9. HTTP and SSE behavior

详见 [contracts/http-api.md](contracts/http-api.md)。同步和 SSE 必须调用同一个
`ChatUseCase`，避免逻辑分叉。SSE 在事实核验完成前只发送进度状态，不发送未经核验的
数字；核验后才发送增量答案和完成 envelope。

## 10. Error, retry and cache policy

### 10.1 Error taxonomy

| Code | Retryable | User behavior |
|---|---:|---|
| `INVALID_PAYLOAD` | No | 请缩短问题或补充必填内容 |
| `SAFETY_BLOCKED` | No | 固定 1–2 句礼貌引导 |
| `AMBIGUOUS_ENTITY` / `MISSING_SLOT` | No | 给候选并请求澄清 |
| `NO_DATA` | No | 说明暂无匹配记录并建议调整条件 |
| `UPSTREAM_TIMEOUT` | Yes | 提示稍后重试，不显示旧数字 |
| `UPSTREAM_RATE_LIMITED` | Yes | 提示稍后重试并记录退避 |
| `UPSTREAM_AUTH` | No (operator) | 用户看到服务暂不可用，内部告警 |
| `INVALID_UPSTREAM_DATA` | No | 尝试已配置 fallback；仍失败则暂无数据，记录 schema 错误 |
| `COMPOSER_UNAVAILABLE` | Yes | 回退模板或稍后重试 |
| `OUTPUT_BLOCKED` | No | 通用安全错误，不泄露规则 |

`SAFETY_BLOCKED`、`AMBIGUOUS_ENTITY`、`MISSING_SLOT` 和 `NO_DATA` 是内部的会话结果码，
对应 HTTP 契约中的 `blocked`、`needs_clarification` 或 `no_data`（HTTP 200），不是技术
失败的 error envelope；其余表项才进入 `status=failed`。

### 10.2 Cache and sessions

- SafetyGuard 必须在任何 Provider/cache lookup 之前完成；BLOCKED 或 OUT_OF_SCOPE 分支不读写
  Provider 缓存（`cache_read_count=cache_write_count=0`）。
- cache key = `provider + canonical request filters + data scope + season`，不把原始敏感文本
  作为可复用 key。
- 默认 TTL（可配置的工程起点）：实时赛程/比分 30–60 秒、box score 5 分钟、历史资料
  24 小时。响应携带 freshness；过期数据只能作背景，不能回答“当前”。
- session store 只保存最近有限轮次的摘要和活动实体，默认 TTL 24 小时；同一 session 的
  用例按 session lock 串行更新 `turn_count`/active refs，或使用 optimistic version 检测
  冲突后重试；不同 session 可并行。不保存凭据，
  安全命中的原始敏感文本只保留哈希/类别。
- 同一 session 的并发请求按 `(session_id, client_message_id)` 在编排开始前原子 reserve；
  重复请求若仍 in-flight 则等待或返回可重连状态，完成后复用 envelope；不同 session 永不
  共享上下文。

## 11. Observability and privacy

每次请求生成 `request_id`/`trace_id`，记录状态转移、intent、session hash、provider call
count、cache read/write count、cache hit、evidence state、TTFT、total latency、error code
和 `safety_category`。红线拒答必须有可验证的 `provider_call_count=0` 且缓存读写计数均为
0。日志进行文本截断和敏感字段脱敏，凭据通过 secret manager/environment 注入；日志留存
期限由部署环境配置。

## 12. Test design

| 层级 | 重点 | 必须断言 |
|---|---|---|
| Unit | 时区/赛季、别名、SafetyGuard、Normalizer、Derivation、OutputGuard | 边界时刻、缺字段不填 0、红线短路、PBP 选择正确 |
| Contract | HTTP JSON、SSE 顺序、Provider fixture、错误码 | schema 版本、隐藏内部字段、retryable 标记 |
| Integration | Orchestrator 全链路 | 成功、澄清、空结果、timeout/429、错误前提、session 隔离 |
| E2E | Web 聊天 | 响应式 UI、加载/流式/断开/重试、键盘可用、卡片/表格可读 |
| Evaluation | A–I + OUT_OF_SCOPE 黄金题和重复回放 | 事实、时区、三轮一致、安全/范围外 provider=0、七维评分和耗时 |

黄金集至少包含每类一题，并增加边界/红队变体；每个客观答案保存参考实体、日期、
关键数值和允许容差。评测同时记录首 token（若流式）和完整答案的起止时间。

## 13. Configuration contract

```text
APP_ENV=local|staging|production
API_BASE_URL=...
PUBLIC_DATA_MODE=live|fixture|hybrid
PROVIDER_TIMEOUT_SECONDS=8
PROVIDER_MAX_RETRIES=2
CACHE_TTL_LIVE_SECONDS=45
CACHE_TTL_BOXSCORE_SECONDS=300
CACHE_TTL_HISTORY_SECONDS=86400
SESSION_TTL_SECONDS=86400
LLM_MODE=mock|live
LLM_TIMEOUT_SECONDS=8
ALLOWED_ORIGINS=...
LOG_LEVEL=INFO
```

`.env` 只作为本地未提交文件；仓库只提交 `.env.example`，其中不得包含真实凭据。

## 14. Requirement and test ID mapping

完整的一对一追踪矩阵维护在 [plan.md](plan.md) 的 “Requirement-to-design-to-test
traceability” 小节；该矩阵是实现和代码审查的唯一任务 ID 来源。下面列出 LLD 模块到
测试层的边界，供任务拆分复用：

| LLD module | Unit | Contract | Integration/E2E | Evaluation |
|---|---|---|---|---|
| SafetyGuard / OutputGuard | safety category、模板、泄露规则 | blocked envelope、provider=0 telemetry | orchestrator short-circuit | I 红线题 |
| SeasonClock / EntityResolver | UTC+8、赛季、别名消歧 | QueryIntent schema | multi-turn context | B/C/H 时间题 |
| Provider / Normalizer | null/字段映射、重试策略 | provider fixtures、错误码 | live/fixture gateway | A/B/C/E 客观题 |
| Verifier / Derivation | 前提纠偏、系列赛、PBP | FactBundle/Evidence schema | partial/conflict upstream | D/E 准确性题 |
| Composer / API | 风格、结构、状态 | HTTP/SSE envelope | Web loading/stream/error | F/G 表达题 |
| Telemetry / Evaluation Runner | 脱敏、权重计算 | report schema | repeated replay | 七维评分/时延 |
