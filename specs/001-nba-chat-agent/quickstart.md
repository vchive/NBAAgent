# NBA Chat Agent — Quickstart and Validation Guide

**Feature**: [spec.md](spec.md)<br>
**Design**: [hld.md](hld.md), [lld.md](lld.md)

本指南对应当前仓库中的可交付版本。API 和零依赖 Web Demo 可以在没有外网、模型或数据源
凭据的情况下运行；`live`/`hybrid` 是可选的公开数据 profile。Docker/Compose、Playwright
和方案 PDF 均已提供；fixture 仍是本地最小路径。

## 1. Prerequisites

- Linux/macOS/WSL，Python 3.12（必需）。
- Git；联网 profile 需要能够访问 allow-list 中的公开 ESPN endpoint。
- Node.js 20+ 用于可选的 Playwright 浏览器验收；Web Demo 本身无需构建。
- Docker/Compose 可选；仓库提供 fixture 默认的镜像和 compose profile。
- 不需要提交或共享 API key；本地凭据（如未来接入模型）通过 `.env` 注入，`.env` 不得提交。

## 2. Install

```bash
git clone git@github.com:vchive/NBAAgent.git
cd NBAAgent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

连接 4173 Web Demo 时请复制配置模板（它会把本地静态页面加入 CORS allow-list；在
Codex 预览中使用 54572 端口时也已包含对应 loopback 来源）；仅做 API/curl 验证时可跳过：

```bash
cp .env.example .env       # 不要提交 .env
set -a; . ./.env; set +a    # Settings 读取进程环境变量，不会自动解析 .env 文件
```

默认配置是 `PUBLIC_DATA_MODE=fixture`、`LLM_MODE=mock`、`RUNTIME_PROFILE=template` 和
`HERMES_LITE_MODE=off`，所有本地示例均可离线完成。

> **访问密码**：对外部署请先运行 `make configure-app-password`，再使用带有
> `docker-compose.auth.yml` 的启动命令（`make deploy` 或 `make docker-up-silicon`）。登录后
> 才能访问聊天、赛事焦点和日期接口；`/healthz`、`/readyz`、`/livez` 保持公开探活。

> **BYOK 说明**：默认 profile 不读取或发送模型请求。live 演示使用
> `LLM_MODE=live`、`RUNTIME_PROFILE=hybrid`、`HERMES_LITE_MODE=embedded_agent` 和
> `FULL_INTELLIGENCE_ENABLED=true`；模型默认是
> `deepseek-ai/DeepSeek-V4-Flash`，接口为 SiliconFlow 的 OpenAI-compatible Chat
> Completions（参见 [SiliconFlow Chat Completions API 文档](https://api-docs.siliconflow.cn/docs/api/chat-completions-post)）。当前实现加载锁定的
> `hermes-agent==0.19.0` 和官方 `run_agent.AIAgent`，只注册 `nba_query`、`nba_schedule`、
> `nba_news` 三个工具；shell、文件系统、浏览器、通用搜索、MCP、memory、skills 和子代理
> 全部关闭。它运行在 API 进程内，并非正式隔离 Hermes sidecar；生产应迁移到 sidecar。
> Key 可用 `SILICONFLOW_API_KEY`（本地临时）或
> `SILICONFLOW_API_KEY_FILE`（Docker/Kubernetes secret 文件）注入。不要把 Key 写入
> `.env.example`、Dockerfile、镜像、Git、前端、日志或聊天消息。
> `HERMES_LITE_MODE=sidecar` 目前仅是未实现占位，会标记为 not-ready 并模板回退，避免误把
> 进程内直连当成隔离边界。
> 模型运行时和 `PUBLIC_DATA_MODE` 独立；`make deploy-live` 会组合 public hybrid 数据与
> auth/SiliconFlow override。公网启用前必须置于认证反代/VPN/受限安全组后，并设置 SiliconFlow
> 账户预算与限额，避免匿名请求消耗 BYOK 额度。
>
> 面试演示建议直接运行仓库内的隐藏输入脚本：`make configure-siliconflow-key`，再按
> [SiliconFlow BYOK 配置指南](../../docs/byok.md)启动 Compose；脚本只写入本地
> `secrets/siliconflow_api_key`（目录 `0700`、文件 `0600`）。

## 3. Start the single-port app (fixture mode)

在已激活虚拟环境的终端运行：

```bash
uvicorn apps.api.src.main:app --host 0.0.0.0 --port 8000
```

仓库中的 FastAPI 应用会在同一端口托管 `apps/web-demo`。因此部署机上直接访问
`http://<服务器IP>:8000/` 即可打开完整 UI；API 仍位于 `/api/...`。开发时若需要热重载，
可自行加上 `--reload`，但公开访问应使用上面的非 reload 命令。

API 提供：

- `GET /healthz`、`GET /livez`、`GET /readyz`：探活/就绪；
- `POST /api/v1/chat`：同步聊天；
- `POST /api/v1/chat/stream`：使用 `fetch` + `ReadableStream` 的 POST-SSE；
- `GET /api/v1/highlights?date=YYYY-MM-DD&timezone=Asia/Shanghai`：左栏赛事焦点投影。
- `GET /api/v1/highlights/availability?from=YYYY-MM-DD&to=YYYY-MM-DD&timezone=Asia/Shanghai`：
  最多 31 天的日期可用性（`available` / `empty` / `unknown`），供历史回顾日历置灰。

全智能分析是会话级开关：前端勾选后会在每个请求中发送 `intelligence_mode=full`。服务端在
`FULL_INTELLIGENCE_ENABLED=true` 且请求通过 SafetyGuard/上下文加载后、规则 Parser 之前
进入官方 Hermes Agent。Agent 可自行选择三个 NBA 工具理解错别字、解析日期并组织回答；
工具内部仍由确定性 Provider/Verifier/Derivation 负责事实、算术和 PBP。模型失败、超时、
重复调用、超预算或输出不合规时自动回到确定性通道。

## 4. Optional standalone Web Demo

如需单独调试静态页面，仍可另开终端（无需 Node 或 npm）：

```bash
python3 -m http.server 4173 --directory apps/web-demo
```

浏览器访问 <http://127.0.0.1:4173>。页面会在加载时短暂探测 `http://127.0.0.1:8000`：

- API 探测成功：聊天和 highlights 使用真实 FastAPI；
- API 不可达：自动回退内置 fixture，仍可演示流式状态、错误/重试、会话隔离和 PBP 回放。

部署到其他 API 地址时，可在页面加载 `api-client.js` 前设置
`window.COURTSIDE_API_BASE`；服务端的 `ALLOWED_ORIGINS` 必须包含静态页面来源。

## 5. Start with Docker Compose (optional)

在仓库根目录运行：

```bash
docker compose up --build
```

Compose 会以非 root 用户启动 fixture API 和同源 Web Demo，映射
`http://<服务器IP>:8000/`，并通过 `/healthz` 做容器健康检查。

停止服务：`docker compose down`。联网数据和 Hermes Agent 仍需显式配置，不会被镜像
默认值悄悄启用。

### 5.1 云主机 IP + 端口访问

如果部署在带 EIP/NAT 的云主机上，除了 Compose 的端口映射，还必须先配置访问密码，并在
主机防火墙和云安全组同时放行 TCP 8000。例如：

```bash
sudo ufw allow 8000/tcp comment 'NBAAgent web and API'
make configure-app-password
docker compose -f docker-compose.yml -f docker-compose.auth.yml up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/healthz
```

随后访问 `http://<EIP>:8000/`。安全组规则建议先将来源限制为你的公网 IP；如果本机探活
正常但外部仍超时，说明云安全组、EIP 端口映射或上游网络策略尚未放行，需在云控制台补充
入站 TCP 8000 规则。

## 6. API smoke checks

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/livez
curl -fsS http://127.0.0.1:8000/readyz

curl -fsS -X POST http://127.0.0.1:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"2025-26 总决赛 G4 谁得分最高？","client_timezone":"Asia/Shanghai"}'

curl -N -X POST http://127.0.0.1:8000/api/v1/chat/stream \
  -H 'Accept: text/event-stream' -H 'Content-Type: application/json' \
  -d '{"message":"2025-26 总决赛 G4 最后 5 秒发生了什么？","client_timezone":"Asia/Shanghai"}'

curl -fsS 'http://127.0.0.1:8000/api/v1/highlights?date=2026-06-12&timezone=Asia/Shanghai'
curl -fsS 'http://127.0.0.1:8000/api/v1/highlights?date=2026-06-13&timezone=Asia/Shanghai'
curl -fsS 'http://127.0.0.1:8000/api/v1/highlights/availability?from=2026-06-06&to=2026-06-13&timezone=Asia/Shanghai'
curl -i 'http://127.0.0.1:8000/api/v1/highlights?date=2099-01-01&timezone=Asia/Shanghai'
```

最后一个请求应为 `400 INVALID_PAYLOAD`（未来日期）；`2026-06-13` 是 fixture 的空日期，
应返回 `200` 且 `games` 为空。SSE 正常顺序为 `run.started → run.status* → message.delta* →
message.completed`；澄清、安全短路和技术错误分别使用对应的终止事件。原生
`EventSource` 只支持 GET，不能用于这个 POST-SSE 接口。

## 7. Product acceptance scenarios

| Check | Action | Expected |
|---|---|---|
| Core facts | 提问球队/球员/赛程/赛果/数据 | 结构化答案、证据状态和北京时间截至口径 |
| Time | 问“本赛季/最近/今天”或指定日期 | 使用 `YYYY-YY` 赛季与正确的时区日期 |
| Correction | 提供错误比分或得分前提 | 独立核验并礼貌纠正；缺数据不等同于错误 |
| PBP | 问全场最后 5 秒/指定节次 | 按完整 PBP 记录筛选，包含 0 和 5 秒边界 |
| Analysis | 问战术或复盘 | 先结论，再列已核验事实；推断与事实分层 |
| Follow-up | 同一 session 连续问“那场/最后那个球” | 上下文可解析；新 session 不串线 |
| Safety | 提交博彩、隐私、犯罪/假球等红线 | 1–2 句礼貌拒答，检索与缓存计数为 0 |
| Failure | 模拟 timeout/429/空/无效 JSON | 明确可重试或暂无数据，不展示旧/虚构数字 |
| Highlights | 切换“今日赛事/历史回顾”和日期 | 已确认无赛日置灰不可选；空/未来日期清除旧卡片并提示；待核验日期不误判为空 |
| UI | 断网、断流、窄屏、键盘操作 | 加载/错误/重试清晰，PBP 明确标注“非视频” |

左栏的“今日赛事 / 历史回顾”是 scoreboard/highlights 的日期投影，不是聊天中的
`HISTORY` intent。API 模式按服务端时钟解析今天；离线 Demo 固定展示 `2026-06-12` fixture，
该日期有比赛，`2026-06-13` 用于演示无比赛。项目没有已
授权的直播源或视频切片，右侧回放仅定位文字 PBP；未来的媒体卡片必须先通过版权和来源审核。

## 8. Automated verification

在仓库根目录、虚拟环境已激活时运行：

```bash
python -m pytest -q
python -m compileall -q apps
node --check apps/web-demo/app.js       # 可选：需要 Node.js
python -m apps.api.src.evaluation.cli --repeat 1
npm ci && npx playwright install --with-deps chromium && npm run e2e
```

当前测试覆盖领域模型、时间/赛季/PBP 算法、安全守卫、Provider/HTTP/SSE 契约、会话/缓存
边界、ESPN 适配器和评测安全映射。`make test`、`make api`、`make demo`、`make eval` 和
`make docker-up` 是上述命令的快捷入口；`make lint` 可用于开发期检查，当前代码通过 Ruff
检查。

评测 runner、报告模块和独立 CLI 已存在于 `apps/api/src/evaluation/`，黄金集当前包含
A–I、安全/范围外和 3 条全智能验收题，共 21 条案例；完整集成回放和 Playwright E2E 可分别通过 pytest 和
`npm run e2e` 验证。

## 9. Optional live/hybrid profile

ESPN adapter 已实现为 HTTPS allow-list、超时和响应大小受限的可替换 Provider；上线前必须
重新确认公开数据服务的条款、robots、频率限制和稳定性。无模型凭据时仍可使用模板回答：

```bash
export PUBLIC_DATA_MODE=live       # 只访问 allow-list 公开 endpoint
export LLM_MODE=mock
export RUNTIME_PROFILE=template
export HERMES_LITE_MODE=off
uvicorn apps.api.src.main:app --host 0.0.0.0 --port 8000
```

### 9.1 Controlled DuckDuckGo search

新闻、背景和长尾问题可在 live/hybrid profile 中显式开启受控搜索：

```bash
export DDG_SEARCH_ENABLED=true
export DDG_TIMEOUT_SECONDS=3
export DDG_MAX_RESULTS=5
```

系统只访问固定的 `https://api.duckduckgo.com/` Instant Answer 端点，不接受用户 URL，
并限制查询、结果数、响应大小和超时。返回内容会移除 HTML、脚本、控制字符和提示注入，
以 `SEARCH`/部分核验证据参与新闻回答；它不能单独证明比分、排名、统计或逐回合事实。
搜索失败会保留 NBA 结构化数据回答或给出暂无数据，不会阻塞核心问答。

开发期也可用 `PUBLIC_DATA_MODE=hybrid`：先尝试公开源，发生有类型的上游错误后才使用本地
fixture fallback；权威空结果不会被旧 fixture 覆盖。

full 模式使用官方 `HermesAgentRuntime`，在规则解析之前执行有界 Agent loop；旧
`HermesRuntimeAdapter` 只保留给 hybrid 战术/复盘的单轮 composer。`embedded_agent` 适合
受控面试演示，正式 sidecar 隔离仍是后续部署形态。没有 key 时 Agent 不发请求并回退模板，
`/healthz`/`/readyz` 会反映配置未就绪。

本地 direct BYOK 示例（不要把真实 key 写进 shell 历史或提交文件）：

```bash
export LLM_MODE=live
export RUNTIME_PROFILE=hybrid
export HERMES_LITE_MODE=embedded_agent
export FULL_INTELLIGENCE_ENABLED=true
export HERMES_LITE_TIMEOUT_MS=40000
export LLM_TIMEOUT_SECONDS=20
export REQUEST_DEADLINE_MS=45000
./scripts/configure-siliconflow-key.sh
export SILICONFLOW_API_KEY_FILE="$PWD/secrets/siliconflow_api_key"
export SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
export SILICONFLOW_MODEL=deepseek-ai/DeepSeek-V4-Flash
uvicorn apps.api.src.main:app --host 0.0.0.0 --port 8000
```

容器部署建议使用可选的 `docker-compose.siliconflow.yml`，将单独的
`secrets/siliconflow_api_key` 挂载到 `SILICONFLOW_API_KEY_FILE`；不要用 `env_file` 把整份
`.env` 传入容器。启用前验证模型账户可用性、额度、`/readyz`、429/超时/撤 key 回退和
日志中没有 `Authorization`/token。向第三方发送的内容仅包括清理后的问题、泛化上下文、
NBA 工具 schema 和清洗后的工具观察，不包括 Provider URL、原始 JSON、
证据/会话 ID 或凭据。
该 override 默认不切换 `PUBLIC_DATA_MODE`；如需公开 ESPN 数据，必须另行设置 `live`/`hybrid`
并审核条款。未配置认证时不要把 live profile 直接暴露给公网用户。

公开交付使用 `docker-compose.public.yml`：服务先请求 allow-list 的公开数据源，发生有类型的
上游失败时才回退到 fixture；不会把固定演示日期当作当天。先配置访问密码：

```bash
make configure-app-password
make deploy                 # hybrid 公开数据 + 共享密码
make deploy-live            # 额外启用 SiliconFlow + 官方 Hermes Agent（需要模型 key）
make deploy-status
```

面试官访问 `http://<EIP>:8000/` 后输入共享密码；`/healthz`、`/readyz`、`/livez` 保持公开。
如只需本地离线复现，继续使用基础 `docker-compose.yml`，它不会访问外网。

### 9.2 Public live acceptance evidence (2026-08-30, Asia/Shanghai)

最终交付使用 `make deploy-live` 构建并启动 base + public + auth + SiliconFlow 四层 Compose。
验收过程中没有输出访问密码或模型 Key：

- 本机与 `http://115.190.174.39:8000/readyz` 均返回 HTTP 200；容器状态为 `healthy`。
- `/readyz` 显示 `mode=hybrid`、`full_intelligence=true`、`web_search=true`、
  `hermes=ok`、`auth=ok`。
- 容器内官方包为 `hermes-agent==0.19.0`；capability self-test 通过，工具集合精确为
  `nba_news`、`nba_query`、`nba_schedule`。
- 公网登录成功后，以下请求均显式携带 `intelligence_mode=full`：

| Request | Result | Composition | Acceptance evidence |
|---|---|---|---|
| `nihao` | `completed` | `agent/used` | 自然问候，不要求补充查询对象，不声明日期或赛季事实 |
| `nishishei` / `你是谁` | `completed` | `agent/used` 或 `deterministic/not_requested` | 身份/能力介绍不进入 NBA 澄清；模型不可用时使用本地能力提示 |
| `下周有比赛买` | `completed` | `agent/used` | 返回完整北京时间范围 `2026-08-31 至 2026-09-06`，明确没有返回比赛 |
| `下周有比赛吗` | `completed` | `agent/used` | 同一完整范围和空赛程结论，不推测休赛期原因 |
| 博彩策略红线 | `blocked` | `deterministic/not_requested` | 1 ms 本地短路，Agent 和工具调用均为 0 |

最终自动化门禁：pytest `297 passed`，Ruff/JS/compileall 通过，Playwright `5 passed`。

## 10. Delivery checklist

交付复核清单（当前仓库已完成实现；目标环境上线时按需复核）：

1. 在干净环境重跑本指南和完整测试，并保存评测报告；
2. 在目标主机执行 `make deploy` 或 `make deploy-live`，保存 `/healthz`、`/readyz` 和登录后的聊天验收证据；
3. 持续扩充黄金题并在目标环境记录七维评分、TTFT、完整延迟和安全否决；
4. 审核数据源/视频版权后再考虑媒体嵌入；
5. 提交 [`docs/solution.pdf`](../../docs/solution.pdf)，不在用户答案中暴露 Provider URL、原始字段、内部 trace 或凭据。
