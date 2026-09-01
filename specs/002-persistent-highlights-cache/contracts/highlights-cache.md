# Contract: Persistent Highlights Cache

## Public HTTP compatibility

以下接口的请求和 JSON envelope 保持不变：

- `GET /api/v1/highlights`
- `GET /api/v1/highlights/recent`
- `GET /api/v1/highlights/range`
- `GET /api/v1/highlights/{game_id}/detail`

缓存状态不作为 Provider/内部实现信息写入公开 payload。`as_of_beijing` 始终表示事实数据时间，而不是缓存读取时间。

## Stable cache keys

```text
date:v1:{timezone}:{YYYY-MM-DD}
recent:v1:{timezone}:{reference-day}:{limit}
range:v1:{timezone}:{from}:{to}
detail:v1:{timezone}:{game-id}
```

所有组成部分先通过现有日期、时区、limit 和 game_id 校验。客户端原始文本不得直接进入 key。

## Read policy

| Response | Fresh hit | Stale hit | Miss |
|---|---|---|---|
| historical date/range | return, no Provider | return immediately + one background refresh | synchronous load + store |
| recent final list | return, no Provider | return immediately + one background refresh | synchronous load + store |
| final detail | return, no Provider | return immediately + one background refresh | synchronous load + store |
| today/live/non-final | return, no Provider | treat as miss; do not return stale | synchronous load + short TTL |

Default freshness: today/live 45s；recent 15min；historical list 24h；final detail 7d。配置必须为正数且有上限。

## Write policy

1. Serialize with `model_dump(mode="json", by_alias=True)` and canonical JSON ordering.
2. Enforce UTF-8 size ≤ configured max.
3. Re-validate the serialized payload with the response model before transaction.
4. For a matching key, reject non-empty → empty downgrade and detail completeness downgrade.
5. Write payload, fingerprint, times and completeness in one transaction.
6. Prune expired non-final records, then least-recently-used derived responses above max entries.

## Failure policy

- Open/schema/lock/disk/write error: emit internal counter/log-safe status and continue without persistent cache.
- Invalid row: delete/quarantine that row and treat as miss.
- Provider refresh failure with historical stale value: keep and return stale value; never rewrite `as_of_beijing`.
- Provider refresh failure for current data: preserve existing public error behavior.

## Concurrency contract

- SQLite uses WAL and a bounded busy timeout.
- A refresh lease allows one active refresh per stable key.
- Lease expires automatically；caller cancellation/background exception releases best-effort.
- Concurrent reads may proceed while a refresh writes.

## Deployment contract

- `HIGHLIGHTS_CACHE_ENABLED=true|false`
- `HIGHLIGHTS_CACHE_DB=/app/data/highlights.sqlite3`
- `HIGHLIGHTS_CACHE_MAX_ENTRIES=5000`
- `HIGHLIGHTS_CACHE_MAX_PAYLOAD_BYTES=2097152`
- Docker mounts a named writable volume at `/app/data`.
- Removing the named volume is the explicit destructive operation that clears persisted highlights.
