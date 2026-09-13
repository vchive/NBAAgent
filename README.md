# 种花 Agent

面向中文家庭种植者的花卉问答助手。你可以直接用自然语言询问选花、浇水、光照、
土壤、施肥、修剪、繁殖、季节安排和常见病虫害；连续追问时，服务会在同一网页会话内
保留最近的植物与环境上下文。

## 当前交付

本仓库按 Specification-Driven Development（SDD）实现 `005-flower-agent`：

- 默认产品域和首屏品牌为“种花 Agent”，默认是**漫游模式**，不绑定具体植物。
- 内置带别名和条件说明的离线花卉知识集；没有网络或额度时仍能给出保守、可执行的建议。
- 全智能模式使用受控的无状态对话运行时，由应用传入最近四个完整回合和有界种植上下文。
- 运行时只允许花卉知识查询、养护计划和受控资料搜索三类能力；不能访问 Shell、文件、
  任意网址、浏览器、MCP、原生记忆或子代理。
- 搜索结果只作为补充观察，清洗掉链接、提示注入和内部元数据；搜索/模型额度耗尽时保留
  本地答案，并显示可理解的重试提示。
- 农药混用、未知植物误食、人或宠物暴露等请求在检索和模型调用前短路，优先给出安全处置
  与专业求助方向。
- 同步和 SSE 接口共享会话、幂等、取消、大小限制及公开输出清洗契约。

详细规格、数据模型和接口契约见 [`specs/005-flower-agent`](specs/005-flower-agent/)。

## 本地运行

需要 Python 3.12。默认配置是离线、模板回答，不读取任何模型或搜索密钥：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
uvicorn apps.api.src.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/> 查看页面。静态页面也可以单独预览：

```bash
python3 -m http.server 4173 --bind 127.0.0.1 --directory apps/web-demo
```

服务默认只使用回环地址；不要在没有认证反向代理和安全组限制时绑定公网地址。停止本地
进程后可用 `ss -ltnp | grep ':8000'` 确认没有监听器。

## API 示例

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS -X POST http://127.0.0.1:8000/api/v1/chat \
  -H 'content-type: application/json' \
  -d '{"message":"北阳台适合种什么花？"}'
```

请求支持可选 `session_id`、`client_message_id`、`client_timezone` 和
`intelligence_mode`（`hybrid` 或 `full`）。省略 `session_id` 会创建新会话；网页点击
“新对话”才会清空上下文。完整同步/SSE 约定见
[`specs/005-flower-agent/contracts/http-api.md`](specs/005-flower-agent/contracts/http-api.md)。

## 在线资料与全智能模式

在线能力由服务端配置，浏览器永远不能选择端点或携带密钥。可按部署需要启用阿里云 IQS、
百度/千帆或 DuckDuckGo 的受控适配器；密钥优先通过 Docker secret 文件注入。示例配置文件
仍只包含占位符，真实 key 不得写入仓库、镜像、日志或聊天记录。

当 `FULL_INTELLIGENCE_ENABLED=true` 且运行时配置为 `embedded_agent` 时，`full` 请求会
进入受控智能链路；安全短路和公开输出清洗始终由应用负责。运行时、搜索和网络故障会映射
为 provider-neutral 的提示，例如“智能回答额度已用完”或“在线资料暂时不可用”，不会把
供应商、模型、端点、工具名或原始错误返回给用户。

## Docker / Compose

规范服务名和镜像名是 `flower-agent` / `flower-agent:fixture`。基础 Compose 配置仍是
离线 fixture，并将宿主端口限制在回环地址：

```bash
docker compose up --build
```

数据卷名为 `floweragent-data`。旧部署脚本需要迁移时，可显式使用兼容 profile（不会在
普通启动时额外创建容器）：

```bash
docker compose --profile nba-compat up --build nba-agent
```

`make docker-build-nba` 和 `nba-agent` Python 命令也保留为迁移别名；它们仍启动同一个
花卉应用，不会恢复旧的默认产品界面。

需要认证的受限部署：

```bash
make configure-app-password
make deploy
```

需要启用模型和在线资料时，再配置相应 secret 后运行 `make deploy-live`。公网部署前必须
通过认证反向代理/VPN/安全组限制访问，并设置供应商预算与速率上限。完整操作说明见
[`specs/005-flower-agent/quickstart.md`](specs/005-flower-agent/quickstart.md)。

## 测试与质量门禁

```bash
python3 -m ruff check apps/api tests
python3 -m pytest -q
npx playwright test tests/e2e/test_flower_ui.spec.ts --project=chromium --reporter=line
```

测试覆盖离线问答、连续指代、搜索/模型故障、额度提示、危险请求短路、公开输出边界、
HTTP/SSE 契约及浏览器首屏。评测时不要把实时公网数据或密钥作为测试前提。

## 目录导航

- [花卉规格与验收契约](specs/005-flower-agent/spec.md)
- [实施计划](specs/005-flower-agent/plan.md)
- [快速验收](specs/005-flower-agent/quickstart.md)
- [HTTP/SSE 契约](specs/005-flower-agent/contracts/http-api.md)
- [受控 Agent 契约](specs/005-flower-agent/contracts/agent-runtime.md)
- [搜索适配器契约](specs/005-flower-agent/contracts/search-provider.md)
- [前端说明](apps/web-demo/README.md)

仓库中 `specs/001-nba-chat-agent`、`docs/solution.pdf` 等文件是历史兼容资料，未作为默认
花卉产品入口；它们保留用于迁移和审计，不应作为当前界面文案或运行时上下文。
