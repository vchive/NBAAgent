# NBA Chat Agent

面向中文球迷的 NBA Chat Agent 笔试题项目，按 GitHub SpecKit 的 SDD（Specification-
Driven Development）流程推进。

## 当前阶段：fixture-first Agent 垂直切片（fixture MVP gate passed；final delivery gates pending）

已完成需求规格、研究记录、HLD、LLD、数据模型、接口契约和验收指南，并打通了默认
fixture/mock 模式的 FastAPI Agent：同步聊天、POST SSE、会话隔离、事实核验、PBP/系列赛
确定性推导、安全短路、重试/缓存和“赛事焦点/历史回顾”日期投影均可离线运行。FastAPI
在仓库包含 Web Demo 时会从同一端口托管 UI，因此可以直接通过一个 `IP:端口` 访问完整演示。
ESPN 与 Hermes-lite 保留为可替换适配器，默认关闭，不需要任何凭据。

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
```

运行测试：

```bash
python3 -m pytest -q
```

可选的容器启动：

```bash
docker compose up --build
```

Compose 默认将完整应用暴露在 `http://<服务器IP>:8000/`。
后台部署可使用 `make deploy`，查看状态用 `make deploy-status`。

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
- [方案说明源文档](docs/solution.md)

## 开发流程

```text
constitution → specify → clarify（如需要）→ plan/HLD/LLD → tasks → implement → verify
```

SpecKit 项目治理原则位于 [.specify/memory/constitution.md](.specify/memory/constitution.md)。
所有提交使用仓库主人 `vchive` 的 Git 身份。

## 下一步

后续按 [`specs/001-nba-chat-agent/tasks.md`](specs/001-nba-chat-agent/tasks.md) 继续补齐
live provider、受限 Hermes sidecar、Playwright、公网部署验收和方案 PDF；fixture 模式始终
是本地可复现的默认路径。
