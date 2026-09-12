#!/usr/bin/env python3
"""Build the interview handoff PDF from a small, reproducible source script.

The PDF intentionally contains only design decisions and verification evidence;
secrets, provider credentials and raw upstream payloads are never included.
ReportLab's built-in Chinese CID font keeps the artifact self-contained without
checking a large font binary into the repository.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "solution.pdf"
FONT = "STSong-Light"


def p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def build() -> None:
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="TitleCN",
            parent=styles["Title"],
            fontName=FONT,
            fontSize=24,
            leading=31,
            textColor=colors.HexColor("#0b1f33"),
            alignment=TA_CENTER,
            spaceAfter=8 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SubtitleCN",
            parent=styles["Normal"],
            fontName=FONT,
            fontSize=11,
            leading=17,
            textColor=colors.HexColor("#496278"),
            alignment=TA_CENTER,
            spaceAfter=15 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H1CN",
            parent=styles["Heading1"],
            fontName=FONT,
            fontSize=16,
            leading=22,
            textColor=colors.HexColor("#0b1f33"),
            spaceBefore=5 * mm,
            spaceAfter=3 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H2CN",
            parent=styles["Heading2"],
            fontName=FONT,
            fontSize=12,
            leading=18,
            textColor=colors.HexColor("#126782"),
            spaceBefore=3 * mm,
            spaceAfter=2 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyCN",
            parent=styles["BodyText"],
            fontName=FONT,
            fontSize=9.5,
            leading=15,
            textColor=colors.HexColor("#263b4d"),
            alignment=TA_LEFT,
            spaceAfter=2.5 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SmallCN",
            parent=styles["BodyText"],
            fontName=FONT,
            fontSize=8,
            leading=12,
            textColor=colors.HexColor("#60778a"),
            spaceAfter=1.5 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="CellCN",
            parent=styles["BodyText"],
            fontName=FONT,
            fontSize=8.2,
            leading=12,
            textColor=colors.HexColor("#263b4d"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="CellHeadCN",
            parent=styles["BodyText"],
            fontName=FONT,
            fontSize=8.3,
            leading=12,
            textColor=colors.white,
        )
    )

    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="NBA Chat Agent 方案说明",
        author="NBA Chat Agent",
    )
    story: list[object] = []
    story.extend(
        [
            Spacer(1, 18 * mm),
            p("NBA Chat Agent", styles["TitleCN"]),
            p("方案说明 · 面试交付版", styles["SubtitleCN"]),
            p(
                "目标：交付一个面向中文球迷的、可在线访问的 NBA 官方风格问答助手。"
                "本方案聚焦事实可信、安全合规和可复现的工程边界。",
                styles["BodyCN"],
            ),
            Spacer(1, 7 * mm),
        ]
    )

    def section(title: str) -> None:
        story.append(p(title, styles["H1CN"]))

    section("1. 产品思路")
    story.append(
        p(
            "产品采用“确定性事实链 + 受控 Agent”的双通道。比分、球员数据、赛程、排名、"
            "系列赛累计和逐回合事件，先从公开数据获取并归一化，再由确定性代码核验和推导；"
            "全智能模式把原始问题和同会话的可信上下文直接交给受控 Agent，由它选择 NBA 工具"
            "并完成最终综合。因此模型可以规划查询，但客观事实文本采用服务器观察，不能交换胜者与比分、"
            "把罚球/终场标记改成投篮、做算术或绕过安全策略。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "用户看到的是结论优先、结构化、简体中文的回答，默认北京时间（UTC+8），并明确数据截至时间和核验状态。"
            "默认是漫游模式；用户显式进入赛事下钻后，左侧才加载最近比赛或自定义日期范围，空日期和未来日期不会复用旧卡片。",
            styles["BodyCN"],
        )
    )

    section("2. 系统架构")
    story.append(
        p(
            "浏览器 Web Demo → FastAPI Chat API → 检索前 Safety Guard → 会话/时区上下文 → "
            "确定性通道或受控 Agent → NBA 工具 → Provider/SQLite/BM25/Verifier/"
            "Derivation → Output Guard。",
            styles["BodyCN"],
        )
    )
    rows = [
        [
            p("模块", styles["CellHeadCN"]),
            p("职责", styles["CellHeadCN"]),
            p("边界", styles["CellHeadCN"]),
        ],
        [
            p("Safety Guard", styles["CellCN"]),
            p("识别红线并在检索前拒答", styles["CellCN"]),
            p("不搜索敏感问题", styles["CellCN"]),
        ],
        [
            p("Provider Gateway", styles["CellCN"]),
            p("公开数据、超时、重试、缓存和公开搜索源 failover", styles["CellCN"]),
            p("唯一外网访问边界；不挂载演示快照", styles["CellCN"]),
        ],
        [
            p("SQLite 赛事索引", styles["CellCN"]),
            p("结构化比赛/球员/PBP 精确过滤，叙事文档 FTS5/BM25 召回", styles["CellCN"]),
            p("演示快照不写入公开索引；搜索摘要只保留部分证据", styles["CellCN"]),
        ],
        [
            p("Verifier / Derivation", styles["CellCN"]),
            p("核验事实，确定性汇总系列赛与 PBP", styles["CellCN"]),
            p("模型不参与算术和选球", styles["CellCN"]),
        ],
        [
            p("受控 Agent", styles["CellCN"]),
            p("理解问题并调用 nba_query / nba_schedule / nba_news / nba_search", styles["CellCN"]),
            p("无通用网络、浏览器、Shell、Memory、MCP；纯搜索不经过结构化新闻", styles["CellCN"]),
        ],
        [
            p("Output Guard", styles["CellCN"]),
            p("客观语义 grounding；拦截无证据数字/专名和内部字段", styles["CellCN"]),
            p("模型输出不直接信任", styles["CellCN"]),
        ],
    ]
    table = Table(rows, colWidths=[32 * mm, 78 * mm, 58 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#126782")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c9d7e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f5f9fb")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f5f9fb"), colors.white]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([table, Spacer(1, 3 * mm)])

    section("3. 数据获取与事实准确性")
    story.append(
        p(
            "实时剖面使用公开 ESPN Web API 适配器（HTTPS allow-list、超时、"
            "响应大小上限和有界重试）。live 与 hybrid 都只装配公开数据栈，"
            "不会构造或挂载 fixture fallback；公开源异常时返回真实的无数据、部分核验或"
            "类型化技术错误。fixture 仅用于显式离线演示和自动测试。业务层只依赖 Provider port，"
            "不绑定供应商字段。用户回答不暴露供应商名称、端点或内部字段。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "所有缺失值保持 null 并显示“暂无数据/部分核验”。系列赛大比分、连胜和"
            "最后 5 秒事件由真实记录确定性计算；"
            "时间先统一为 UTC，再按请求时区展示。赛季采用跨自然年标签，例如 2025-26。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "赛事下钻和聊天索引使用 SQLite 保存已经过公开模型校验的比赛、球员、PBP 和叙事文档。"
            "结构化字段采用关系过滤，文本证据采用实体/赛季约束后的 FTS5/BM25；"
            "纯网页搜索先读本地 BM25，"
            "缺失时才调用受控搜索适配器，避免额外消耗结构化新闻请求。"
            "搜索摘要始终是部分证据，不会升级为比分或统计事实。"
            "历史终场数据可先返回缓存再后台刷新；最近赛事先查完整本地索引窗口，远端扫描预算"
            "耗尽产生的 partial 空结果不会写入 recent 缓存；今日和进行中比赛只接受短时 fresh 数据。"
            "详情完整度只能前进，低字段或冲突比分不会覆盖已有版本。具名卷保证容器替换后"
            "仍可复用，缓存异常则失败开放到原公开数据链路。公开 profile 只投影公开赛事卡片，"
            "显式 fixture profile 只投影带演示标签的固定快照，两者不会在同一公开 fallback "
            "链中混用。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "搜索或模型额度耗尽时，公共响应使用供应商无关的能力提醒：若 SQLite 或已核验事实"
            "仍能回答，就保留答案并说明最新资料或开放性分析受限；若完全没有可用事实，则返回"
            "不可重试的技术失败，不伪装为‘暂无数据’。本轮额度状态在共享缓存读写前剥离，"
            "不会污染后续请求。",
            styles["BodyCN"],
        )
    )

    section("4. 交互与 UI")
    story.append(
        p(
            "前端是零构建 HTML/CSS/ES2022，实现赛事转播风格的三栏布局：赛事焦点、"
            "对话区、比赛 HUD/PBP。支持 POST-SSE 流式输出、加载阶段、停止生成、"
            "错误重试、响应式布局、日期可用性三态和文字回放。"
            "历史请求在 250ms 内完成时不闪加载，慢请求保留原卡片并只提示一次。"
            "永久额度耗尽会显示醒目的能力提醒且不提供无效重试按钮；临时限流或超时仍可重试。"
            "没有获得授权的直播视频源，因此首版只展示文字 PBP，不嵌入第三方视频。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "对外演示增加共享密码：密码只从 Docker secret 读取，成功登录后发放短期 "
            "HttpOnly、SameSite Cookie；"
            "健康/就绪探针保持公开，聊天、赛事焦点和日期接口未认证时返回 401。",
            styles["BodyCN"],
        )
    )

    section("5. 模型与智能 Agent 取舍")
    story.append(
        p(
            "模型并不替代事实系统。显式 fixture profile 完全离线；公开 live/hybrid profile "
            "可使用锁定的受控智能 Agent。全智能请求保留用户原始问题，并在确定性模板之前直接进入"
            "有界 tool loop；Agent 获取结构化范围和同会话上下文，自主选择工具并完成回答，"
            "输出必须通过本地 Output Guard。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "明确日期、对阵或 G# 的比赛过程问题先绑定结构化比赛查询，不用日期赛程观察代替复盘；"
            "结构化记录缺字段时，再以有界公开报道补充并明确部分核验。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "Agent 只启用四个任务级 NBA 工具；Shell、文件系统、浏览器、任意 URL、通用网络、"
            "MCP、Memory、Skills 和子代理均关闭。运行时实现和内部品牌属于服务端部署细节，"
            "系统提示词、公开 UI、回答与流式事件只使用产品身份，不暴露这些内部信息。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "用户明确要求联网复核演示比赛时，系统跳过旧缓存，先按北京时间"
            "日期和主客对阵匹配唯一公开赛事 ID，再读取公开详情；无唯一匹配只说明不能升级核验，"
            "不会把演示数据伪装成实时结果。公开数据栈从一开始就不包含快照 fallback。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "同一网页聊天在用户点击“新对话”前保持一个稳定逻辑会话，并显式传入最近 4 个完整回合；"
            "底层原生 Memory 和会话数据库仍关闭。历史只用于理解“那场/最后那个球”等指代，"
            "每个事实追问都必须在当前轮重新调用 NBA 工具核验。Agent 推荐的场次只有解析到当前"
            "系列赛已核验候选时才会绑定；后续“刚推荐的那场”等追问会重新查询该 canonical game。"
            "新对话会同时隔离历史、选中比赛和推荐场次。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "确定性恢复不是全智能主链，只在 Agent 超时、缺少可用观察、事实关系与可信记录冲突，"
            "或输出/安全守卫拒绝结果时触发。此时应用只从已完成的可信结构化记录和网页证据重组"
            "完整回答；真正查无记录才返回无数据，能力故障且没有任何可用事实时返回对应技术"
            "失败，不套用固定快照。",
            styles["BodyCN"],
        )
    )

    section("6. 验证、评测与交付")
    story.append(
        p(
            "黄金题集包含 63 轮案例，覆盖 A–I 参考题型和全智能验收题；评测 Runner "
            "支持重复运行、七维评分、性能记录和安全一票否决。"
            "本地 847 项 pytest、29 项浏览器 E2E 及关系级对抗用例覆盖模型、时间、Provider、"
            "HTTP/SSE、认证、运行时、失败路径和多轮上下文。",
            styles["BodyCN"],
        )
    )
    rows = [
        [
            p("交付项", styles["CellHeadCN"]),
            p("状态", styles["CellHeadCN"]),
            p("入口", styles["CellHeadCN"]),
        ],
        [
            p("在线产品", styles["CellCN"]),
            p("已部署，单端口访问", styles["CellCN"]),
            p("http://115.190.174.39:8000/（需访问密码）", styles["CellCN"]),
        ],
        [
            p("需求/HLD/LLD", styles["CellCN"]),
            p("已提交", styles["CellCN"]),
            p("随交付物提供完整设计说明", styles["CellCN"]),
        ],
        [
            p("方案说明 PDF", styles["CellCN"]),
            p("本文件", styles["CellCN"]),
            p("独立可阅读，不包含仓库、代码托管或凭据资料", styles["CellCN"]),
        ],
        [
            p("运行配置", styles["CellCN"]),
            p("公开 live/hybrid；显式 fixture 演示/测试", styles["CellCN"]),
            p("公开模式永不回退固定快照；Secret 仅服务端注入", styles["CellCN"]),
        ],
    ]
    table = Table(rows, colWidths=[35 * mm, 42 * mm, 91 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#126782")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c9d7e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f5f9fb"), colors.white]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 5 * mm))
    story.append(
        p(
            f"生成日期：{date.today().isoformat()} · 版本：v0.3",
            styles["SmallCN"],
        )
    )
    doc.build(story)
    print(OUTPUT)


if __name__ == "__main__":
    build()
