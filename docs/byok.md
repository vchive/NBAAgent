# SiliconFlow BYOK 配置指南

> 对外演示同时需要访问密码。先运行 `make configure-app-password`，再配置模型 key；认证
> 配置与 Hermes 自检互相独立，但没有密码不应将 live profile 暴露到公网。

本项目默认是 `fixture + mock + template`，不需要模型凭据，也不会产生模型请求。只有在
明确开启下面三个开关后，战术（F）和复盘（G）问题才会把已经核验的事实摘要交给
SiliconFlow：

```text
LLM_MODE=live
RUNTIME_PROFILE=hybrid       # 也可以是 hermes
HERMES_LITE_MODE=embedded_spike
```

默认模型是 `deepseek-ai/DeepSeek-V4-Flash`，端点固定为
`https://api.siliconflow.cn/v1/chat/completions`。当前 `HermesRuntimeAdapter` 的
`embedded_spike` 是 API 进程内的受限适配器（无工具、无文件系统、无 Provider 访问），不是
正式隔离的 Hermes sidecar；`HERMES_LITE_MODE=sidecar` 仍会保持未就绪并回退模板。

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

随后启动 opt-in profile：

```bash
make docker-up-silicon
# 后台启动可用：
# docker compose -f docker-compose.yml -f docker-compose.auth.yml -f docker-compose.siliconflow.yml up -d --build
```

验证本地配置（不会进行付费模型探活）：

```bash
curl -fsS http://127.0.0.1:8000/readyz
```

配置正确时返回 HTTP 200，且 `dependencies.hermes` 为 `ok`。该检查只验证 secret 文件可读、
端点/模型配置合法和能力边界；首次实际的 F/G 请求才会验证 token 是否有效。没有 key、key
为空或格式不合法时，`/readyz` 返回 HTTP 503，模型调用数为 0，聊天会安全回退到本地模板。

Compose override 的事实数据仍是 `PUBLIC_DATA_MODE=fixture`，所以演示结果可复现；若确实要
联网取公开 ESPN 数据，另行设置 `PUBLIC_DATA_MODE=live` 或 `hybrid`，并先核查数据源条款。
fixture Compose 默认设置 `HIGHLIGHTS_DEMO_DATE=2026-06-12`，因此部署当天没有比赛时首页仍
会展示可复现的三场演示赛事；live/hybrid 模式忽略该设置并按北京时间查询真实“今日赛事”。

## 本地 Uvicorn 进程（不使用 Docker）

优先使用 secret 文件，避免把 token 放进环境变量：

```bash
./scripts/configure-siliconflow-key.sh
export LLM_MODE=live
export RUNTIME_PROFILE=hybrid
export HERMES_LITE_MODE=embedded_spike
export HERMES_LITE_TIMEOUT_MS=20000
export LLM_TIMEOUT_SECONDS=20
export REQUEST_DEADLINE_MS=25000
export SILICONFLOW_API_KEY_FILE="$PWD/secrets/siliconflow_api_key"
uvicorn apps.api.src.main:app --host 0.0.0.0 --port 8000
```

也可以临时使用 `SILICONFLOW_API_KEY` 环境变量（不要写入命令历史、`.env`、CI 日志或
截图），例如在交互式 shell 中隐藏读取：

```bash
read -r -s -p 'SiliconFlow API key: ' SILICONFLOW_API_KEY; echo
export SILICONFLOW_API_KEY
export LLM_MODE=live RUNTIME_PROFILE=hybrid HERMES_LITE_MODE=embedded_spike
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
| F/G 回答仍是模板 | 可能是 key 缺失、sidecar 占位模式、超时或模型输出未通过安全守卫；先看 `/healthz`，再检查服务端脱敏日志。 |
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
- 发给模型的内容仅是清理后的问题、风格策略和已核验事实投影，不含 Provider 原始响应、
  URL、证据 ID、会话 ID 或工具调用。
- live profile 不应直接暴露在未认证公网端口后；使用认证反代/VPN/安全组，并在供应商侧设置
  预算与限流。
- 默认 fixture/mock 路径始终可离线运行，撤掉 key 后仍能演示客观题、安全拦截和文字 PBP 回放。
