# Hermes Agent Runtime Contract

**Version**: `agent.v1`
**Feature**: [spec.md](../spec.md)
**Design**: [lld.md](../lld.md)

## 1. Boundary

The full-intelligence runtime starts only after authentication, request validation and the
pre-retrieval SafetyGuard return `ALLOW`. It uses the official locked Hermes Agent package for
question understanding and bounded function calling. It does not own NBA facts, Provider
credentials, cache access, safety decisions or deterministic arithmetic.

The runtime-visible tool set MUST equal exactly:

```json
["nba_news", "nba_query", "nba_schedule"]
```

Shell, filesystem, browser, generic web, MCP, memory, skills, delegation, code execution and
arbitrary URL capabilities are absent. A capability mismatch disables full mode and routes the
request through the deterministic/hybrid path.

## 2. Agent input and result

```text
AgentTurnInput
  contract_version: "agent.v1"
  request_id: UUID
  opaque_session_id: hash
  sanitized_question: string (1..2000)
  timezone: IANA timezone
  now_beijing: "YYYY-MM-DD HH:mm"
  context_hint: string? (bounded, provider-free)
  conversation_history: [{role: user|assistant, content: string}] (0..8 messages / 12 KiB)
  deadline_at_utc: Instant
  max_iterations: 1..4
  max_tool_calls: 1..4

AgentTurnResult
  status: OK|TIMEOUT|UNAVAILABLE|UNSAFE
  answer_markdown: string?
  evidence_state: VERIFIED|PARTIAL|NONE
  tool_calls: AgentToolCallRecord[]
  finish_reason: string? (internal only)
  usage: {input_tokens: int, output_tokens: int}?
  latency_ms: int
```

The system prompt is server-owned and includes the current Beijing time, NBA-only scope, tool-use
rules, empty-result behavior, fact/inference separation and prompt-injection warning. User text or
tool output cannot replace it.

The application session is the sole continuity owner. Its UUID is one-way hashed into a stable
`opaque_session_id` for the lifetime of that chat. The browser reuses the application session
across refreshes and replaces it only when the user starts a new chat. Every Agent invocation uses
a separate random `task_id` for the tool bridge; `task_id` MUST NOT be used as the logical session.

`conversation_history` contains at most the latest four complete user/assistant pairs. Roles MUST
alternate beginning with `user` and end with `assistant`; incomplete pairs, control characters,
more than eight messages, or more than 12 KiB are rejected. The official runtime receives this
history explicitly while native memory, session database, context files and trajectories remain
disabled. History resolves references only: every current factual question still requires a new
successful NBA tool observation, and old answers never become evidence.

Questions about deterministic application session state are resolved before this runtime. Turn
count, previous user/assistant message, bounded conversation summary, active subject and current
intelligence mode therefore use `composition.mode=deterministic,status=not_requested` even when
full mode is selected. Referential NBA questions such as “刚才那个球是谁” are not session-meta
questions and still require a fresh successful NBA tool observation.

## 3. Tool contracts

### `nba_query`

Input:

```json
{"question":"2025-26 总决赛 G4 谁得分最高？"}
```

`question` is trimmed and limited to 500 characters. The handler reuses the deterministic query
pipeline in internal-tool mode. It may perform typed game, summary, stats, history or PBP provider
operations, then returns a sanitized `AgentToolObservation`.

When the original user wording explicitly requests public/online re-verification and a server-owned
selected game exists, the bridge overrides ordinary tool arguments with the public re-verification
policy. It force-refreshes only the primary scoreboard, resolves the internal selection to one exact
date+matchup public event ID, and disables fixture fallback for the subsequent summary. The Agent
cannot opt out of or broaden that policy.

### `nba_schedule`

Input:

```json
{"date_expression":"下周","team":"可选球队名"}
```

The server, not the model, resolves the date expression using the request timezone and injectable
clock. The observation includes the resolved inclusive Beijing calendar range even when the result
is empty. An empty result says only that the bounded structured query returned no games; season
phase/background needs separate evidence.

### `nba_news`

Input:

```json
{"subject":"NBA 下赛季季前赛时间","date_expression":"可选日期表达"}
```

`subject` is 1–160 characters and must not contain a URL, control text or tool instruction. The
handler routes through typed `search_news`; controlled DuckDuckGo candidates remain `PARTIAL` and
cannot authorize score, ranking, player-statistic, series-arithmetic or PBP numbers.

All tool outputs have this public-to-agent shape:

```json
{
  "status":"completed|no_data|needs_clarification|failed",
  "intent":"schedule_result",
  "query_scope":{"start_date":"2026-08-31","end_date":"2026-09-06","timezone":"Asia/Shanghai"},
  "answer_markdown":"……",
  "blocks":[],
  "evidence_state":"verified|partial|none",
  "as_of_beijing":"2026-08-30 18:04",
  "data_origin":"public|demo_snapshot|mixed|none"
}
```

Provider names, URLs, raw payloads, canonical IDs, evidence IDs, request/session IDs, stack traces
and credentials are forbidden.

## 4. Budget, deduplication and cancellation

- A request has at most four model iterations and four tool calls.
- Each tool call must finish inside the parent request deadline; it cannot extend that deadline.
- The same tool plus normalized arguments may execute once per request. A repeated call returns a
  bounded `duplicate` error without touching Provider/cache.
- Tool output is capped at 16 KiB per call; the Agent final text is capped at 20 KiB.
- The task bridge is removed in `finally`. Disconnect/cancellation invalidates it immediately;
  late handlers receive `cancelled` and make no external call.
- Agent execution runs in a bounded worker because Hermes exposes a synchronous conversation loop;
  Provider/cache/session work always returns to the owning ASGI loop through the bridge.

## 5. Output and fallback

Greetings and capability introductions (including identity questions and common pinyin/English
aliases) may complete without a tool and must contain no NBA factual claim. Deterministic session
metadata is answered before the runtime and is not a zero-tool Agent exception. Schedule, score,
player/team data, history, news, tactical-fact or PBP answers require at least one successful
observation. If Hermes is unavailable for a capability turn, the API returns a local capability
prompt with `composition.mode=deterministic,status=not_requested` rather than NBA clarification.

The final guard rejects control text, prompt/tool instructions, internal/provider fields,
unobserved numeric claims and factual entities unsupported by observations. Server clock values and
numbers present in the user's question are separately tagged; they do not authorize unrelated NBA
facts.

Objective NBA, metadata and PBP output is grounded before the final guard: the final factual markdown
is the server-owned deterministic observation, not the Agent paraphrase. This prevents relation-level
hallucinations that numeric membership alone cannot detect (winner/team-score inversion, free throw
described as a field goal, or a terminal marker described as a shot). Analytical wording may remain
Agent-authored, but must not mention tool names/counts, claim inability to connect, or introduce
unsupported facts. Public-reverification observations are always authoritative, including no-match.

Accepted output uses:

```json
{"composition":{"mode":"agent","status":"used","latency_ms":1234}}
```

Package/configuration failure, timeout, invalid tool call, duplicate loop, exhausted budget or
guard rejection falls back to deterministic/hybrid processing:

```json
{"composition":{"mode":"fallback","status":"fallback","latency_ms":1234}}
```

Detailed finish reasons and tool arguments remain internal telemetry. Red-line safety outcomes have
zero Agent and tool calls and never use fallback to answer substantively.
