# SiliconFlow BYOK 配置指南

> 对外演示同时需要访问密码。先运行 `make configure-app-password`，再配置模型 key；认证
> 配置与 Hermes 自检互相独立，但没有密码不应将 live profile 暴露到公网。

本项目默认是 `fixture + mock + template`，不需要模型凭据，也不会产生模型请求。live
演示 profile 明确开启以下配置：

```text
LLM_MODE=live
RUNTIME_PROFILE=hybrid
HERMES_LITE_MODE=embedded_agent
FULL_INTELLIGENCE_ENABLED=true
```

默认模型是 `deepseek-ai/DeepSeek-V4-Flash`，端点固定为
`https://api.siliconflow.cn/v1/chat/completions`。`embedded_agent` 会加载锁定的
`hermes-agent==0.19.0`，并通过官方 `run_agent.AIAgent` 执行有界 tool-calling loop。全智能
请求只能调用 `nba_query`、`nba_schedule`、`nba_news` 三个服务端工具；通用网络、shell、
文件系统、浏览器、MCP、memory、skills 和子代理全部关闭。当前形态运行在 API 进程内，
不是正式隔离的 Hermes sidecar；`HERMES_LITE_MODE=sidecar` 仍会保持未就绪并安全回退。

## 推荐：Docker Compose secret（面试演示）

在仓库根目录运行配置脚本。它会隐藏输入、原子写入 `secrets/siliconflow_api_key`，并设置
目录 `0700`。由于 Compose 的 file secret 是 bind mount，镜像中的 `nbaagent` 用户使用
固定 gid `10001`；脚本会将文件设为 `root:10001`、`0640`，仅容器应用用户可读：

```bash
./scripts/configure-app-password.sh
./scripts/configure-siliconflow-key.sh
# 已存在的 key 需要替换时：
# ./scripts/configure-siliconflow-key.sh --force
```

公网演示推荐直接启动完整 live profile：

```bash
make deploy-live
# 仅在本机验证 SiliconFlow 配置时可运行（仍启用 bounded hybrid 数据）：
# make docker-up-silicon
```

验证本地配置（不会进行付费模型探活）：

```bash
curl -fsS http://127.0.0.1:8000/readyz
```

配置正确时返回 HTTP 200，且 `dependencies.hermes` 为 `ok`。该检查只验证 secret 文件可读、
端点/模型配置合法、官方包版本和精确工具清单；首次实际的 Agent 请求才会验证 token 是否有效。没有 key、key
为空或格式不合法时，`/readyz` 返回 HTTP 503，模型调用数为 0，聊天会安全回退到本地模板。

单独运行 `make docker-up-silicon` 时事实数据仍是 fixture；`make deploy-live` 会叠加 public
override，使用 `PUBLIC_DATA_MODE=hybrid` 并按北京时间查询真实“今日赛事”。两种模式都应先
核查公开数据源条款；fixture Compose 的 `HIGHLIGHTS_DEMO_DATE=2026-06-12` 仅用于离线复现。

## 本地 Uvicorn 进程（不使用 Docker）

优先使用 secret 文件，避免把 token 放进环境变量：

```bash
./scripts/configure-siliconflow-key.sh
export LLM_MODE=live
export RUNTIME_PROFILE=hybrid
export HERMES_LITE_MODE=embedded_agent
export FULL_INTELLIGENCE_ENABLED=true
export HERMES_LITE_TIMEOUT_MS=40000
export LLM_TIMEOUT_SECONDS=20
export AGENT_REASONING_EFFORT=none
export REQUEST_DEADLINE_MS=45000
export SILICONFLOW_API_KEY_FILE="$PWD/secrets/siliconflow_api_key"
uvicorn apps.api.src.main:app --host 0.0.0.0 --port 8000
```

也可以临时使用 `SILICONFLOW_API_KEY` 环境变量（不要写入命令历史、`.env`、CI 日志或
截图），例如在交互式 shell 中隐藏读取：

```bash
read -r -s -p 'SiliconFlow API key: ' SILICONFLOW_API_KEY; echo
export SILICONFLOW_API_KEY
export LLM_MODE=live RUNTIME_PROFILE=hybrid HERMES_LITE_MODE=embedded_agent
export FULL_INTELLIGENCE_ENABLED=true
uvicorn apps.api.src.main:app --host 0.0.0.0 --port 8000
unset SILICONFLOW_API_KEY
```

如果使用 `.env`，复制模板后只在本机填写，并显式加载（应用不会自动读取 `.env`）：

```bash
cp .env.example .env
# 编辑 .env：至少设置 LLM_MODE/RUNTIME_PROFILE/HERMES_LITE_MODE；不要把真实 key 提交到 Git
set -a; . ./.env; set +a
uvicorn apps.api.src.main:app --host 0.0.0.0 --port 8000
```

对于 Docker，不要将整份 `.env` 通过 `env_file` 注入容器；使用上面的 Compose secret 文件。

## 排障与撤销

| 现象 | 含义与处理 |
| --- | --- |
| `/readyz` 为 503，`hermes=degraded` | 检查三个开关、secret 路径和文件权限；不会发出模型请求。 |
| 容器内读取 secret 报 `Permission denied` | Compose file secret 是 bind mount；执行 `chown root:10001 secrets/siliconflow_api_key && chmod 640 secrets/siliconflow_api_key`，再重新创建容器。不要改成 644。 |
| 容器内 `cat /run/secrets/siliconflow_api_key` 报 `Permission denied` | Compose file secret 是 bind mount；执行 `chown root:10001 secrets/siliconflow_api_key && chmod 640 secrets/siliconflow_api_key`，再重新创建容器。不要把权限改成 644。 |
| 全智能回答仍是模板/回退 | 确认页面开关已启用、请求携带 `intelligence_mode=full`，再检查 key、`embedded_agent`、超时和输出守卫；完成响应的 `composition` 应为 `agent/used`。 |
| 全智能请求长时间停在理解/整理阶段 | 保持 `AGENT_REASONING_EFFORT=none`；系统会同时向固定 SiliconFlow 模型发送关闭隐藏思考的请求参数，并用 `LLM_TIMEOUT_SECONDS` 限制单次模型调用。修改推理档位后需重新做 live 时延回归。 |
| 首次请求返回认证错误 | SiliconFlow token 无效/过期，重新生成并运行配置脚本；不要把 token 粘贴进聊天。 |
| 返回限流/额度错误 | 检查 SiliconFlow 账户余额、模型权限和预算限额；不要通过重试绕过限流。 |

撤销当前 Compose 实例的 key：

```bash
docker compose -f docker-compose.yml -f docker-compose.siliconflow.yml down
rm -f -- secrets/siliconflow_api_key
```

如果 key 曾出现在 shell 历史、CI 日志或公开截图中，应立即在 SiliconFlow 控制台吊销并重发，
不能只删除本地文件。

## 安全边界（面试说明）

- token 只在 API 进程读取，不进入前端、聊天消息、镜像层或 telemetry；`Authorization` 不写
  日志。
- 发给模型的内容仅包括清理后的问题、泛化上下文、NBA 工具 schema 和清洗后的工具观察；
  不含 Provider 原始响应、URL、证据 ID、原始会话 ID 或凭据。
- Agent 不能直接访问 DuckDuckGo；`nba_news` 只会调用 Provider 侧受控搜索，结果作为不可信
  候选清洗并保持部分核验。
- live profile 不应直接暴露在未认证公网端口后；使用认证反代/VPN/安全组，并在供应商侧设置
  预算与限流。
- 默认 fixture/mock 路径始终可离线运行，撤掉 key 后仍能演示客观题、安全拦截和文字 PBP 回放。
