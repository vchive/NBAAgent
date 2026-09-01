# Data Model: 赛事回顾持久缓存

## CacheEntry

一个经过校验的公开 highlights 响应。

| Field | Type | Rules |
|---|---|---|
| cache_key | text primary key | 由 response kind + 规范化参数生成，最大 512 字符 |
| response_kind | enum text | `date` / `range` / `recent` / `detail` |
| schema_version | integer | 当前为 1；不兼容版本读出即隔离 |
| payload_json | UTF-8 JSON text | 最大 2MiB；必须由对应 Pydantic 公开模型重新校验 |
| game_status | nullable enum text | detail/date 的主状态；`final` 才允许长期 stale 读取 |
| completeness_score | integer | 确定性、非负；只用于比较同 kind/key 的数据完整度 |
| content_fingerprint | SHA-256 text | 规范 JSON 指纹，用于冲突/重复判断 |
| source_as_of | nullable text | 公开响应原 `as_of_beijing`，不得改写成缓存写入时间 |
| stored_at_utc | aware UTC text | 成功事务提交时间 |
| fresh_until_utc | aware UTC text | fresh hit 截止；历史过期可 SWR，今日过期不可返回 |
| last_accessed_utc | aware UTC text | 命中更新，用于清理派生列表 |
| payload_bytes | integer | 1..2MiB |

### Completeness score

- list/range: 每场稳定 ID、球队、时间、状态和比分计分；终场比赛比分权重高。
- detail: 基础 game 字段 + 非空比分 + leaders 字段数 + PBP 事件数；未来加入场馆/分节时按非空字段增加。
- `evidence_state=none` 的空历史范围可以缓存，但不得覆盖已有非空结果。

### State transitions

```text
missing -> fresh
fresh -> stale (fresh_until elapsed)
stale historical -> served + refreshing -> fresh | stale (refresh failed)
stale live/today -> miss -> refreshing -> fresh | unavailable
any -> quarantined/deleted (schema, JSON, size or model validation failure)
```

## RefreshLease

同一缓存键的有界刷新权。

| Field | Type | Rules |
|---|---|---|
| cache_key | text primary key | 指向逻辑 CacheEntry 键，不要求 entry 已存在 |
| owner_token | random text | 进程内生成，不向客户端返回 |
| expires_at_utc | aware UTC text | 最长 30 秒；崩溃后自动可抢占 |

成功刷新或后台任务结束后释放；过期 lease 可由下一请求替换。

## CompletenessProfile

不单独持久化为表；写入时从公开模型计算。

| Component | Examples |
|---|---|
| identity | game_id、主客队、start_utc、status |
| score | home_score、away_score、终场状态 |
| metadata | 场馆、比赛时长、分节（schema 可用时） |
| leaders | player_name + points/rebounds/assists 非空数 |
| play_by_play | 事件数、带比分/球员/action 的字段数 |

## Privacy boundary

不允许的内容：Cookie、密码、session_id、聊天问题/回答、模型/Agent 提示词、API key、Provider 原始 URL/响应、内部 evidence_id。
