# 服务访问密码

公网演示使用一个共享访问密码。密码由 Docker Compose secret 注入，服务只在内存中保存
短期、不可逆的会话令牌；浏览器拿到的是 `HttpOnly` Cookie，不会把密码写入
`localStorage`、URL、页面源码或日志。

## 配置与启动

```bash
make configure-app-password
make deploy
```

脚本会隐藏输入并要求确认，密码至少 8 位，最终写入 `secrets/app_password`（该目录已被
`.gitignore` 忽略）。如果同时启用 SiliconFlow：

```bash
make configure-app-password
make configure-siliconflow-key
make docker-up-silicon
```

`make docker-up` 仍用于本地 fixture 开发，不挂载密码；对外发布必须使用 `make deploy`
或显式加入 `docker-compose.auth.yml`：

```bash
docker compose -f docker-compose.yml -f docker-compose.auth.yml up -d --build
```

## API 行为

- `GET /api/v1/auth/status`：返回是否启用认证以及当前浏览器会话状态。
- `POST /api/v1/auth/login`：JSON body 为 `{"password":"..."}`，成功设置
  `HttpOnly; SameSite=Lax` Cookie。
- `POST /api/v1/auth/logout`：撤销当前会话并清除 Cookie。
- `/api/v1/chat`、`/api/v1/chat/stream`、`/api/v1/highlights` 和日期可用性接口需要登录。
- `/healthz`、`/readyz`、`/livez` 与静态页面保持可探活/可加载；`/readyz` 会把缺失密码标记为
  `auth=degraded`，不会因误删 secret 而匿名放行。

登录失败按客户端 IP 做进程内限流。该方案是面试演示级单用户认证；正式环境仍应在 HTTPS
反向代理后使用独立账户、持久化会话和更严格的审计策略。
