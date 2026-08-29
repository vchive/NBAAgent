# NBA Chat Agent

面向中文球迷的 NBA Chat Agent 笔试题项目，按 GitHub SpecKit 的 SDD（Specification-
Driven Development）流程推进。

## 当前阶段：可交付 Agent（fixture 默认、public hybrid 可部署）

已完成需求规格、研究记录、HLD、LLD、数据模型、接口契约和验收指南，并打通了默认
fixture/mock 模式的 FastAPI Agent：同步聊天、POST SSE、会话隔离、事实核验、PBP/系列赛
确定性推导、安全短路、重试/缓存和“赛事焦点/历史回顾”日期投影均可离线运行。FastAPI
在仓库包含 Web Demo 时会从同一端口托管 UI，因此可以直接通过一个 `IP:端口` 访问完整演示。
ESPN 与 Hermes-lite 保留为可替换适配器，默认关闭，不需要任何凭据。

> **SiliconFlow BYOK 状态**：默认仍是完全离线的 `template`/`mock` 模式（不会读取或发送
> 模型请求）。显式设置 `LLM_MODE=live`、`RUNTIME_PROFILE=hybrid`（或 `hermes`）以及
> `HERMES_LITE_MODE=embedded_spike` 后，F/G 战术与复盘问题才会调用受限的 SiliconFlow
> OpenAI-compatible Chat Completions（[API 文档](https://api-docs.siliconflow.cn/docs/api/chat-completions-post)）；默认模型为 `deepseek-ai/DeepSeek-V4-Flash`。
> 这是当前 API 进程内的 direct BYOK adapter，不是已部署的独立 Hermes sidecar；生产环境
> 应改用隔离 sidecar。Key 仅通过 `SILICONFLOW_API_KEY`（本地）或
> `SILICONFLOW_API_KEY_FILE`（挂载 secret）注入，绝不能提交到仓库、镜像、前端、日志或聊天。
> `HERMES_LITE_MODE=sidecar` 目前是保守的未实现占位，会保持 not-ready/模板回退，不会
> 偷换成进程内直连。
> `LLM_MODE=live` 只切换分析措辞模型，和 `PUBLIC_DATA_MODE` 相互独立；Compose 示例仍使用
> 本地 fixture 事实。若把 live profile 暴露到公网，任何可访问者都可能消耗 BYOK 额度，必须
> 放在认证反代/VPN/受限安全组后，并配置供应商预算与限额。

启动 API：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env  # 可选；不要提交 .env
uvicorn apps.api.src.main:app --host 0.0.0.0 --port 8000
```

启动后打开 `http://<服务器IP>:8000/` 即可看到 UI；API 和 UI 使用同源地址，不需要额外
启动 4173 静态服务器。若只想单独预览静态文件，仍可使用下方的 Web Demo 命令。

探活和示例请求：

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS -X POST http://127.0.0.1:8000/api/v1/chat \
  -H 'content-type: application/json' \
  -d '{"message":"2025-26 总决赛 G4 谁得分最高？"}'
curl -fsS 'http://127.0.0.1:8000/api/v1/highlights?date=2026-06-12&timezone=Asia/Shanghai'
curl -fsS 'http://127.0.0.1:8000/api/v1/highlights/availability?from=2026-06-06&to=2026-06-13&timezone=Asia/Shanghai'
```

运行测试：

```bash
python3 -m pytest -q
```

浏览器验收（Node.js 20+，首次运行需下载 Chromium）：

```bash
npm ci
npx playwright install --with-deps chromium
npm run e2e
```

可选的容器启动（本地 fixture，不启用登录）：

```bash
docker compose up --build
```

Compose 默认将完整应用暴露在 `http://<服务器IP>:8000/`。
后台对外部署请先设置访问密码，再使用 `make deploy`；查看状态用 `make deploy-status`。

```bash
make configure-app-password   # 隐藏输入，写入 secrets/app_password
make deploy                   # 自动加入 docker-compose.auth.yml
```

登录后才可访问聊天、赛事焦点和日期接口；`/healthz`、`/readyz`、`/livez` 仍可用于探活。
完整说明见 [`docs/auth.md`](docs/auth.md)。

启用 SiliconFlow（仅在你已准备好自己的 key 时；完整说明见
[`docs/byok.md`](docs/byok.md)）：

```bash
make configure-app-password
make configure-siliconflow-key   # 交互式隐藏输入，写入 root:10001 / 0640 secret 文件
docker compose -f docker-compose.yml -f docker-compose.auth.yml -f docker-compose.siliconflow.yml up -d --build
curl -fsS http://127.0.0.1:8000/readyz
```

上述 override 会把 key 和访问密码作为 Docker secret 文件挂载，不会写入镜像层或环境变量。没有
授权 key 时不要切换 live；服务会保持模板回退并在 `/healthz`/`/readyz` 标为 degraded。
该 profile 的 `PUBLIC_DATA_MODE` 仍为 `fixture`，只验证模型措辞链路；要访问 ESPN 公开数据，
另行显式设置 `PUBLIC_DATA_MODE=live` 或 `hybrid`，并先审核数据源条款。live profile 不应直接
暴露未认证的 `8000` 端口。

在云主机上还需要同时放行两层网络策略：本机执行
`ufw allow 8000/tcp`，并在云厂商安全组添加一条入站 TCP 8000 规则（演示阶段可先限制为
你的公网 IP）。完成后使用 `http://<EIP>:8000/` 访问；若只能在本机访问，通常是云安全组
或 EIP/NAT 尚未做端口映射。

评测 CLI：

```bash
python -m apps.api.src.evaluation.cli --repeat 3
```

## UI 交互 Demo

赛事转播风格的零依赖前端 Demo 已放在 [`apps/web-demo`](apps/web-demo/)。它用本地
fixture 演示聊天流式状态、事实/分析分层、错误与重试、会话隔离，以及 Q2/Q3/Q4/OT
节次切换；回放是 PBP 事件定位，不是视频播放，OT 标签会明确展示本场无加时的空状态。

启动方式：

```bash
python3 -m http.server 4173 --directory apps/web-demo
```

打开 <http://127.0.0.1:4173> 即可查看。详细的交互探针和真实 API 接入替换点见
[`apps/web-demo/README.md`](apps/web-demo/README.md)。

- [需求规格](specs/001-nba-chat-agent/spec.md)
- [实施计划](specs/001-nba-chat-agent/plan.md)
- [研究决策](specs/001-nba-chat-agent/research.md)
- [HLD](specs/001-nba-chat-agent/hld.md)
- [LLD](specs/001-nba-chat-agent/lld.md)
- [数据模型](specs/001-nba-chat-agent/data-model.md)
- [HTTP/SSE 契约](specs/001-nba-chat-agent/contracts/http-api.md)
- [Provider 契约](specs/001-nba-chat-agent/contracts/provider-adapter.md)
- [评测契约](specs/001-nba-chat-agent/contracts/evaluation.md)
- [本地验收指南](specs/001-nba-chat-agent/quickstart.md)
- [SiliconFlow BYOK 配置指南](docs/byok.md)
- [方案说明源文档](docs/solution.md)
- [方案说明 PDF](docs/solution.pdf)

## 开发流程

```text
constitution → specify → clarify（如需要）→ plan/HLD/LLD → tasks → implement → verify
```

SpecKit 项目治理原则位于 [.specify/memory/constitution.md](.specify/memory/constitution.md)。
所有提交使用仓库主人 `vchive` 的 Git 身份。

## 交付 profile

方案说明 PDF 已生成在 [`docs/solution.pdf`](docs/solution.pdf)。本地默认仍是 fixture/mock，
公网交付使用 `make deploy`（hybrid 公开数据 + 访问密码）；如需启用 SiliconFlow 分析，再使用
`make deploy-live`。正式隔离 Hermes sidecar 仍是后续可替换部署形态，当前 embedded Spike 只用于
面试演示和受限模型链路验证。
