# HTTP and SSE Contract

## POST `/api/v1/chat`

Request:

```json
{
  "session_id": "optional-uuid",
  "client_message_id": "optional-idempotency-key",
  "message": "绣球多久浇水？",
  "client_timezone": "Asia/Shanghai",
  "intelligence_mode": "full"
}
```

- Unknown fields, blank messages, control characters and messages over 2,000 characters are rejected.
- `selected_game_id` remains accepted only for explicit legacy compatibility and cannot influence a flower turn.
- Omitting `session_id` creates a new logical session. Reusing it preserves bounded flower context.

Successful conversational response (`200`):

```json
{
  "request_id": "uuid",
  "session_id": "uuid",
  "status": "completed",
  "answer_markdown": "...",
  "blocks": [{"type": "text", "content": "..."}],
  "as_of_beijing": "2026-09-13 13:00",
  "evidence_state": "verified",
  "data_origin": "local",
  "corrections": [],
  "follow_up": null,
  "latency_ms": 12,
  "composition": {"mode": "agent", "status": "used", "latency_ms": 8},
  "notices": []
}
```

Allowed status values are `completed`, `needs_clarification`, `blocked` and `no_data`. Technical failures use a separate error envelope and never masquerade as `no_data`.

Allowed flower origins are `local`, `local_knowledge`, `search`, `mixed` and `none`; legacy sports values `public` and `demo_snapshot` remain wire-compatible. Search-only material has at most `partial` evidence.

Public notice codes:

- `INTELLIGENCE_QUOTA_EXHAUSTED`
- `INTELLIGENCE_AUTH_UNAVAILABLE`
- `INTELLIGENCE_TEMPORARILY_UNAVAILABLE`
- `SEARCH_QUOTA_EXHAUSTED`
- `SEARCH_AUTH_UNAVAILABLE`
- `SEARCH_TEMPORARILY_UNAVAILABLE`

Each notice contains only `{code,message,retryable}`. It cannot contain a provider, endpoint, model, key, raw response or internal identifier.

Technical error status mapping remains:

- `400 INVALID_PAYLOAD`
- `429 UPSTREAM_RATE_LIMITED`
- `502 UPSTREAM_AUTH|INVALID_UPSTREAM_DATA`
- `503 SERVICE_BUSY`
- `504 UPSTREAM_TIMEOUT`
- `500 COMPOSER_UNAVAILABLE|OUTPUT_BLOCKED`

## POST `/api/v1/chat/stream`

The request body is identical. The response is `text/event-stream` with this state machine:

```text
run.started
  -> zero or more run.status
  -> zero or more message.delta
  -> exactly one message.completed

or, before any delta:
run.started -> run.error
```

Flower safety may emit `safety.blocked` before `message.completed`. A material missing slot may emit `clarification.required`. The terminal `message.completed` payload is the same public shape as the synchronous response. Events must use one request/session pair throughout and obey configured frame and cumulative byte limits.

Cancellation closes the producer and does not persist a partial assistant answer. Idempotent replay returns the original terminal result; reusing one `client_message_id` with different text is an `INVALID_PAYLOAD` conflict and must not search or call the model.

## GET `/healthz`, `/readyz`, `/livez`

These endpoints disclose only product-neutral health, version, experience and intelligent-analysis availability. They never expose runtime, provider, storage, key or model names.
