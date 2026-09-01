# Implementation Plan: 赛事回顾持久缓存与完整性

**Branch**: `002-persistent-highlights-cache` | **Date**: 2026-09-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-persistent-highlights-cache/spec.md`

## Summary

在现有 HighlightsService 与公开响应模型之间增加一个失败可退化的 SQLite 持久缓存，保存最近列表、历史日期/区间和比赛详情的已校验公开投影。历史终场数据采用 stale-while-revalidate；今日/进行中数据只允许短 TTL。详情写入采用单调完整度规则，不能用更少字段或更少 PBP 覆盖已有版本。Web 端将加载提示延迟 250ms，并去掉重复文案。Docker 使用具名卷保存数据库，容器重建后仍可复用。

## Technical Context

**Language/Version**: Python 3.12；浏览器端 ES2022

**Primary Dependencies**: FastAPI、Pydantic v2、Python 标准库 `sqlite3`；不新增运行时第三方包

**Storage**: SQLite 3，WAL 模式，默认 `/app/data/highlights.sqlite3`；本地测试使用临时目录或 `:memory:`

**Testing**: pytest/pytest-asyncio、httpx ASGI contract tests、Playwright Chromium E2E、Ruff

**Target Platform**: Linux 单实例 Docker 公网演示；本地 fixture 模式保持可复现

**Project Type**: FastAPI Web 服务 + 无构建前端

**Performance Goals**: 缓存命中 p95 < 200ms；命中路径外部 Provider 调用为 0；浏览器 250ms 内完成时不显示加载空态

**Constraints**: 公开接口结构向后兼容；只保存公开模型数据；单条 ≤ 2MiB；默认 ≤ 5000 条；数据库不可用时无损退化；今日结果不得 stale-while-revalidate

**Scale/Scope**: 单机面试演示、百级并发读取、最近 120 天/最多 93 天区间、每场最多 2000 条公开 PBP

## Constitution Check

| Principle / constraint | Gate | Design evidence |
|---|---|---|
| Specification-first | PASS | 独立 spec、验收场景、FR-001–015 与 SC-001–007 |
| Evidence-first NBA facts | PASS | 仅缓存 Pydantic 校验后的公开投影；冲突/缺失不猜测；完整度只阻止倒退，不创造字段 |
| Safety and respectful scope | PASS | 缓存仅位于 highlights 数据路径；聊天安全门顺序不变，不保存用户文本 |
| Contract/test-first | PASS | 新增持久缓存内部契约、单元/契约/E2E 测试，API envelope 不变 |
| Observable/reproducible/simple | PASS | 标准库 SQLite、可关闭配置、fixture 测试、通用计数器；无新服务依赖 |
| Public-data constraint | PASS | 不把不可达或覆盖不足的候选源默认接入；保留来源时间口径和部分核验状态 |
| UI loading/error constraint | PASS | 250ms 延迟加载、单一可见提示、缓存故障回退原路径 |

## Project Structure

### Documentation (this feature)

```text
specs/002-persistent-highlights-cache/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── highlights-cache.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
apps/api/src/
├── api/highlights_routes.py          # cache-first/SWR route orchestration
├── application/highlights.py         # existing verified projections
├── config.py                         # bounded cache configuration
├── infrastructure/
│   └── highlights_cache.py           # SQLite schema, reads, writes, leases, pruning
└── main.py                           # process lifecycle and application-owned cache

apps/web-demo/
└── app.js                            # delayed, non-duplicated loading state

tests/
├── unit/test_highlights_cache.py
├── contract/test_highlights.py
└── e2e/test_highlights.spec.ts

docker-compose.yml                    # persistent named volume
Dockerfile                            # writable mount point ownership
.env.example                          # cache settings
```

**Structure Decision**: 复用现有应用分层；SQLite 实现属于 infrastructure，路由只负责编排 cache-first/SWR，已有 HighlightsService 继续作为唯一事实投影逻辑。这样缓存失败不会污染 Provider/Verifier，也不会把存储细节引入领域模型。

## Phase 0 — Research decisions

详见 [research.md](research.md)。主要决策：

- 缓存公开响应投影而非 Provider 原始 JSON，避免持久化不受控字段和来源内部信息。
- 历史数据使用 stale-while-revalidate，今日/进行中数据只允许 fresh hit。
- 详情按确定性完整度单调升级，冲突时保留旧版本并放弃该次写入。
- ESPN 保持主路径；NBA 官方 CDN 因当前部署网络 403 暂不启用；TheSportsDB 仅列为覆盖有限的候选元数据源。

## Phase 1 — Design and contracts

- [data-model.md](data-model.md)：CacheEntry、RefreshLease、CompletenessProfile。
- [contracts/highlights-cache.md](contracts/highlights-cache.md)：稳定键、TTL、SWR、失败退化、公开 API 不变。
- [quickstart.md](quickstart.md)：首次写入、命中零 Provider、重启复用、容器卷和 E2E 验证。

## Traceability

| Requirement | Design / code touchpoint | Verification |
|---|---|---|
| FR-001–002, FR-011 | SQLite cache + app lifecycle + Docker volume | restart contract + deployment smoke |
| FR-003–004 | route cache policy and refresh lease | fresh/stale/current-day tests |
| FR-005–009 | schema, completeness, limits, prune | cache unit tests |
| FR-010 | null/failed cache fallback | unavailable-path contract test |
| FR-012 | delayed frontend loading | Playwright fast/slow request cases |
| FR-013–014 | research source matrix and no default secondary | provider config/research review |
| FR-015 | public wire payload only | payload/schema/privacy assertions |
| SC-001–007 | quickstart acceptance suite | pytest, E2E, restart smoke, source probe record |

## Post-design Constitution Check

PASS. SQLite is justified by SC-002 (restart persistence) and remains a single in-process dependency. No provider credentials, raw upstream payloads or user data are stored. All current safety and verification boundaries remain unchanged.

## Complexity Tracking

No constitution violations. The only added stateful component is an embedded standard-library database required for cross-restart reuse；an in-memory TTL cache cannot satisfy FR-001/SC-002.
