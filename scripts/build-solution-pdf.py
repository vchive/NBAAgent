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
        author="vchive",
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
            "产品采用“确定性事实链 + 受控 Agent”的双通道。比分、球员数据、赛程、排名、系列赛累计和逐回合事件，"
            "先从公开数据获取并归一化，再由确定性代码核验和推导；全智能模式由 Hermes 理解问题并选择三个 NBA 工具。"
            "因此模型可以规划查询，但不能修改比分、做算术或绕过安全策略。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "用户看到的是结论优先、结构化、简体中文的回答，默认北京时间（UTC+8），并明确数据截至时间和核验状态。"
            "左侧赛事焦点支持“今日赛事 / 精彩回顾”，空日期和未来日期不会复用旧卡片。",
            styles["BodyCN"],
        )
    )

    section("2. 系统架构")
    story.append(
        p(
            "浏览器 Web Demo → FastAPI Chat API → 检索前 Safety Guard → 会话/时区上下文 → hybrid 确定性通道或"
            " Official Hermes Agent → NBA 工具 → Provider/Verifier/Derivation → Output Guard。",
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
            p("公开数据、超时、重试、缓存和 fallback", styles["CellCN"]),
            p("唯一外网访问边界", styles["CellCN"]),
        ],
        [
            p("Verifier / Derivation", styles["CellCN"]),
            p("核验事实，确定性汇总系列赛与 PBP", styles["CellCN"]),
            p("模型不参与算术和选球", styles["CellCN"]),
        ],
        [
            p("Official Hermes Agent", styles["CellCN"]),
            p("理解问题并调用 nba_query / nba_schedule / nba_news", styles["CellCN"]),
            p("无通用网络、浏览器、Shell、Memory、MCP", styles["CellCN"]),
        ],
        [
            p("Output Guard", styles["CellCN"]),
            p("拦截无证据数字、敏感内容和内部字段", styles["CellCN"]),
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
            "实时剖面使用公开 ESPN Web API 适配器（HTTPS allow-list、超时、响应大小上限和有界重试）；"
            "hybrid 模式在上游异常时回退到版本化 fixture，避免演示不可用。业务层只依赖 Provider port，"
            "不绑定供应商字段。用户回答不暴露供应商名称、端点或内部字段。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "所有缺失值保持 null 并显示“暂无数据/部分核验”。系列赛大比分、连胜和最后 5 秒事件由真实记录确定性计算；"
            "时间先统一为 UTC，再按请求时区展示。赛季采用跨自然年标签，例如 2025-26。",
            styles["BodyCN"],
        )
    )

    section("4. 交互与 UI")
    story.append(
        p(
            "前端是零构建 HTML/CSS/ES2022，实现赛事转播风格的三栏布局：赛事焦点、对话区、比赛 HUD/PBP。"
            "支持 POST-SSE 流式输出、加载阶段、停止生成、错误重试、响应式布局、日期可用性三态和文字回放。"
            "没有获得授权的直播视频源，因此首版只展示文字 PBP，不嵌入第三方视频。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "对外演示增加共享密码：密码只从 Docker secret 读取，成功登录后发放短期 HttpOnly、SameSite Cookie；"
            "健康/就绪探针保持公开，聊天、赛事焦点和日期接口未认证时返回 401。",
            styles["BodyCN"],
        )
    )

    section("5. 模型与 Hermes 取舍")
    story.append(
        p(
            "模型并不替代事实系统。默认 fixture/mock 模式完全离线；live + hybrid + embedded_agent 使用锁定的"
            " hermes-agent==0.19.0 和官方 AIAgent，默认模型为 deepseek-ai/DeepSeek-V4-Flash。"
            "全智能请求在规则 Parser 之前进入有界 tool loop，输出必须通过本地 Output Guard。",
            styles["BodyCN"],
        )
    )
    story.append(
        p(
            "Agent 只启用三个任务级 NBA 工具，Shell、文件系统、浏览器、通用搜索、MCP、Memory、Skills 和子代理均关闭。"
            "当前 embedded_agent 是 API 进程内的受控面试演示形态；正式生产可将 AgentOrchestratorPort 迁移到隔离 sidecar。",
            styles["BodyCN"],
        )
    )

    section("6. 验证、评测与交付")
    story.append(
        p(
            "黄金题集包含 21 条案例，覆盖 A–I 参考题型和全智能验收题；评测 Runner 支持重复运行、七维评分、性能记录和安全一票否决。"
            "本地 pytest 覆盖模型、时间、Provider、HTTP/SSE、认证、运行时、失败路径和多轮上下文。",
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
            p("specs/001-nba-chat-agent/", styles["CellCN"]),
        ],
        [
            p("方案说明 PDF", styles["CellCN"]),
            p("本文件", styles["CellCN"]),
            p("docs/solution.pdf", styles["CellCN"]),
        ],
        [
            p("运行配置", styles["CellCN"]),
            p("fixture / hybrid / live 可切换", styles["CellCN"]),
            p("README.md、docs/byok.md", styles["CellCN"]),
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
            f"生成日期：{date.today().isoformat()} · 代码提交人：vchive · 版本：v0.1",
            styles["SmallCN"],
        )
    )
    doc.build(story)
    print(OUTPUT)


if __name__ == "__main__":
    build()
