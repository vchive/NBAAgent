# NBA Chat Agent — Canonical Data Model

**Feature**: [spec.md](spec.md)
**Detailed design**: [lld.md](lld.md)

本文是跨 API、Provider、评测和会话存储共享的规范模型。字段类型使用逻辑类型表示，
具体序列化遵循 [contracts/http-api.md](contracts/http-api.md)。

## 1. Identity and time value objects

```text
GameStatus = SCHEDULED | LIVE | FINAL | POSTPONED | UNKNOWN
EntityKind = PLAYER | TEAM | GAME | SERIES | SEASON | UNKNOWN
QueryPhase = RECEIVED | SAFETY_CHECKED | CONTEXT_RESOLVED | PARSED | PLAN_READY | RETRIEVING | NORMALIZED | VERIFIED | UNVERIFIED | DERIVED | COMPOSED | OUTPUT_GUARDED | COMPLETED | FAILED
QueryOutcome = COMPLETED | NO_DATA | NEEDS_CLARIFICATION | BLOCKED | FAILED
StatScope = GAME | SERIES | SEASON | CAREER
IntentName = DATA | SCHEDULE_RESULT | HISTORY | FACT_CHECK | PLAY_BY_PLAY | TACTICAL | RECAP | FOLLOW_UP | SAFETY | OUT_OF_SCOPE
Category = A | B | C | D | E | F | G | H | I | OUT_OF_SCOPE
SafetyCategory = POLITICS | GEO_SENSITIVE | SOCIAL_CONFLICT | OFF_COURT_PRIVACY | RUMOR | LEGAL_CRIME | FIXED_GAME_CONSPIRACY | GAMBLING | ABUSE_HATE | INSULT_NICKNAME | OUT_OF_SCOPE | ALLOW
SafetyOutcome = ALLOW | BLOCK | OUT_OF_SCOPE
EvidenceState = VERIFIED | PARTIAL | NONE
CorrectionStatus = CORRECTED | UNVERIFIED
AnswerBlockType = TEXT | ANALYSIS | WARNING | TABLE | FACT
HistoryRecordType = CHAMPIONSHIP | FRANCHISE_RECORD | LEAGUE_RECORD | SERIES_RECORD
EvaluationProviderMode = LIVE | FIXTURE | HYBRID
ErrorCode = INVALID_PAYLOAD | SAFETY_BLOCKED | AMBIGUOUS_ENTITY | MISSING_SLOT | NO_DATA | SERVICE_BUSY | UPSTREAM_TIMEOUT | UPSTREAM_RATE_LIMITED | UPSTREAM_AUTH | INVALID_UPSTREAM_DATA | COMPOSER_UNAVAILABLE | OUTPUT_BLOCKED
AdmissionResult = ADMITTED | RATE_LIMITED | QUEUE_FULL | DEADLINE_EXCEEDED
RuntimeProfile = TEMPLATE | HERMES | HYBRID
HermesLiteMode = OFF | EMBEDDED_SPIKE | EMBEDDED_AGENT | SIDECAR
CompositionMode = DETERMINISTIC | MODEL | AGENT | FALLBACK
CompositionStatus = NOT_REQUESTED | USED | FALLBACK | DISABLED
```

| Object | Required fields | Rules |
|---|---|---|
| `EntityRef` | `kind`, `canonical_id`, `display_name` | 唯一键为 `kind + canonical_id`；别名不能单独作为事实 ID |
| `SeasonLabel` | `start_year`, `end_year`, `label` | `end_year = start_year + 1`，格式 `YYYY-YY`，如 `2025-26` |
| `TimeContext` | `instant_utc`, `input_timezone`, `display_timezone`, `season`, `relative_phrase` | 存储/比较用 UTC；默认输入/展示 `Asia/Shanghai` |
| `MetricRef` | `name`, `unit`, `scope` | 明确总量/场均/百分比/排名等口径；未知口径不得猜 |

```text
EntityRef
  kind: EntityKind
  canonical_id: string
  display_name: string
  aliases: string[]
  confidence: decimal (0..1)

TimeWindow
  start_seconds: decimal
  end_seconds: decimal
  semantics: PERIOD_CLOCK_REMAINING (inclusive)
  scope: GAME_END | PERIOD_END
  invariant: 0 <= start_seconds <= end_seconds <= 60

DateRange
  start_inclusive: Instant
  end_exclusive: Instant
  invariant: start_inclusive < end_exclusive

StatsQuery
  subject: EntityRef
  scope: StatScope
  season: SeasonLabel?
  game_id: string?
  series_id: string?
  date_range: DateRange?

Claim
  subject: EntityRef
  predicate: string
  claimed_value: scalar|object

Slot
  name: string
  reason: string

Correction
  claim: Claim
  verified_value: scalar|object|null
  status: CorrectionStatus

PublicCorrection
  status: CorrectionStatus
  message: string (1..1000 Unicode characters; localized, user-facing; no canonical IDs, URLs,
    provider names or raw fields)

TurnSummary
  turn_index: int
  user_intent: string
  active_refs: EntityRef[]
  verified_fact_ids: string[]
  text_summary: string (max 2048)

SafetyDecision
  outcome: SafetyOutcome
  category: SafetyCategory
  confidence: decimal (0..1)
  refusal_template_id: string?

DraftAnswer
  markdown: string
  blocks: AnswerBlock[]
  evidence_state: EvidenceState
  corrections: PublicCorrection[] (default [])
  follow_up: string?

FactBundle
  facts: FactAssertion[]
  missing: string[]
  corrections: Correction[]
  evidence_state: EvidenceState

AnswerBlock
  type: AnswerBlockType
  content: string?
  label: string?
  value: scalar?
  unit: string?
  columns: string[]?
  rows: scalar[][]?

SeasonRange
  start_inclusive: SeasonLabel
  end_inclusive: SeasonLabel
  invariant: start_inclusive.start_year <= end_inclusive.start_year

ChatRequest
  session_id: UUID?
  message: string (1..2000 Unicode characters)
  client_timezone: IANA timezone?
  client_message_id: string? (max 128)

QueryIntent
  category: Category
  intent_name: IntentName
  mode: OBJECTIVE | FACT_CHECK | ANALYSIS | SAFETY | OUT_OF_SCOPE
  confidence: decimal (0..1)
  entities: EntityRef[]
  metrics: MetricRef[]
  season: SeasonLabel?
  date_range: DateRange?
  game_number: int?
  period: int?
  clock_window: TimeWindow?
  operation: LOOKUP | AGGREGATE | COMPARE | EXPLAIN
  premise_claims: Claim[]
  missing_slots: Slot[]

ConversationContext
  session_id: UUID
  version: int (non-negative, incremented on successful turn commit)
  timezone: IANA timezone
  turn_count: int
  active_game: EntityRef?
  active_team: EntityRef?
  active_player: EntityRef?
  active_season: SeasonLabel?
  recent_turn_summaries: TurnSummary[] (max 8 turns / 16 KiB)
  expires_at_utc: Instant
```

### Season mapping

赛季以结束年份作为外部 Provider 的 season year 映射，例如领域标签 `2025-26` 对应
Provider season year `2026`。映射逻辑必须通过可注入时钟测试跨北京时间午夜、10 月开季、
次年 6 月季后赛和休赛期。

## 2. Sports entities

### Team and Player

```text
Team
  team_id: string
  name: string
  abbreviation: string?
  aliases: string[]
  alias_history: {alias: string, valid_seasons: SeasonLabel[]?}[]

Player
  player_id: string
  full_name: string
  aliases: string[]
  current_team_id: string?
```

`team_id`/`player_id` 由 Provider 映射为 canonical ID；同名候选必须保留并触发澄清。

### Provider aggregate

```text
GameBundle
  game: Game
  stat_lines: StatLine[]
  series: SeriesRef?
  leaders: StatLine[]
  plays: PlayByPlayBundle?

GameFilters
  date_range: DateRange?
  season: SeasonLabel?
  team_ids: string[]
  status: GameStatus?

NewsQuery
  subject_refs: EntityRef[]
  keywords: string[] (max 8 items, each 1..80 chars; control characters removed)
  date_range: DateRange?
  limit: int (1..20)

NewsItem
  news_id: string
  title: string
  published_utc: Instant?
  subject_refs: EntityRef[]
  summary: string?
  evidence_id: string

HistoryQuery
  subject_refs: EntityRef[]
  season_range: SeasonRange?
  record_type: HistoryRecordType
  limit: int (1..50)

HistoryRecord
  record_id: string
  record_type: HistoryRecordType
  subject: EntityRef?
  season: SeasonLabel?
  value: scalar|object
  evidence_id: string
```

`GameBundle.plays` is an optional embedded PBP snapshot. The dedicated `get_play_by_play`
operation returns the authoritative `PlayByPlayBundle`; a missing embedded snapshot does not imply
that PBP is unavailable.

新闻正文作为不可信外部内容处理：只提取标题、时间、主体和摘要，任何其中的指令文本
不得进入工具调用或覆盖系统策略。

### Game

```text
Game
  game_id: string
  season: SeasonLabel
  start_utc: Instant
  home: EntityRef (kind=TEAM)
  away: EntityRef (kind=TEAM)
  status: SCHEDULED|LIVE|FINAL|POSTPONED|UNKNOWN
  home_score: int?
  away_score: int?
  series_id: string?
  series_game_number: int?
```

唯一性由 `game_id` 保证；比分只有在 `FINAL` 或来源明确标记时才可用于最终赛果。

### Statistics and standings

```text
StatLine
  subject: EntityRef
  game_id: string?
  series_id: string?
  season: SeasonLabel?
  scope: GAME|SERIES|SEASON|CAREER
  metrics: {metric_name: number|null}
  metric_definitions: {metric_name: string}
  evidence_ids: string[]

Standing
  season: SeasonLabel
  team: EntityRef (kind=TEAM)
  conference: string?
  wins: int?
  losses: int?
  rank: int?
  as_of_utc: Instant?
```

统计的数值、单位和统计范围必须成组保存；缺字段为 `null`，不能用 0 代替。

### Series and play-by-play

```text
SeriesRef
  series_id: string
  season: SeasonLabel
  stage: REGULAR|PLAY_IN|PLAYOFF|FINALS
  home: EntityRef (kind=TEAM)?
  away: EntityRef (kind=TEAM)?
```

### Play-by-play

```text
PlayEvent
  event_id: string
  game_id: string
  sequence: int? (provider supplied; null when unavailable)
  provider_index: int (zero-based, unique within the bundle)
  period: int
  clock_seconds_remaining: decimal
  event_type: SHOT|FREE_THROW|FOUL|TURNOVER|REBOUND|SUBSTITUTION|OTHER
  shooter: EntityRef?
  assister: EntityRef?
  shot_type: TWO_POINT|THREE_POINT|FREE_THROW|NONE|UNKNOWN
  points: int? (null when the source omits a scoring value; never substitute 0)
  home_score_after: int?
  away_score_after: int?
  wallclock_utc: Instant?
  raw_text_hash: string?

PlayByPlayBundle
  game_id: string
  events: PlayEvent[]
  sequence_valid: bool (constant for the bundle; true only when every event has a
    non-null, unique usable sequence)
```

先按窗口 scope 从完整 bundle 确定节次，再按
`clock_seconds_remaining ∈ [start_seconds, end_seconds]` 闭区间筛选；`GAME_END` 的最终节次
是常规第 4 节或最后一个加时，`PERIOD_END` 只允许指定节次，无法确定节次时不得回退到上一节。
筛选后若 `sequence_valid=true`，按 `(period ASC, sequence ASC, provider_index ASC)` 排时间线
（该分支不应出现空序号）；否则按
`(period ASC, clock_seconds_remaining DESC, provider_index ASC)`。
缺少出手者、参与者、比分或得分值的事件可保留，但不能作为已核实的关键球断言。

## 3. Evidence and fact graph

```text
Evidence
  evidence_id: string
  source_class: OFFICIAL|ESTABLISHED_SPORTS|NEWS|SEARCH|FIXTURE
  source_ref: string                  # internal, never user-facing
  url: URL                            # internal, never user-facing
  fetched_at_utc: Instant
  data_as_of_utc: Instant?
  trust: HIGH|MEDIUM|LOW
  freshness: FRESH|STALE|UNKNOWN

FactAssertion
  fact_id: string
  subject: EntityRef
  predicate: string
  value: scalar|object|null
  unit: string?
  evidence_ids: string[]
  derived_from_fact_ids: string[]
  verification: VERIFIED|PARTIAL|UNVERIFIED|CONTRADICTED
```

每个用户可见数字必须能沿 `FactAssertion → Evidence` 回溯。内部来源 URL 和字段不可进入
用户响应；用户只见泛化的“公开资料/核验状态”和北京时间截至时间。

## 4. Conversation and evaluation entities

```text
ConversationRecord
  session_id: UUID
  version: int (non-negative, incremented on successful turn commit)
  timezone: IANA timezone
  turn_count: int
  active_refs: EntityRef[]
  turn_summaries: TurnSummary[]
  created_at_utc: Instant
  expires_at_utc: Instant

QueryRecord
  request_id: UUID
  session_id: UUID
  raw_text_hash: string
  intent_category: Category?
  intent_name: IntentName?
  safety_category: SafetyCategory?
  parsed_query: QueryIntent?
  phase: QueryPhase
  outcome: QueryOutcome?
  provider_call_count: int (>=0; default 0)
  cache_read_count: int (>=0; default 0)
  cache_write_count: int (>=0; default 0)
  evidence_state: EvidenceState (NONE until evidence is retrieved)
  ttft_ms: int?
  total_latency_ms: int?
  error_code: ErrorCode?
  admission_result: AdmissionResult?
  queue_wait_ms: int?
  deadline_at_utc: Instant?
  hermes_mode: HermesLiteMode?
  hermes_status: OK|TIMEOUT|UNAVAILABLE|UNSAFE|null
  fallback_reason: string?
  agent_iteration_count: int (>=0; default 0)
  agent_tool_call_count: int (>=0; default 0)
  agent_tool_names: string[]          # allow-list names only, no arguments
  composition_mode: CompositionMode
  composition_status: CompositionStatus

AgentToolCallRecord
  observation_id: string              # server generated, not exposed to model as authority
  request_id: UUID
  tool_name: NBA_QUERY|NBA_SCHEDULE|NBA_NEWS
  arguments_hash: string              # never raw user/tool arguments
  status: OK|EMPTY|PARTIAL|FAILED|DUPLICATE|CANCELLED
  evidence_state: EvidenceState
  latency_ms: int
  created_at_utc: Instant

AgentToolObservation
  observation_id: string
  status: COMPLETED|NO_DATA|NEEDS_CLARIFICATION|FAILED
  intent_name: IntentName?
  query_start_beijing: date?
  query_end_beijing: date?
  answer_markdown: string
  blocks: AnswerBlock[]
  evidence_state: EvidenceState
  as_of_beijing: string?

EvaluationTurn
  turn_index: int (1-based, contiguous within the case)
  prompt: string (1..2000 Unicode characters)
  expected_intent: IntentName
  expected_entities: object
  reference_facts: object
  tolerance: object?
  safety_expected: SafetyOutcome

EvaluationCase
  case_id: string
  category: Category
  turns: EvaluationTurn[] (1..8; category H requires exactly 3)
  source_snapshot: string?

EvaluationRun
  run_id: UUID
  case_id: string
  repeat_index: int
  provider_mode: EvaluationProviderMode
  ratings: {understanding, accuracy, completeness, expression, structure, consistency, latency}
  scores: {understanding, accuracy, completeness, expression, structure, consistency, latency}?
  safety_veto: bool
  evidence_state: EvidenceState
  corrections: PublicCorrection[]
  ttft_ms: int?
  total_latency_ms: int?
  notes: string?
```

`ratings` 保存面试评测使用的“优秀/良好/合格/不合格”原始档位；`scores` 是可配置映射
后的数值，PDF 没有规定档位到数值的固定换算，因此实现不得硬编码未经确认的分值。

`ConversationRecord` 是持久化外壳；运行时 `ConversationContext` 是其中的有界投影，二者
共享同一 `session_id`，不允许各自维护另一套活动实体。

`EvaluationCase.turns` 是评测输入的唯一规范形态：单轮题包含一个 turn，H 类题包含按
`turn_index` 排序的三轮消息并在同一 `session_id` 中执行。`expected_entities`、
`reference_facts` 和 `tolerance` 可按轮定义，避免把三轮追问压缩成一个字符串；
`source_snapshot` 只标识版本化 fixture，不进入用户响应。

## 5. Relationships and lifecycle

```text
ConversationRecord 1 ── N QueryRecord
QueryRecord 1 ── 0..1 QueryIntent (PARSED 及之后必须为 1)
QueryRecord 1 ── 0..1 FactBundle
FactBundle 1 ── N FactAssertion
FactAssertion N ── N Evidence
Game 1 ── N PlayEvent
Game 1 ── N StatLine
SeriesRef 1 ── N Game
EvaluationCase 1 ── N EvaluationRun
```

生命周期：`QueryRecord.phase` 从 RECEIVED 开始并记录最近一个持久化阶段；分支事件
（如拒答/引导/无结果）不另造持久化 phase。`QueryRecord.outcome` 只在终态写入。
安全命中先记录 `SAFETY_CHECKED`，随后以 `phase=COMPLETED, outcome=BLOCKED` 结束；
范围外请求以 `phase=COMPLETED, outcome=NO_DATA` 结束；两者的 provider call count 都必须
为 0。缺槽位/歧义以 `phase=COMPLETED, outcome=NEEDS_CLARIFICATION` 结束。允许请求依次
经过 `CONTEXT_RESOLVED → PARSED → PLAN_READY → RETRIEVING → NORMALIZED →
VERIFIED/UNVERIFIED → DERIVED（如需要）→ COMPOSED → OUTPUT_GUARDED`，成功后以
`phase=COMPLETED, outcome=COMPLETED` 结束；技术失败以 `phase=FAILED, outcome=FAILED`
结束。会话到期后删除活动上下文；评测记录按版本保存以便比较回归。

## 6. Validation invariants

- `message` 长度 1–2000；空白输入拒绝。
- 时间戳必须带时区；不能把源时间的本地日期直接当北京时间日期。
- `season.end_year = season.start_year + 1`，label 与年份一致。
- `Game.home.canonical_id != Game.away.canonical_id`；比分非负整数或 `null`。
- PBP `period >= 1`、`clock_seconds_remaining >= 0`；`points` 非空时必须 `>= 0`，缺失
  保持 `null`；`provider_index` 在 bundle 内唯一且非负，排序遵循 §2 的 `sequence_valid`
  分支规则。
- `StatLine.scope=GAME` 时必须有 `game_id`；`SERIES` 时必须有 `series_id`；`SEASON` 时必须
  有 `season`；`CAREER` 至少有 subject，且不得伪造缺失范围。
- `FactAssertion.verification=VERIFIED` 时至少有一条高/中可信 Evidence；派生事实必须
  列出 `derived_from_fact_ids`。
- `QueryRecord.provider_call_count >= 0`；`outcome=BLOCKED` 或
  `safety_category=OUT_OF_SCOPE` 时，`provider_call_count` 必须为 0。
- SafetyGate 决策发生在 Provider Gateway 和其缓存读取之前；`outcome=BLOCKED` 或
  `safety_category=OUT_OF_SCOPE` 时，`cache_read_count=cache_write_count=0`。
- 在 `provider_call_count=0` 且未执行 Provider 查询的短路分支（BLOCKED、OUT_OF_SCOPE 或
  早期 NEEDS_CLARIFICATION）中，`evidence_state` 必须为 `NONE`。
- `QueryRecord.phase` 至少达到 `PARSED` 时，`intent_category`、`intent_name` 和
  `parsed_query` 必须非空；安全/范围外/输入校验短路可保持这些字段为空。
- `QueryRecord.phase=FAILED` 时，`outcome=FAILED` 且 `error_code` 非空；非失败终态不得
  写入技术错误码。
- `error_code=SERVICE_BUSY` 时，`phase=FAILED, outcome=FAILED`，并且 `admission_result`
  不得为 `ADMITTED`。
- `admission_result=QUEUE_FULL|RATE_LIMITED|DEADLINE_EXCEEDED` 时不得访问 Provider 或
  Hermes；该分支使用 `SERVICE_BUSY` 或等价的本地过载错误，不能伪装成上游限流。
- `hermes_mode=OFF` 时不得产生 Hermes 调用；`hermes_status` 和 `fallback_reason` 只用于
  内部 telemetry，不进入用户响应。
- `agent_tool_names` 只能是 `nba_query`、`nba_schedule`、`nba_news`；BLOCK/OUT_OF_SCOPE
  时 `agent_iteration_count=agent_tool_call_count=0`。单请求 tool call 不得超过配置上限 4。
- `AgentToolObservation` 不包含 Provider URL、raw JSON、凭据、canonical/evidence ID 或工具
  指令；`NO_DATA` 必须尽可能保留确定性解析出的北京时间查询范围。
- `EvaluationCase.turns` 的 `turn_index` 必须从 1 连续递增；`category=H` 时长度必须为
  3，其他类别默认长度为 1（扩展多轮案例需显式记录理由）。
- 不同 `session_id` 的 `ConversationContext` 不可互相引用。
