# Research Notes: NBA Chat Agent

**Feature**: [spec.md](spec.md)
**Date**: 2026-08-26

本文件记录 Phase 0 的设计研究。它区分 PDF 明确要求、实测结果和本项目的可撤销
设计决策。PDF 没有指定厂商、语言、模型、数据库、部署平台或具体时延数字，因此
下面的技术选择不是题目硬性约束。

## Decision 1: Use a replaceable public-data gateway

**Decision**: 通过统一的 Provider Gateway 访问公开互联网数据；首个适配器以 ESPN
公开 Web API 为主，保留可插拔的官方或其他可靠来源适配器作为回退。业务层只依赖
领域协议，不直接依赖某个供应商的响应格式。

**Rationale**:

- PDF 要求联网取数且禁止依赖内部数据库。
- 2026-08-26 实测 ESPN 的 scoreboard、summary 和 play-by-play 端点可返回赛程、
  比赛摘要、box score 和逐回合事件，足以覆盖题目 A–E 的主要事实查询。
- 供应商接口的稳定性、配额和授权可能变化，适配器隔离可以降低替换成本。
- 所有响应都能归一化为统一的事实和证据对象，便于核验、缓存和离线测试。

**Alternatives considered**:

- 直接抓取 NBA 官方页面：权威性高，但当前网络实测存在 403/超时，且页面结构变化
  会使解析脆弱；保留为可选回退而不是唯一依赖。
- Basketball-Reference：历史数据有价值，但当前网络实测可能遇到 Cloudflare 403；
  不作为唯一在线来源。
- 自建内部数据库：与 PDF 的“自行从公开互联网获取、无内部数据库”要求冲突，排除。

## Decision 2: Separate safety, fact retrieval and generative analysis

**Decision**: 请求管线固定为“安全判定 → 意图/时间解析 → 数据计划 → 取数/归一化
→ 事实核验/确定性推导 → 回答编排 → 输出安全检查”。安全命中在任何外部检索前
短路；客观数字由模板或确定性渲染器输出，生成模型只负责有证据的解释、战术和复盘
语言化。

**Rationale**:

- PDF 2.4 明确要求敏感话题不得先检索再评论。
- PDF 2.3 要求累计和逐回合事实真实核对，不能凭记忆或让模型心算。
- 分层后可以对每一段单独测试，并在模型不可用时仍提供事实回答。

**Alternatives considered**:

- 单次 LLM 端到端回答：无法保证来源、算术和安全短路，排除。
- 先搜索再做安全分类：违反红线要求，排除。

## Decision 3: Canonical time and season policy

**Decision**: 领域层内部使用明确的 UTC 时间戳，用户界面默认转换为
`Asia/Shanghai`（UTC+8）；赛季使用 `YYYY-YY` 标签（例如 `2025-26`）。相对时间
词基于当前赛季上下文解析，并由可注入的 Clock 提供“当前时间”。

**Rationale**: PDF 2.2 要求源时区转北京时间、赛季跨年，并正确解释“本赛季/最近”等
时效表达。可注入时钟也让历史题和评测可重复。

**Alternatives considered**:

- 直接沿用来源时区：用户容易误解北京时间日期，排除。
- 按自然年命名赛季：会在跨年季后赛中产生错误，排除。

## Decision 4: Web chat with streaming-compatible API

**Decision**: 产品提供 Web 聊天界面；服务同时支持普通请求和 Server-Sent Events
（SSE）流式请求。流式是产品体验目标，普通请求用于自动化测试、降级和评测。

**Rationale**:

- PDF 4.1 要求 Web 聊天、多轮及加载/错误反馈，并鼓励流式输出。
- 普通请求使契约测试和离线演示不依赖浏览器或长连接。
- SSE 足以承载状态、增量文本、完成结果和错误，不引入双向协议复杂度。

**Alternatives considered**:

- 仅同步 HTTP：实现简单但无法满足流式加分项和进度反馈。
- WebSocket：适合双向实时协作，但本题只有服务端向客户端增量输出，复杂度过高。

## Decision 5: Evaluation as a first-class artifact

**Decision**: 建立 A–I 参考题型的黄金问题集、可重复回放、事实参考答案、七维评分
和时延记录。安全红线单独记录为否决项，不并入加权分数。

**Rationale**: PDF 4.2 允许面试官重复采集，且明确权重为题意理解 20%、事实准确
20%、完整性 15%、表达规范 10%、结构可读 10%、多轮一致 10%、性能响应时延 15%。
将评测固化为数据文件和脚本，能够复现结果并追踪回归。

**Alternatives considered**:

- 只做人工演示：无法比较多次运行的一致性，也无法证明安全短路和时延。
- 只做单一准确率：遗漏表达、多轮和性能等题目评分维度，排除。

## Decision 6: Explicit provenance without exposing internals

**Decision**: 内部每个事实都保留来源标识、URL、获取时间、数据截至时间和可信度；
面向用户只展示“数据截至北京时间 …”和核验状态等友好信息，不展示供应商名称、API
端点、字段名、提示词或调用链。是否展示公开链接作为后续产品决策，不写入首版硬约束。

**Rationale**: 同时满足可核验性、可观测性和 PDF 2.1 的“不暴露内部技术细节”。

## Resolved unknowns and remaining risks

| Topic | Resolution for design | Remaining risk / mitigation |
|---|---|---|
| Data provider | ESPN-first adapter + replaceable fallback | 速率限制、授权和接口变化；缓存、重试、fixture、健康检查 |
| LLM/model | Optional provider behind a narrow composer contract | 模型不可用或幻觉；事实模板优先、输出守卫、mock mode |
| Storage | Session-scoped lightweight store and TTL cache; no internal NBA DB | 进程重启丢失上下文可接受于 v1；接口抽象便于升级 |
| Latency | Project target: 90% complete answers under 5s | PDF 无阈值；记录 TTFT/完整时延并按面试官档位评估 |
| Deployment | Publicly reachable web + API, containerized local reproduction | Hosting choice deferred; provide one-click local and deployment instructions |
| Source licensing | Verify terms and robots/rate limits before production | Fallback and cached fixtures for demo; do not redistribute restricted data |

## Reproducible source probe (2026-08-26)

在开发环境于 2026-08-26（Asia/Shanghai）使用只读 HTTP 探针验证了以下公开 Web API
能力（响应状态 200；这不是供应商 SLA，也不代表官方背书）。等价探针命令为：

```bash
curl -fsS -A 'NBAAgent-research-probe/0.1' \
  'https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20260825' \
  | head -c 200
```

真实实现必须在上线前重新探测、核查服务条款/robots/访问频率，并把结果保存为脱敏
fixture；一次成功响应不能当作长期稳定性保证。

| Capability | Probe shape | Observed fields / use |
|---|---|---|
| Scoreboard | `https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD` | events、date、status、competitors、scores |
| Game summary | `https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={id}` | header、boxscore、leaders、series context（视事件而定） |
| Play-by-play | `https://cdn.espn.com/core/nba/playbyplay?xhr=1&gameId={id}` | plays、period、clock、participants、scores、scoreValue |
| Athlete/profile | `https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{id}` | 稳定 ID、姓名和资料 |
| Season stats | `https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/{year}/types/2/athletes/{id}/statistics` | 赛季统计（需 adapter 映射） |

探针还观察到 `site.api.espn.com` 和 NBA CDN 在当前网络可能返回 403/超时；因此实现必须
保留 fallback 和 fixture。日期请求应按日分片，避免月份响应过大。所有真实端点、原始字段
和供应商名称只出现在本方案/内部日志，不进入用户回答。

### Season-year mapping evidence

Provider 的赛季 year 使用结束年份：例如请求 2025 年 10 月的赛程时，响应可标记
`displayName: 2025-26`、`year: 2026`。适配器不得把领域 `2025-26` 的首年 2025 直接
当作 Provider season year。该映射必须由 fixture 和固定 Clock 测试锁定。
