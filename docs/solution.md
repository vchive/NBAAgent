# NBA Chat Agent 方案说明（评审稿）

**对应需求**：[spec.md](../specs/001-nba-chat-agent/spec.md)
**详细设计**：[HLD](../specs/001-nba-chat-agent/hld.md) · [LLD](../specs/001-nba-chat-agent/lld.md)
**状态**：需求与方案设计阶段；UI 交互原型已提供（业务服务尚未实现）

可运行的赛事转播风格 UI 原型位于 [`apps/web-demo`](../apps/web-demo/)，用于先确认聊天、
状态反馈和 PBP 事件回放的交互，不代表真实 Provider 或在线数据已经接入。

## 1. 目标与边界

本项目交付一个可在线访问的简体中文 NBA Web Chat。用户可以查询球队、球员、赛程、
赛果、统计、历史纪录和新闻背景，也可以围绕同一场比赛进行多轮追问。答案默认采用
北京时间（UTC+8），以 NBA 官方风格呈现：先给结论，再给结构化事实和必要的分析。

PDF 中的要求分为三类：联网公开取数、事实核验、时区/赛季、多轮、安全拦截和交付物是
硬性要求；流式输出、卡片、表格和顺畅的加载/错误反馈是体验加分项；A–I 是用于准备
黄金题集的参考题型，不把题型数量误当成 PDF 的固定规模要求。博彩、隐私绯闻、法律
犯罪、无证据假球和仇恨/侮辱等内容不在回答范围内；敏感请求必须在检索前短路。

## 2. 总体架构

```mermaid
flowchart LR
  U[用户] --> UI[Web Chat]
  UI --> API[版本化 Chat API]
  API --> SG[Safety Guard]
  SG --> CTX[会话/时区上下文]
  CTX --> PARSE[意图/实体/赛季解析]
  PARSE --> ADM[准入/截止时间]
  ADM --> PLAN[查询规划]
  PLAN --> PG[Provider Gateway]
  PG --> NORM[归一化]
  NORM --> VERIFY[事实核验]
  VERIFY --> DERIVE[确定性聚合/PBP]
  DERIVE --> SELECT[模板或 Hermes-lite]
  SELECT --> COMPOSE[回答编排与输出守卫]
  COMPOSE --> API
  API --> UI
```

Provider Gateway 是唯一的公开互联网访问边界。首版以 ESPN Web API 适配器为实现起点，
但业务层只依赖可替换的 Provider port，并保留 fallback、缓存、重试和离线 fixture。供应
商名称、端点、原始字段、提示词和内部证据 URL 只保存在内部契约/脱敏日志中，不进入用户
回答。

## 3. 一次请求如何被处理

1. API 校验消息长度、时区和幂等键，并生成 `request_id`/`session_id`。
2. Safety Guard 使用本地规则/分类器先判定红线。BLOCK 或 `OUT_OF_SCOPE` 直接返回礼貌
   拒答/篮球引导，Provider 和其缓存读取均为 **0**。
3. 允许请求加载当前会话上下文，解析实体、指标、日期、节次、时间窗和跨年赛季；条件
   不足时返回澄清问题，不猜测比赛。
4. 查询规划器调用 typed Provider port。适配器处理超时、限流、格式异常和 fallback，
   Normalizer 将结果映射到统一领域模型并保留缺失值。
5. Verifier 检查证据可信度、新鲜度、实体/时间一致性和用户前提。系列赛累计、连胜和
   最后 5 秒等结果由确定性 Derivation 从真实比赛/PBP 记录计算，模型不负责算术或选球。
6. 客观题优先由确定性模板渲染；战术/复盘等分析题在事实核验完成后才可选用 Hermes-lite
   进行措辞组织。Hermes 不拥有 Provider、缓存、安全判定、算术或 PBP 选择，且关闭
   shell、MCP、浏览器、memory、skills 和子代理。其输入只含结构化 FactBundle、规范化问题
   和风格策略；Output Guard 再检查无证据数字、敏感内容和内部字段泄露。Hermes 不可用时
   客观题回退模板，分析题只返回已核实事实摘要。

同步 HTTP 和 POST SSE 共用同一个用例；SSE 只在核验完成后发送事实增量，完成事件与同步
响应使用同一最终 envelope。断线会取消下游任务；重复 `client_message_id` 在同一会话
内幂等返回已保存结果。

## 4. 关键事实与时间规则

- 内部时间统一为带时区的 UTC Instant，用户默认看到 `Asia/Shanghai`。
- 赛季使用 `YYYY-YY`，Provider 的结束年份映射由 `SeasonClock` 统一处理；“本赛季/最近/
  卫冕冠军”结合可注入时钟和官方 active season 重新核验。
- PBP 时间窗先从完整 bundle 确定目标节次，再按节次剩余秒数做闭区间筛选；“全场最后 5 秒”
  只取最终节（含加时），明确某节时只取该节窗口。`sequence_valid=true` 时按节次、有效
  sequence 和 Provider 索引排序；序号不可信时降级到节次/剩余时钟/Provider 索引，并标记
  `sequence_valid=false`。缺失得分值保持为空，不用 0 补齐。
- 每个用户可见数字都能回溯到内部 `FactAssertion → Evidence`；冲突或缺失只显示“部分
  核验/暂无数据”，不以 0、旧缓存或模型记忆补齐。

## 5. 安全、隐私与可观测性

Safety Guard 在任何外部检索前运行；一条消息同时包含正常篮球问题和红线内容时整体拒答。
拒答不复述敏感词、不提供规避建议，只用 1–2 句引导回比赛、球员或球队数据。日志只保留
脱敏哈希、题型、状态、时延、证据状态、Provider 调用计数和缓存读写计数，不记录凭据、
原始敏感文本或不必要的个人信息；安全短路的 Provider/cache 计数必须均为 0。健康检查不
暴露上游 URL 和依赖细节。

## 6. 评测与交付

版本化黄金集覆盖 A–I，并包含至少 10 条客观题；H 类使用同一会话的三轮 `turns` 验证
上下文一致性，I 类和 `OUT_OF_SCOPE` 类验证检索前 `provider_call_count=0` 且缓存读写
计数为 0。每题按 PDF 的七个维度记录档位、可配置数值、TTFT、完整时延、证据状态和安全
否决；题意理解、事实准确或安全不合格时该题为 0 分。

本阶段提交需求规格、研究记录、HLD、LLD、统一数据模型、HTTP/SSE/Provider/评测契约和
本地验收指南。实现阶段再生成任务清单、代码、fixture、公开部署链接，并将本说明导出为
PDF 作为最终提交物。

## 7. 设计取舍与未决项

PDF 没有规定语言、模型、数据供应商、数据库、部署平台、并发量或具体时延阈值；Python
API + React Web、ESPN-first、单 API 副本和 90% 查询 5 秒内的目标均是可替换的工程决策，
不是题目硬性承诺。上线前必须重新核查公开数据源的条款、robots、访问频率和稳定性。
