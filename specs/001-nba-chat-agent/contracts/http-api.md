# HTTP and SSE Contract

**Version**: `v1`
**Feature**: [spec.md](../spec.md)
**Implementation detail**: [lld.md](../lld.md)

## 1. Shared rules

- JSON uses UTF-8; timestamps in wire metadata use ISO-8601 UTC unless the field name says
  `as_of_beijing`.
- Wire enum values are lowercase; they serialize the uppercase canonical domain enums defined in
  `data-model.md` using this mapping:

  | Domain enum | Wire values |
  |---|---|
  | `QueryOutcome` | `completed`, `no_data`, `needs_clarification`, `blocked`, `failed` |
  | `EvidenceState` | `verified`, `partial`, `none` |
  | `DataOrigin` | `public`, `demo_snapshot`, `mixed`, `none` |
  | `AnswerBlockType` | `text`, `analysis`, `warning`, `table`, `fact` |
  | `CorrectionStatus` | `corrected`, `unverified` |

Internal `ErrorCode` values `SAFETY_BLOCKED`, `AMBIGUOUS_ENTITY`, `MISSING_SLOT` and `NO_DATA`
map to the conversational statuses in §3 and are not emitted in the technical `error` object.
`SERVICE_BUSY` is a technical overload error and uses the §5 error envelope; the remaining error
codes use the uppercase names shown there.

- `message` is required, trimmed, 1–2000 Unicode characters.
- The server creates `request_id` and `session_id` when absent.
- `client_timezone`, when present, must be a valid IANA timezone; an invalid value is a
  `400 INVALID_PAYLOAD` and is never used for a data query. It affects parsing of relative input
  dates only; user-facing output remains Asia/Shanghai unless a future version adds an explicit
  display-timezone field.
- Clients must not send provider URLs, API fields, prompts or arbitrary tool commands.
- User-facing responses never contain provider names, endpoint URLs, raw fields or internal traces.
- Sync and SSE routes invoke the same application use case and produce equivalent final envelopes.
- `as_of_beijing` is nullable for blocked, clarification, empty or failed outcomes. When present
  it is formatted as `YYYY-MM-DD HH:mm` in `Asia/Shanghai`; it is a display timestamp, not a
  replacement for internal UTC instants.
- `data_origin` is a generic per-response origin classification. `demo_snapshot` never exposes a
  fixture/provider identifier and MUST use `as_of_beijing=null`; clients label it as a fixed demo
  rather than “fresh public verification”.
- Safety BLOCK and `OUT_OF_SCOPE` decisions are made before any Provider or Provider-cache lookup;
  their internal call/read/write counters are all zero.

### Authentication

When `AUTH_REQUIRED=true`, `POST /api/v1/chat`, `POST /api/v1/chat/stream`,
`GET /api/v1/highlights`, and `GET /api/v1/highlights/availability` require the opaque session
Cookie issued by `POST /api/v1/auth/login`. Missing/expired sessions return HTTP 401 with
`AUTH_REQUIRED`; a required but unreadable password secret returns HTTP 503 with
`AUTH_NOT_CONFIGURED`. Authentication never falls back to anonymous access.

- `GET /api/v1/auth/status` → `{"enabled":true,"authenticated":false}`.
- `POST /api/v1/auth/login` with `{"password":"..."}` → `{"authenticated":true}` plus
  `HttpOnly; SameSite=Lax; Path=/` Cookie; bad credentials return 401 and repeated failures 429.
- `POST /api/v1/auth/logout` revokes the session and expires the Cookie.

The password and session token never appear in JSON responses or public logs.

## 2. Health endpoint

`GET /healthz`

Response `200` (liveness; readiness may use `503`):

```json
{"status":"ok|degraded|not_ready","version":"v1","mode":"live|fixture|hybrid","capabilities":{"full_intelligence":true,"web_search":false},"dependencies":{"session_store":"ok|degraded","cache":"ok|degraded","hermes":"disabled|ok|degraded","auth":"ok|degraded","web_search":"enabled|disabled"}}
```

`/healthz` is a compatibility alias for the public liveness response and must not expose
credentials, upstream URLs or detailed dependency errors. Deployments SHOULD also expose
`/livez` (process liveness only) and `/readyz` (local API/session/cache dependencies; external
Provider/LLM probes are not readiness requirements). A `not_ready` response uses HTTP 503.

## 3. Synchronous chat

`POST /api/v1/chat`

Request:

```json
{
  "session_id": "optional UUID",
  "message": "2025-26 总决赛 G4 谁得分最高？",
  "client_timezone": "Asia/Shanghai",
  "client_message_id": "optional idempotency key",
  "intelligence_mode": "hybrid|full|auto (optional)",
  "selected_game_id": "optional highlights card ID"
}
```

Response `200` for all conversational outcomes (`completed`, `needs_clarification`, `blocked`,
or `no_data`). These are successful protocol responses, not transport failures:

```json
{
  "request_id": "uuid",
  "session_id": "uuid",
  "status": "completed|needs_clarification|blocked|no_data",
  "answer_markdown": "……",
  "blocks": [
    {"type":"text","content":"……"},
    {"type":"table","columns":["球队","胜场"],"rows":[["示例",1]]}
  ],
  "as_of_beijing": "2026-08-26 21:30",
  "evidence_state": "verified|partial|none",
  "data_origin": "public|demo_snapshot|mixed|none",
  "corrections": [
    {"status":"corrected|unverified","message":"仅含用户可见的本地化纠偏说明"}
  ],
  "follow_up": null,
  "latency_ms": 1234,
  "composition": {
    "mode": "agent",
    "status": "used",
    "latency_ms": 980
  }
}
```

`composition` is a deliberately small, provider-neutral provenance marker for the demo UI:
`mode=deterministic` means the fact renderer answered directly, `mode=model` means the legacy
single-pass analysis composer was accepted, `mode=agent` means the bounded full-intelligence loop
participated, and `mode=fallback` means a model/Agent path returned to the deterministic
evidence-first answer. `status` is `not_requested`, `used`, `fallback`, or `disabled`. It never
contains a model/provider name, endpoint, prompt, key, tool arguments or internal IDs.

`intelligence_mode=full` is accepted only when the service feature flag is enabled; otherwise the
server safely uses hybrid routing. `auto` or an omitted field uses the configured default.

`status=failed` uses the technical error envelope in §5. A blocked response must have an internal
`provider_call_count=0`; that field is intentionally not exposed to the client. A clarification
response uses `answer_markdown` as the single question and may include `follow_up`. `corrections`
is always the public mapping described above; internal `Correction.claim`, canonical IDs and
evidence references are never serialized.

## 4. Streaming chat

`POST /api/v1/chat/stream` with the same request body. Response:

```text
Content-Type: text/event-stream
Cache-Control: no-cache
X-Request-Id: <request id>
```

Each event is a single JSON payload preceded by `event:` and `data:`. Event order is strict:

1. `run.started`
2. zero or more `run.status` (safe progress text only)
3. one of:
   - `message.delta` events followed by `message.completed`,
   - `clarification.required` followed by `message.completed` with status `needs_clarification`,
   - `safety.blocked` followed by `message.completed` with status `blocked`,
   - `message.completed` with status `no_data`,
   - `run.error`.

Examples:

```text
event: run.started
data: {"request_id":"uuid","session_id":"uuid"}

event: run.status
data: {"stage":"verifying","text":"正在核对比赛数据"}

event: message.delta
data: {"text":"勇士在"}

event: message.completed
data: {"request_id":"uuid","session_id":"uuid","status":"completed","answer_markdown":"…","as_of_beijing":"…","evidence_state":"verified","blocks":[],"corrections":[],"follow_up":null,"latency_ms":1234}
```

Before verification/derivation, status events must not contain factual numbers. The server sends
a comment heartbeat (`: heartbeat`) at most every 15 seconds. On client disconnect it cancels
downstream work and records a telemetry event. A client may retry with the same
`client_message_id`; the deduplication key is `(session_id, client_message_id)`, so the server
returns the existing completed result for that session when available. The same client ID in
another session never reuses a result.

Every `message.completed` payload MUST equal the §3 conversational response envelope (including
`request_id`, `session_id`, `status`, `blocks`, `corrections`, `follow_up`, `evidence_state`,
`as_of_beijing`, `latency_ms` and `composition`); the example above is abbreviated only for
readability.

`blocks` follows the canonical `AnswerBlock` union: `text`, `analysis` and `warning` require
`content`; `fact` uses `label`/`value`/optional `unit`; `table` uses non-empty `columns` and
same-width `rows`. Unknown block fields are ignored on rendering and must not be used to smuggle
provider metadata.

## 4.1 Date availability projection

`GET /api/v1/highlights/availability?from=YYYY-MM-DD&to=YYYY-MM-DD&timezone=...` returns a
bounded calendar projection for the history date picker. `from` and `to` are inclusive local
calendar dates; the range is limited to 31 days. When both are omitted, the current month in the
requested timezone is used. Supplying only one endpoint, an invalid/reversed range, or a range
longer than 31 days returns the technical `400 INVALID_PAYLOAD` envelope described below.

The response is provider-free and preserves a tri-state distinction so an outage is never shown
as a confirmed no-game day:

```json
{
  "timezone": "Asia/Shanghai",
  "from": "2026-06-06",
  "to": "2026-06-09",
  "days": [
    {"date": "2026-06-06", "status": "available", "game_count": 1, "is_future": false},
    {"date": "2026-06-07", "status": "empty", "game_count": 0, "is_future": false},
    {"date": "2026-06-08", "status": "available", "game_count": 1, "is_future": false},
    {"date": "2026-06-09", "status": "unknown", "game_count": null, "is_future": false}
  ],
  "as_of_beijing": "2026-08-28 15:50",
  "evidence_state": "verified|partial|none"
}
```

`status=available` means at least one normalized game was returned; `status=empty` means the
requested day completed successfully with no games; `status=unknown` means the day could not be
confirmed (for example, an upstream timeout/partial response or a future day). Clients MAY gray
and disable `empty` days and all `is_future=true` days. They SHOULD leave non-future `unknown`
days retryable rather than presenting them as no-game dates.

## 4.2 Recent and custom-range highlights

`GET /api/v1/highlights/recent?limit=5&timezone=...` returns the latest completed games, newest
first. `limit` defaults to 5 and is bounded to 1–20. The response uses the range projection shape
below; its `from`/`to` fields describe the bounded lookback used by the service, while `games` is
limited to the requested count.

`GET /api/v1/highlights/range?from=YYYY-MM-DD&to=YYYY-MM-DD&timezone=...` returns every normalized
game whose local start date falls in the inclusive interval. Custom ranges are limited to 93 days;
missing, reversed, future or oversized ranges return `400 INVALID_PAYLOAD`. Both endpoints keep
the response provider-free. A subsequent chat request MAY include the selected card's
`selected_game_id`; the server resolves that ID against its own highlights registry and uses it as
the current session game context. Client-supplied scores/team names are ignored, explicit game
entities in the message take precedence, and an unknown/unverified ID is never used to fabricate
facts:

```json
{
  "timezone": "Asia/Shanghai",
  "from": "2026-06-06",
  "to": "2026-06-12",
  "games": [
    {
      "game_id": "2026-finals-g4",
      "start_utc": "2026-06-12T01:30:00Z",
      "home_name": "凯尔特人",
      "away_name": "雷霆",
      "status": "final",
      "home_score": 108,
      "away_score": 104,
      "venue_name": "TD Garden",
      "venue_city": "Boston",
      "venue_state": "MA",
      "venue_country": "USA"
    }
  ],
  "as_of_beijing": "2026-08-28 15:50",
  "evidence_state": "verified|partial|none",
  "data_origin": "public|demo_snapshot|mixed|none"
}
```

## 5. Technical error envelope

```json
{
  "request_id":"uuid",
  "session_id":"uuid",
  "status":"failed",
  "error": {
    "code":"INVALID_PAYLOAD|SERVICE_BUSY|UPSTREAM_TIMEOUT|UPSTREAM_RATE_LIMITED|UPSTREAM_AUTH|INVALID_UPSTREAM_DATA|COMPOSER_UNAVAILABLE|OUTPUT_BLOCKED",
    "retryable":true,
    "message":"面向用户的简短说明"
  }
}
```

`message` is localized and must not contain internal URLs, stack traces, field names or prompts.
For SSE, `run.error` has the same `error` object and includes `request_id`/`session_id`:

```text
event: run.error
data: {"request_id":"uuid","session_id":"uuid","status":"failed","error":{"code":"UPSTREAM_TIMEOUT","retryable":true,"message":"数据暂时不可用，请稍后重试"}}
```

`OUT_OF_SCOPE` is a conversational `no_data` outcome with a short basketball redirection and
zero provider calls; it is not a technical failure.
Conversational codes such as `SAFETY_BLOCKED`, `AMBIGUOUS_ENTITY`, `MISSING_SLOT` and `NO_DATA`
are represented by the §3 `200` outcome and are not `status=failed`. HTTP status mapping for
technical failures:

| HTTP | Codes |
|---:|---|
| 400 | `INVALID_PAYLOAD` |
| 429 | `UPSTREAM_RATE_LIMITED` or ingress client rate limit (include `Retry-After` only when supplied by application policy) |
| 503 | `SERVICE_BUSY` (local queue/capacity or maintenance; include `Retry-After` when available) |
| 502 | `INVALID_UPSTREAM_DATA`, `UPSTREAM_AUTH` |
| 504 | `UPSTREAM_TIMEOUT` |
| 500 | `COMPOSER_UNAVAILABLE`, `OUTPUT_BLOCKED` |

## 6. Security and compatibility requirements

- CORS allowlist is configuration-driven; wildcard origins are forbidden in production.
- Request body and event size are bounded; server rejects control characters and invalid UTF-8.
- API is versioned under `/api/v1`; additive fields are allowed, breaking changes require a new
  version and contract fixtures.
- Error and status content is safe to log after redaction. No user-supplied URL is fetched.
