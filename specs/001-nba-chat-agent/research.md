# Research Notes: NBA Chat Agent

**Feature**: [spec.md](spec.md)
**Date**: 2026-08-30

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

**Decision**: 检索前安全判定始终是第一道门。门后提供两条可回退通道：`hybrid` 使用
“意图/时间解析 → 数据计划 → 取数/归一化 → 事实核验/确定性推导 → 回答编排”；`full`
由 Agent 先理解问题并选择服务器批准的 NBA 工具，工具内部仍执行同一解析、Provider、核验
和推导链。客观数字始终由确定性事实链提供，Agent 负责规划、容错理解和基于工具观察组织
解释。

**Rationale**:

- PDF 2.4 明确要求敏感话题不得先检索再评论。
- PDF 2.3 要求累计和逐回合事实真实核对，不能凭记忆或让模型心算。
- 分层后可以对每一段单独测试，并在 Agent/模型不可用时仍提供事实回答。

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

## Decision 7: SiliconFlow BYOK remains the model transport

**Decision**: 模型传输继续使用 SiliconFlow OpenAI-compatible Chat Completions、Bearer
鉴权和默认模型 `deepseek-ai/DeepSeek-V4-Flash`。`hybrid` 可沿用现有单轮 composer；`full`
由 Hermes Agent 使用同一 BYOK 配置完成 function/tool calling。只有显式 live + Agent 配置
才发起请求；fixture/mock/template 路径保持离线。

**Rationale**:

- 用户已指定 SiliconFlow API 和模型，OpenAI-compatible 契约可复用现有 runtime port。
- 模型可在 `full` 模式规划受控工具调用，但工具返回的确定性事实、算术、PBP 选择和 Output
  Guard 仍由本地代码负责。
- 缺 key、超时、限流、无效响应或不安全输出时保留模板答案，避免模型成为单点依赖。

**Security boundary**: 模型只接收清理后的问题、当前北京时间、泛化会话上下文、工具 schema
与清洗后的工具结果；不接收 Provider URL/原始 JSON、证据 ID、凭据或思维链。正式生产
sidecar 仍需独立部署与审计，不能把进程内嵌入当作隔离边界。

## Decision 8: Integrate the official Hermes Agent package and expose only NBA tools

**Decision**: 面试演示锁定 PyPI `hermes-agent==0.19.0`（MIT，Python 3.11–3.13），使用其
`run_agent.AIAgent`、自动 tool-calling loop、iteration budget 和 tool registry。进程启动时
注册独立 `nba` toolset；每次 Agent 仅以 `enabled_toolsets=["nba"]` 启动，并显式关闭内置
terminal、file、browser、MCP、memory、skills、delegation 和通用 web 工具。首个工具集合为：

- `nba_query`：把自然语言子问题送入现有确定性查询/核验用例；
- `nba_schedule`：查询带日期表达和可选球队条件的已核验赛程；
- `nba_news`：通过现有受控新闻/背景 Provider 查询，不接受 URL。

工具 handler 通过 Hermes 提供的 `task_id` 查找一次性 request bridge；bridge 只在请求 deadline
内可用，调用完成即删除。这样 Hermes 负责规划，NBA API 仍拥有 Provider、缓存、安全、证据
和会话。

**Rationale**:

- 0.19.0 wheel 暴露可嵌入 `AIAgent(base_url, api_key, model, max_iterations,
  enabled_toolsets, ...)` 和自定义 `registry.register(...)`，无需复制其 agent loop。
- SiliconFlow 实测在强制 function choice 下对默认模型返回标准 `tool_calls`，说明传输契约兼容；
  system prompt 必须提供当前北京时间并明确何时调用工具，避免模型自行假设日期。
- 官方包依赖与现有 FastAPI/Pydantic/httpx 约束兼容，但体积较大，因此版本锁定并保持可关闭。
- 一个请求最多 4 个 Agent 迭代、4 次工具调用和一个总 deadline；重复同参工具调用由 bridge
  去重/拒绝，防止循环和额度失控。

**Alternatives considered**:

- 继续扩展自研 `HermesRuntimeAdapter`：只是模型客户端，无法体现真正 Agent loop，排除。
- 允许 Hermes 内置 web/browser/terminal：开发快但扩大 SSRF、文件和命令执行面，排除。
- 立刻拆独立 sidecar：隔离最好，但本轮部署和调试成本更高；保留为生产硬化路径。

## Decision 9: Full mode branches before deterministic parsing and falls back safely

**Decision**: `ChatUseCase` 在认证、安全和会话解析之后检查 `intelligence_mode=full`。全模式先
调用 Agent；Agent 工具以内部 `deterministic_tool` 标记重入同一用例，禁止再次进入 Agent，
从而复用所有 Provider/Verifier/Derivation 逻辑且无递归。问候可以零工具回答；空数据工具结果
携带解析出的日期范围、状态和限制，使 Agent 能解释休赛期/无赛日而非输出通用空模板。

**Fallback**: Agent 包未安装、key 缺失、tool call 无效、超时、重复工具、超预算或输出守卫
失败时，原请求回到既有 deterministic/hybrid 路径。红线请求在分支之前完成，模型和工具调用
始终为零。

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
