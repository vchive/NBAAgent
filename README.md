# NBA Chat Agent

面向中文球迷的 NBA Chat Agent 笔试题项目，按 GitHub SpecKit 的 SDD（Specification-
Driven Development）流程推进。

## 当前阶段：需求与方案设计

已完成需求规格、研究记录、HLD、LLD、数据模型、接口契约和验收指南；业务服务代码和
部署链接将在下一阶段按任务清单实现，当前可先用 UI Demo 验证交互方案。

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

方案评审通过后运行 `$speckit-tasks` 生成依赖排序任务，再运行 `$speckit-implement` 开始
实现。上线前必须完成 fixture 模式验收、公开 URL 探活、A–I 黄金题回放和方案 PDF 导出。
