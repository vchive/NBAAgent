# Research: 赛事回顾持久缓存与数据完整性

## Decision 1 — 保存公开投影，不保存原始上游响应

**Decision**: SQLite 只保存 `HighlightsResponse`、`HighlightsRangeResponse` 与 `HighlightDetailResponse` 的 JSON 序列化结果，并在读出时再次执行 Pydantic 校验。

**Rationale**: 公开投影已经完成字段约束、时间格式、文本安全和 Provider 内部信息剥离；这能显著降低 schema 漂移、敏感字段和超大响应进入持久存储的风险。

**Alternatives considered**:

- 保存 ESPN/NBA 原始 JSON：字段更全，但未经统一校验、体积不可控且强耦合 Provider 版本，拒绝。
- 重新建立完整比赛范式化数据库：长期更灵活，但本次最近五场/详情缓存不需要第二套领域仓储，复杂度过高。

## Decision 2 — 历史 SWR，今日 fresh-only

**Decision**: 已结束比赛详情和历史范围允许返回 stale cache 并后台低频刷新；今日、进行中或未结束比赛只有 fresh hit 才可返回。

**Rationale**: 终场比分和 PBP 基本不可变，先返回缓存可消除重复等待；今日赛程与实时比分会变化，不能把过期值当作当前事实。

**Alternatives considered**:

- 所有缓存永久有效：最近五场列表会错过新比赛，实时风险不可接受。
- 所有缓存过期后同步刷新：正确但重新引入用户每隔一段时间看到长时间 loading 的问题。

## Decision 3 — 详情完整度单调升级

**Decision**: 对详情计算确定性 `completeness_score` 和 profile（比分、元信息、leaders、PBP 数量）；新响应只有不存在冲突且完整度不低于旧记录时才替换。相同比赛的非空字段可以安全合并，比分/PBP 冲突则拒绝写入。

**Rationale**: 公共源偶尔返回临时不完整 summary。简单 last-write-wins 会把完整详情退化成只有比分的记录。

**Alternatives considered**:

- 总是覆盖：实现简单但违反 FR-007。
- 按来源静态优先级覆盖：来源可靠性重要，但同一来源在不同时间也可能完整度不同，不能替代内容比较。

## Decision 4 — SQLite 与单机并发

**Decision**: 使用 Python 标准库 SQLite，WAL、busy timeout、事务化 UPSERT、进程内锁和数据库 refresh lease；单条 2MiB、默认 5000 条并按访问时间清理。

**Rationale**: 当前是单机 Docker 演示，SQLite 提供跨重启持久性且无需外部服务。WAL 适合读多写少；写入失败可以安全退化。

**Alternatives considered**:

- Redis：TTL/SWR 方便，但需要额外服务和持久化配置，不符合简单运维原则。
- 文件逐条 JSON：缺少原子并发更新、索引和有界清理。

## Decision 5 — 数据源能力评估

评估日期：2026-09-01；评估环境：当前部署机网络。

| 候选源 | 当前可达性 | 可用字段 | 限制 | 决策 |
|---|---|---|---|---|
| ESPN Site API（现有） | 当前探测返回 Akamai 403；代码/fixture 契约完整 | 赛程、比分、summary、球员 box score、leaders、可用 PBP | 部署区域可能被 CDN 拒绝；history 能力有限 | 保持主适配器，不删除；依赖持久缓存降低重复访问，并在失败时使用明确 partial fallback |
| NBA 官方 `cdn.nba.com` liveData | 当前探测返回 403 Access Denied | 官方今日比分、boxscore、PBP、部分 arena/attendance | 同样受 CDN 区域策略限制；历史索引和端点稳定性需额外验证 | 不在本次默认启用；保留为迁移候选，部署网络验证通过后再做独立 adapter |
| TheSportsDB v1 | 当前 HTTP 200 | 赛程、终场比分、场馆、队徽/视频链接 | 免费 demo 查询覆盖不完整；示例 season 查询仅返回 5 场；无完整 box score/PBP | 不作为比分/PBP 主源；未来可在有正式配额和覆盖监控时仅补场馆等非冲突元数据 |

**Decision**: 本次不为了“看起来更全”自动拼接覆盖不足的数据。先完整保存现有结构化 summary/PBP，并保留部分核验状态。新增 Provider 必须单独满足覆盖、冲突和部署可达性测试。

## Decision 6 — 前端加载感知

**Decision**: 最近/区间请求启动后 250ms 内不清空现有卡片；仅超过阈值时显示一次 `aria-live` 加载状态。请求完成或切换视图时取消计时器。

**Rationale**: SQLite 命中通常在毫秒级，立即闪出 loading 反而让缓存优势不可见；真实慢请求仍必须向用户反馈。

**Alternatives considered**:

- 永不显示 loading：违反 UI 反馈要求。
- 立即显示两个 loading 占位：造成当前重复文案和视觉闪烁。
