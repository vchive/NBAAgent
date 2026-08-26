# NBA Chat Agent — Quickstart and Validation Guide

**Feature**: [spec.md](spec.md)
**Design**: [hld.md](hld.md), [lld.md](lld.md)

本指南用于开发者和面试评审在干净环境中启动、验收和导出交付物。以下目录和命令是
Phase 2 实现的目标结构；在业务代码尚未生成前不能直接执行，任务完成后必须保持本文件
可执行。实现阶段如包管理器有调整，必须同步更新本文件。

## 1. Prerequisites

- Linux/macOS/WSL，Python 3.12，Node.js 20+，Docker（可选）。
- Git 能访问仓库；联网模式需要公开数据源可访问。
- 不需要提交或共享任何 API key。模型和数据源凭据（如将来需要）通过本地 `.env` 注入。

## 2. Local fixture mode (no external network)

```bash
git clone git@github.com:vchive/NBAAgent.git
cd NBAAgent
cp .env.example .env                 # 若文件尚未生成，按配置契约手工创建
export PUBLIC_DATA_MODE=fixture
export LLM_MODE=mock
export RUNTIME_PROFILE=template
export HERMES_LITE_MODE=off       # embedded_spike 仅用于本地 fixture 验证

# API
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn apps.api.src.main:app --reload --port 8000

# Web（另开终端）
npm ci --prefix apps/web
npm run dev --prefix apps/web
```

浏览器访问 `http://localhost:3000`。fixture 模式必须无需外网即可运行一条 A–I 题型样例、
错误场景和三轮 H 场景。

## 3. API smoke checks

```bash
curl -fsS http://localhost:8000/healthz

curl -fsS -X POST http://localhost:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"2025-26 总决赛 G4 谁得分最高？","client_timezone":"Asia/Shanghai"}'

curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H 'Accept: text/event-stream' -H 'Content-Type: application/json' \
  -d '{"message":"那场最后5秒发生了什么？","session_id":"<上一请求返回的 UUID>"}'
```

SSE 客户端使用 `fetch()` + `ReadableStream` 读取 POST 响应；不要使用只支持 GET 的原生
`EventSource`。检查事件顺序为 `run.started → run.status* → message.delta* →
message.completed`，且核验前不出现未经核实的数字。

## 4. Required acceptance scenarios

| Check | Action | Expected |
|---|---|---|
| Core facts | 提问球队/球员/赛程/赛果/数据 | 返回结构化答案与北京时间截至口径 |
| Time | 在固定时钟下问“本赛季/最近/今年” | 使用跨年赛季标签，跨时区日期正确 |
| Correction | 提供错误比分或得分前提 | 独立核验并礼貌纠正，不把“没查到”当作错误 |
| PBP | 问最后 5 秒出手/助攻/比分 | 基于逐回合 fixture，类型和顺序正确 |
| Analysis | 问战术或主观比较 | 先结论，再 2–4 条事实理由；区分推断 |
| Follow-up | 同场连续三轮“那场/最后那个球” | 上下文正确且三轮事实一致；新 session 不串线 |
| Safety | 逐类提交红线问题 | 1–2 句礼貌拒答；provider call/cache read-write count 均为 0 |
| Failure | 模拟 timeout/429/空/部分 JSON | 明确可重试或暂无数据，不输出旧/虚构数字 |
| UI | 断网、断流、窄屏、键盘操作 | 加载/错误/重试清晰，布局可用 |

## 5. Automated verification

目标测试命令：

```bash
pytest -q tests/unit tests/contract tests/integration
pytest -q tests/evaluation --mode fixture --repeat 3
npx playwright test tests/e2e
```

评测运行器必须输出每题七维得分、否决标记、TTFT、完整时延、证据状态和重复运行差异；
具体 JSONL 与权重见 [contracts/evaluation.md](contracts/evaluation.md)。

## 6. Live mode and deployment smoke test

```bash
export PUBLIC_DATA_MODE=live
export LLM_MODE=mock                 # 无模型凭据时仍可回答事实题
export RUNTIME_PROFILE=hybrid
export HERMES_LITE_MODE=sidecar      # 生产/在线剖面不得使用 embedded_spike
uvicorn apps.api.src.main:app --host 0.0.0.0 --port 8000
```

上线前：

1. 使用 `GET /healthz` 和一条非敏感基准题探活。
2. 重复访问部署 URL，确认无需登录、HTTPS 有效、Web/API CORS 正确。
3. 运行一条安全题并在内部指标确认 `provider_call_count=0` 且
   `cache_read_count=cache_write_count=0`。
4. 保存评测报告和简要方案说明 PDF；PDF 可写技术栈、数据获取方式和亮点，但用户回答
   仍不得泄露内部 provider/API/字段。

部署 URL 由发布环境注入，不写死在代码或测试 fixture；交付时在项目 release note 中记录
最终公开链接和探活时间。
