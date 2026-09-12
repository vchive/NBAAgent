"""Regression tests for Hermes recap routing and answer grounding.

These tests intentionally exercise the application seams directly.  The
server-owned observations remain evidence, while a complete Hermes synthesis
is allowed to provide the user-facing explanation for open-ended questions.
"""

import re

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.application.parser import IntentParser, is_game_recap_question
from apps.api.src.application.ports import RuntimeStatus
from apps.api.src.domain.models import IntentName
from apps.api.src.infrastructure.agent_tools import AgentToolCall
from apps.api.src.infrastructure.hermes_agent_runtime import AgentTurnResult


def test_win_explanation_phrases_are_open_recap_questions() -> None:
    questions = (
        "最近的一场比赛尼克斯赢了吗，怎么赢的",
        "这场比赛是如何取胜的？",
        "凯尔特人这场的胜因是什么？",
        "这场赢球靠什么？",
    )

    for question in questions:
        assert is_game_recap_question(question), question
        assert ChatUseCase._is_open_ended_agent_synthesis(question), question


def test_objective_winner_question_is_not_recap() -> None:
    question = "这场比赛谁赢了？"

    assert not is_game_recap_question(question)
    assert not ChatUseCase._is_open_ended_agent_synthesis(question)
    assert IntentParser().parse(question).intent.intent_name is IntentName.SCHEDULE_RESULT


def test_bare_matchup_prefers_agent_synthesis_but_explicit_metric_does_not() -> None:
    assert ChatUseCase._is_open_ended_agent_synthesis("2026尼克斯-马刺")
    assert ChatUseCase._is_bare_matchup_query("2026尼克斯-马刺")
    assert ChatUseCase._is_bare_matchup_query("2025-26赛季 Knicks vs Spurs")
    assert not ChatUseCase._is_open_ended_agent_synthesis(
        "2026尼克斯-马刺系列赛大比分"
    )
    assert not ChatUseCase._is_bare_matchup_query("2026尼克斯-马刺系列赛大比分")
    assert not ChatUseCase._is_open_ended_agent_synthesis("2026尼克斯-马刺谁赢了？")


@pytest.mark.parametrize(
    "question",
    [
        "你觉得最精华的是哪一场？",
        "哪一场最好看，为什么？",
        "这轮最值得回看的是哪场？",
        "乔丹和詹姆斯谁更伟大？请说出判断依据",
        "文班亚马和邓肯怎么比较？",
    ],
)
def test_subjective_recommendation_and_comparison_stay_on_agent_synthesis(
    question: str,
) -> None:
    assert ChatUseCase._is_open_ended_agent_synthesis(question)


def test_clear_subjective_question_rejects_generic_clarification_as_agent_success() -> None:
    result = AgentTurnResult(
        status=RuntimeStatus.OK,
        answer_markdown="请补充查询对象，我再帮您核对。",
        observations=[
            {
                "status": "needs_clarification",
                "intent": "nba_query",
                "answer_markdown": "请补充查询对象，我再帮您核对。",
                "evidence_state": "none",
            }
        ],
        tool_calls=[AgentToolCall("nba_query", "query", "needs_clarification", 1)],
    )

    assert not ChatUseCase._agent_result_relevant(
        "你觉得最精华的是哪一场？", result
    )


def test_event_hard_fact_rejects_typed_miss_plus_unscoped_web_result() -> None:
    """A typed miss does not authorize a search snippet as play-by-play fact."""

    result = AgentTurnResult(
        status=RuntimeStatus.OK,
        answer_markdown="最后一投由另一场比赛的球员在篮下完成。",
        observations=[
            {
                "status": "needs_clarification",
                "intent": "nba_query",
                "query_scope": None,
                "answer_markdown": "请补充具体比赛。",
                "evidence_state": "none",
            },
            {
                "status": "completed",
                "intent": "web_search",
                "query_scope": None,
                "answer_markdown": "公开报道提到另一场比赛的最后一球。",
                "evidence_state": "partial",
            },
        ],
        tool_calls=[
            AgentToolCall("nba_query", "query", "needs_clarification", 1),
            AgentToolCall("nba_search", "search", "completed", 1),
        ],
    )

    assert not ChatUseCase._agent_result_relevant("最后谁投篮的，在什么位置？", result)


def test_event_hard_fact_rejects_typed_observation_for_another_game() -> None:
    result = AgentTurnResult(
        status=RuntimeStatus.OK,
        answer_markdown="G4 最后一投发生在篮下。",
        observations=[
            {
                "status": "completed",
                "intent": "nba_query",
                "query_scope": None,
                "_resolved_game_id": "hupu:168858",
                "answer_markdown": "第4节还剩2秒，球员在篮下出手。",
                "evidence_state": "partial",
            }
        ],
        tool_calls=[AgentToolCall("nba_query", "query", "completed", 1)],
    )

    assert not ChatUseCase._agent_result_relevant(
        "这场比赛最后谁投篮？",
        result,
        expected_game_id="hupu:168859",
    )


def test_bare_matchup_observation_is_compact_and_user_ready() -> None:
    blocks = [
        {
            "type": "text",
            "content": "系列赛大比分：**尼克斯 4–1 马刺**。",
        },
        {
            "type": "table",
            "columns": ["北京时间", "客队", "比分", "主队", "状态"],
            "rows": [
                ["2026-06-14 08:30", "尼克斯", "94–90", "马刺", "已结束"],
                ["2026-06-11 08:30", "马刺", "106–107", "尼克斯", "已结束"],
                ["2026-06-09 08:30", "马刺", "115–111", "尼克斯", "已结束"],
                ["2026-06-06 08:30", "尼克斯", "105–104", "马刺", "已结束"],
                ["2026-06-04 08:30", "尼克斯", "105–95", "马刺", "已结束"],
                ["2026-03-02 02:00", "马刺", "89–114", "尼克斯", "已结束"],
                ["2026-01-01 08:00", "尼克斯", "132–134", "马刺", "已结束"],
            ],
        },
        {"type": "fact", "label": "已计入场次", "value": 5, "unit": "场"},
    ]

    answer = ChatUseCase._compact_bare_matchup_observation(
        "系列赛大比分：**尼克斯 4–1 马刺**。", blocks
    )

    assert answer is not None
    assert answer.startswith("这组系列赛的结论很清楚：**尼克斯 4–1 马刺**")
    assert "尼克斯拿下 G1、G2、G4、G5" in answer
    assert "马刺赢了 G3" in answer
    assert "收官战 G5" in answer
    assert "客场以 **94–90** 取胜" in answer
    assert "另外 2 场交手中，两队各胜 1 场" in answer
    assert "|" not in answer
    assert "结构化" not in answer


def test_complete_hermes_win_explanation_survives_structured_miss() -> None:
    """Keep the Agent result while pruning its unsupported causal clause."""

    question = "最近的一场比赛尼克斯赢了吗，怎么赢的"
    hermes_answer = "最近一场尼克斯 94–90 赢了马刺，末节靠防守和布伦森的关键得分守住优势。"
    observations = [
        {
            "status": "no_data",
            "intent": "nba_query",
            "answer_markdown": "暂未找到这场比赛的结构化记录。",
            "evidence_state": "none",
        },
        {
            "status": "completed",
            "intent": "web_search",
            "answer_markdown": (
                "公开资料线索（待交叉核验）：\n"
                "- **比赛报道**：尼克斯在收官阶段守住领先。"
            ),
            "evidence_state": "partial",
        },
    ]

    answer = ChatUseCase._ground_agent_answer(question, hermes_answer, observations)

    assert answer == "最近一场尼克斯 94–90 赢了马刺。"
    assert "暂未找到" not in answer
    assert "防守" not in answer


def test_short_complete_hermes_recap_is_not_treated_as_truncated() -> None:
    """Concise natural answers must stay on the Agent path."""

    question = "最近的一场比赛尼克斯赢了吗，怎么赢的"
    answer = "尼克斯靠防守守住了胜利。"

    assert not ChatUseCase._looks_like_incomplete_agent_answer(answer, question)
    assert ChatUseCase._ground_agent_answer(question, answer, []) == answer


def test_recap_with_unclosed_emphasis_or_dangling_clause_is_truncated() -> None:
    question = "最后一场比赛是怎样的，谁赢了怎么赢的"

    assert ChatUseCase._looks_like_incomplete_agent_answer(
        "**尼克斯 94–90 客场击败马刺。\n\n需要说明的是；",
        question,
    )


def test_search_recovery_does_not_expose_supplement_heading() -> None:
    raw = (
        "公开资料线索（待交叉核验）：\n"
        "- **尼克斯夺冠**：尼克斯以4-1击败马刺。\n"
        "- **第五场报道**：尼克斯在第五场以94-90取胜。\n"
        "- **赛后分析**：收官阶段防守执行决定胜负。"
    )

    answer = ChatUseCase._synthesize_search_answer(
        "2026尼克斯-马刺", raw, public=True
    )

    assert "补充线索" not in answer
    assert "公开资料线索" not in answer
    assert "相关报道还提到" not in answer
    assert "交叉核验" not in answer
    assert "不标记为已核验事实" not in answer
    assert "尼克斯以4-1击败马刺" in answer


def test_title_only_search_results_do_not_become_public_answer() -> None:
    """A result headline without a snippet is not usable factual evidence."""

    raw = (
        "公开资料线索（待交叉核验）：\n"
        "- **尼克斯夺冠：2026 NBA 总决赛完整回顾**\n"
        "- **NBA 总决赛赛程与结果**\n"
    )

    answer = ChatUseCase._synthesize_search_answer(
        "2026尼克斯-马刺", raw, public=True
    )

    assert "尼克斯夺冠：2026 NBA 总决赛完整回顾" not in answer
    assert "NBA 总决赛赛程与结果" not in answer
    assert "资料不足" in answer or "没有找到" in answer


def test_title_only_search_hit_is_not_used_as_public_evidence() -> None:
    """A headline without a snippet must not become a factual answer."""

    raw = (
        "公开资料线索（待交叉核验）：\n"
        "- **尼克斯荣膺2026NBA总冠军**\n"
    )

    answer = ChatUseCase._synthesize_search_answer(
        "2026尼克斯-马刺", raw, public=True
    )

    assert answer == "目前可用资料不足以可靠回答这个问题。"
    assert "尼克斯荣膺2026NBA总冠军" not in answer


def test_search_synthesis_uses_summary_without_copying_headline() -> None:
    """The title ranks evidence internally; only its summary is answerable."""

    raw = (
        "公开资料线索（待交叉核验）：\n"
        "- **尼克斯荣膺2026NBA总冠军**：报道记录总决赛第五场以94-90结束。\n"
    )

    answer = ChatUseCase._synthesize_search_answer(
        "2026尼克斯-马刺", raw, public=True
    )

    assert "总决赛第五场以94-90结束" in answer
    assert "尼克斯荣膺2026NBA总冠军" not in answer


def test_search_recovery_with_title_only_observation_does_not_leak_headline() -> None:
    raw = "公开资料线索（待交叉核验）：\n- **尼克斯荣膺2026NBA总冠军**"
    answer = ChatUseCase._ground_agent_answer(
        "2026尼克斯-马刺",
        "暂未找到两队的公开交手记录，请补充日期或场次。",
        [
            {
                "status": "completed",
                "intent": "web_search",
                "answer_markdown": raw,
            }
        ],
    )

    assert answer == "目前可用资料不足以可靠回答这个问题。"
    assert "尼克斯荣膺2026NBA总冠军" not in answer


def test_public_search_projection_hides_bold_and_provider_headings() -> None:
    raw = (
        "结论：尼克斯在系列赛中取胜。\n\n"
        "**搜索摘要**\n"
        "- **报道一**：末节守住领先。\n"
        "\n"
        "公开资料摘要：\n"
        "- **报道二**：布伦森在收官阶段得分。\n"
        "\n"
        "公开报道尚未与结构化比赛记录交叉核验。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert "结论：尼克斯在系列赛中取胜。" in answer
    assert "搜索摘要" not in answer
    assert "公开资料摘要" not in answer
    assert "报道一" not in answer
    assert "报道二" not in answer
    assert "**" not in answer
    assert "公开报道尚未与结构化比赛记录交叉核验" not in answer
    assert "这些内容不标记为已核验事实" not in answer


def test_public_search_projection_preserves_next_composed_paragraph() -> None:
    raw = (
        "结论：尼克斯赢球。\n\n"
        "公开网页线索：\n"
        "- 一条网页摘要\n\n"
        "综合结论：末节防守是已报道的转折点。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert "一条网页摘要" not in answer
    assert "综合结论：末节防守是已报道的转折点。" in answer


def test_public_search_projection_hides_adjacent_search_sections() -> None:
    """Adjacent provider headings must not reopen a leaked bullet section."""

    raw = (
        "结论：尼克斯赢球。\n\n"
        "**搜索摘要**\n"
        "- 第一条网页摘要\n"
        "公开资料摘要：\n"
        "- 第二条网页摘要\n"
        "公开网页线索：\n"
        "- 第三条网页摘要\n\n"
        "综合结论：收官阶段的执行决定胜负。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert "结论：尼克斯赢球。" in answer
    assert "综合结论：收官阶段的执行决定胜负。" in answer
    assert "第一条网页摘要" not in answer
    assert "第二条网页摘要" not in answer
    assert "第三条网页摘要" not in answer
    assert "搜索摘要" not in answer
    assert "公开资料摘要" not in answer
    assert "公开网页线索" not in answer


def test_public_search_projection_hides_unbulleted_titles_and_excerpts() -> None:
    """Plain provider rows stay internal until an explicit answer boundary."""

    raw = (
        "结论：尼克斯赢下系列赛。\n\n"
        "搜索摘要：\n"
        "尼克斯荣膺2026NBA总冠军\n"
        "这是一段没有项目符号的网页摘要，后面还有更多检索文本。\n"
        "第二段网页摘要也不应显示。\n\n"
        "综合结论：尼克斯在收官阶段守住优势。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert "结论：尼克斯赢下系列赛。" in answer
    assert "综合结论：尼克斯在收官阶段守住优势。" in answer
    assert "尼克斯荣膺2026NBA总冠军" not in answer
    assert "没有项目符号的网页摘要" not in answer
    assert "第二段网页摘要" not in answer


def test_public_search_projection_preserves_inline_reported_conclusion() -> None:
    raw = "根据搜索结果，尼克斯在末节守住领先。"

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert answer == "尼克斯在末节守住领先。"


def test_public_search_projection_hides_quote_table_and_inline_appendix() -> None:
    raw = (
        "结论：尼克斯赢下系列赛。补充线索：第一篇报道摘要。\n"
        "> **搜索摘要**\n"
        "| 标题 | 摘要 |\n"
        "| --- | --- |\n"
        "| 新闻标题 | 新闻正文 |\n"
        "总结：尼克斯守住了收官阶段的领先。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert "结论：尼克斯赢下系列赛。" in answer
    assert "总结：尼克斯守住了收官阶段的领先。" in answer
    assert "补充线索" not in answer
    assert "第一篇报道摘要" not in answer
    assert "新闻标题" not in answer
    assert "新闻正文" not in answer


def test_plain_title_only_search_row_cannot_become_a_fact() -> None:
    raw = "公开资料线索：\n1. 尼克斯荣膺2026NBA总冠军"

    answer = ChatUseCase._synthesize_search_answer(
        "2026尼克斯-马刺", raw, public=True
    )

    assert answer == "目前可用资料不足以可靠回答这个问题。"
    assert "尼克斯荣膺2026NBA总冠军" not in answer


def test_honest_partial_agent_answer_is_not_replaced() -> None:
    answer = (
        "能确认终场比分是108–104，但最后一投的出手者无法确认，"
        "因为现有逐回合记录没有标注。"
    )
    observation = {
        "status": "completed",
        "intent": "web_search",
        "answer_markdown": "公开资料线索：\n- **赛后记录**：终场比分为108–104。",
    }

    grounded = ChatUseCase._ground_agent_answer(
        "最后一投是谁？", answer, [observation]
    )

    assert grounded == answer


def test_open_recap_cannot_reverse_a_typed_winner_score_relation() -> None:
    observation = {
        "status": "completed",
        "intent": "nba_query",
        "answer_markdown": (
            "对阵双方：雷霆 vs 凯尔特人。\n\n"
            "凯尔特人 以 108–104 取胜。"
        ),
    }
    wrong = "雷霆以108–104赢了凯尔特人，原因是末节执行更好。"

    grounded = ChatUseCase._ground_agent_answer(
        "雷霆为什么能赢下这场比赛？", wrong, [observation]
    )

    assert "雷霆以108–104赢了凯尔特人" not in grounded
    assert "凯尔特人 以 108–104 取胜" in grounded


def test_basketball_internal_execution_wording_is_not_runtime_truncation() -> None:
    answer = "球队内部执行流程很清晰，末节换防和篮板保护更到位。"

    assert not ChatUseCase._looks_like_incomplete_agent_answer(
        answer, "这场比赛为什么能赢？"
    )
    assert ChatUseCase._ground_agent_answer(
        "这场比赛为什么能赢？", answer, []
    ) == answer


def test_sentence_initial_basketball_internal_execution_is_preserved() -> None:
    answer = "内部执行流程上，球队末节换防和篮板保护更到位。"

    assert not ChatUseCase._looks_like_incomplete_agent_answer(
        answer, "这场比赛为什么能赢？"
    )
    assert ChatUseCase._ground_agent_answer(
        "这场比赛为什么能赢？", answer, []
    ) == answer


def test_honest_partial_recap_is_not_classified_as_a_failed_answer() -> None:
    answers = (
        "尼克斯赢了，不过具体过程无法确认。",
        "尼克斯末节守住优势，但完整战术无法确认。",
    )

    for answer in answers:
        assert not ChatUseCase._looks_like_failed_search_answer(answer), answer


def test_tactical_quality_gate_requires_composed_actionable_advice() -> None:
    question = "如何同时限制库里的无球跑动和挡拆？"
    low_quality = (
        "分析：球迷热议库里的无球跑动。"
        "另外，文章提到挡拆。"
        "另外，公开资料认为防守很重要。"
    )
    high_quality = (
        "核心建议：用多层协防压缩他的接球与持球空间。\n"
        "- 无球端追防并阻断接球路线。\n"
        "- 挡拆端挤过掩护，内线短暂延误持球。\n"
        "- 弱侧及时轮转补位，优先保护篮下。"
    )

    assert not ChatUseCase._tactical_answer_is_high_quality(
        question, low_quality, []
    )
    assert ChatUseCase._tactical_answer_is_high_quality(
        question, high_quality, []
    )


def test_tactical_recovery_is_generic_bounded_and_question_driven() -> None:
    answer = ChatUseCase._tactical_advice_recovery(
        "如何同时限制库里的无球跑动和挡拆？"
    )

    assert "阻断接球路线" in answer
    assert "挤过掩护" in answer
    assert "弱侧轮转" in answer
    assert "保护篮下" in answer
    assert 2 <= len(re.findall(r"(?m)^- ", answer)) <= 4
    assert "库里一定" not in answer
    for forbidden in ("球迷热议", "文章", "报道", "公开资料", "另外"):
        assert forbidden not in answer


def test_public_projection_preserves_natural_summary_boundaries() -> None:
    for boundary in ("综合来看", "总体来看", "总的来说", "综合判断", "简要回答"):
        raw = (
            "搜索摘要：\n"
            "一条不应公开展示的检索摘要。\n"
            f"{boundary}：尼克斯在收官阶段守住了领先。"
        )

        answer = ChatUseCase._strip_public_search_sections(raw)

        assert "检索摘要" not in answer
        assert f"{boundary}：尼克斯在收官阶段守住了领先。" in answer


def test_public_projection_removes_internal_retrieval_narration() -> None:
    raw = (
        "根据搜索结果，尼克斯在收官阶段守住了领先。\n"
        "缓存命中后，系统复用了上一轮结果。\n"
        "我查询了结构化比赛记录，但没有找到直接匹配。\n"
        "本轮已调用工具完成搜索。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert answer == "尼克斯在收官阶段守住了领先。"
    assert "缓存" not in answer
    assert "结构化" not in answer
    assert "工具" not in answer


def test_public_projection_removes_evidence_process_framing_only() -> None:
    raw = (
        "基于已核验事实和交叉检索材料，回答：\n\n"
        "**尼克斯最近一场赢了，终场 94–90。**\n\n"
        "**怎么赢的（已核验硬事实）：**\n"
        "- 布伦森帮助球队守住优势。\n\n"
        "**过程分析（依据公开报道，谨慎表述）：**\n"
        "- 尼克斯末节防守更稳。\n\n"
        "> 说明：结构化记录未提供完整逐回合，以上基于公开复盘。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert "尼克斯最近一场赢了，终场 94–90" in answer
    assert "怎么赢的：" in answer
    assert "过程分析：" in answer
    assert "末节防守更稳" in answer
    assert "交叉检索" not in answer
    assert "已核验硬事实" not in answer
    assert "依据公开报道" not in answer
    assert "结构化记录" not in answer


def test_public_projection_rewrites_production_style_evidence_narration() -> None:
    raw = (
        "已经拿到比分硬事实和过程线索，综合回答。\n\n"
        "**结论：尼克斯赢了。** 尼克斯客场 **94–90** 击败马刺。\n\n"
        "需要说明的是，结构化记录没有逐节攻防走势，"
        "打法细节（哪些回合拉开、何时反超）来自公开报道线索，"
        '措辞以"分析/报道"限定；比分 94–90、布伦森 45 分、'
        "4–1 夺冠这些是已核验的硬事实。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert answer.startswith("**结论：尼克斯赢了。**")
    assert "94–90" in answer
    assert "布伦森 45 分" in answer
    assert "目前缺少逐节攻防走势" in answer
    assert "无法可靠还原具体回合和反超节点" in answer
    for hidden in (
        "拿到比分硬事实",
        "过程线索",
        "结构化记录",
        "公开报道线索",
        "措辞以",
        "已核验的硬事实",
    ):
        assert hidden not in answer


def test_realistic_hermes_recap_does_not_expose_supplement_clues() -> None:
    """Search grounding is internal; the public answer keeps only the synthesis."""

    raw = (
        "根据相关公开报道（尚待更多公开来源交叉核验），该届季后赛，"
        "尼克斯客场94比90击败马刺，大比分4比1夺冠，布伦森当选总决赛MVP。\n\n"
        "补充线索：\n\n"
        "- 布伦森总决赛场均32.6分 2018届新秀火力第一 6月15日，尼克斯4比1击败马刺，"
        "拿下2025-26赛季NBA总冠军\n"
        "- 尼克斯荣膺2026NBA总冠军\n\n"
        "这些内容目前按公开网页线索处理，不标记为已核验事实。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert "尼克斯客场94比90击败马刺" in answer
    assert "补充线索" not in answer
    assert "布伦森总决赛场均32.6分" not in answer
    assert "尼克斯荣膺2026NBA总冠军" not in answer
    assert "公开报道尚未与结构化比赛记录交叉核验" not in answer
    assert "搜索" not in answer
    assert "provider" not in answer.lower()


def test_public_projection_repairs_single_game_wording_for_series_award() -> None:
    variants = (
        "他本场当选总决赛 MVP",
        "本场布伦森当选总决赛 MVP",
        "布伦森在这场比赛荣膺总决赛最有价值球员",
        "G5 布伦森获得 FMVP",
    )

    for phrase in variants:
        raw = f"尼克斯客场以 94–90 击败马刺。布伦森得到 45 分，{phrase}。"
        answer = ChatUseCase._strip_public_search_sections(raw)

        assert "94–90" in answer
        assert "45 分" in answer
        assert "最终当选总决赛 MVP" in answer
        assert "本场当选" not in answer
        assert "这场比赛荣膺" not in answer
        assert "G5 布伦森获得" not in answer


def test_public_projection_removes_paraphrased_internal_workflow() -> None:
    raw = "\n".join(
        (
            "我们搜索后发现，尼克斯守住了领先。",
            "命中缓存后，系统复用了数据库结果。",
            "我查询了结构化记录，但没有找到逐回合。",
            "已调用工具完成搜索。",
            "Hermes provider 返回了缓存中的搜索摘要。",
            "结论：尼克斯 94–90 赢球，补充线索：新闻标题和摘要。",
        )
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert "尼克斯守住了领先" in answer
    assert "目前缺少逐回合" in answer
    assert "结论：尼克斯 94–90 赢球" in answer
    for hidden in (
        "我们搜索后发现",
        "缓存",
        "数据库",
        "结构化记录",
        "已调用工具",
        "Hermes",
        "provider",
        "补充线索",
        "新闻标题和摘要",
    ):
        assert hidden not in answer


def test_public_projection_turns_model_workflow_meta_prose_into_fan_answer() -> None:
    raw = (
        "搜索结果存在不一致，来源间分数说法不同，且包含未经核验的热度类信息。"
        "我只使用能确认的部分，谨慎综合。\n\n"
        "先给结论，再给有限的分析，并明确标注哪些是确认事实、"
        "哪些过程细节尚不足以确认。\n\n"
        "**结论：尼克斯赢了。** 尼克斯 94–90 击败马刺。\n\n"
        "可以确认的硬事实：\n- 布伦森得到 45 分。\n\n"
        "关于怎么赢的：目前缺少分节走势或逐回合数据，"
        "公开报道的说法也不完全一致（有报道对全场分数口径不一致），"
        "过程细节尚不足以确认，我不能凭空替你还原每节攻防。\n"
        "可以推断的有限可能因素：尼克斯的防守可能起了作用。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert answer.startswith("**结论：尼克斯赢了。**")
    assert "**关键数据**" in answer
    assert "布伦森得到 45 分" in answer
    assert "目前缺少分节走势或逐回合数据" in answer
    assert "暂时无法可靠还原" in answer
    assert "**谨慎分析**" in answer
    for hidden in (
        "搜索结果",
        "来源间",
        "未经核验",
        "我只使用",
        "谨慎综合",
        "先给结论",
        "明确标注",
        "硬事实",
        "公开报道",
        "分数口径",
        "凭空",
        "可以推断的有限可能因素",
    ):
        assert hidden not in answer


def test_public_projection_removes_live_query_and_report_caveat() -> None:
    raw = (
        "**赢了。** 最近一场是系列赛 G5，尼克斯客场 **94–90** 击败马刺。\n\n"
        "**怎么赢的：**\n"
        "- 布伦森全场砍下全场最高（也是本队关键）的 **45 分**，"
        "报道描述他在决胜第四节独揽 15 分。\n\n"
        "需要说明：查询记录里有比分和布伦森统计可确认；"
        "第四节具体回合的攻防细节来自比赛复盘报道，属于过程描述，"
        "可作为参考但要谨慎对待。"
        "总冠军归属（4–1 击败马刺捧杯）可以确认。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert answer.startswith("**赢了。**")
    assert "94–90" in answer
    assert "45 分" in answer
    assert "决胜第四节独揽 15 分" in answer
    assert "砍下全场最高的" in answer
    for hidden in (
        "查询记录",
        "复盘报道",
        "作为参考",
        "谨慎对待",
        "总冠军归属",
        "需要说明",
        "报道描述",
        "全场砍下全场最高",
    ):
        assert hidden not in answer


def test_public_projection_repairs_live_recap_markdown_and_analysis_meta() -> None:
    raw = (
        "- 布伦森砍下全场最高的 45 分（其余球员合计 49 分）。"
        "- 结合公开报道分析，尼克斯赢球大致有三点：\n"
        "1. 他，这场收官战继续在关键时刻稳住局面。\n"
        "2. 防守端压住分差。\n\n"
        "目前缺少逐节走势，所以暂时无法逐回合还原；"
        "上述第 1、2 点是基于比分和总体格局的谨慎分析，"
        "而非可确认的逐回合事实。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert "49 分）。\n\n综合来看" in answer
    assert "综合来看，尼克斯赢球大致有三点" in answer
    assert "大致有三点：\n\n1." in answer
    assert "他在这场收官战" in answer
    assert "目前缺少逐节走势" in answer
    for hidden in ("公开报道", "上述第", "谨慎分析", "逐回合事实"):
        assert hidden not in answer


def test_public_projection_hides_nba_tool_framing_and_naturalizes_missing_data() -> None:
    raw = (
        "根据 NBA 工具核验，尼克斯 4–1 击败马刺。\n\n"
        "现有技术统计中，布伦森得到 45 分。\n\n"
        "当前记录没有逐回合或分节走势，因此只能给出有限回顾，"
        "不能还原每节攻防过程。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert answer.startswith("尼克斯 4–1 击败马刺")
    assert "布伦森得到 45 分" in answer
    assert "击败马刺。\n\n布伦森得到 45 分" in answer
    assert "目前缺少逐回合或分节走势，无法可靠还原每节攻防过程" in answer
    assert "工具" not in answer
    assert "当前记录" not in answer
    assert "现有技术统计" not in answer
    assert "有限回顾" not in answer


def test_agent_grounding_removes_unobserved_concrete_win_causes() -> None:
    question = "最近的一场比赛尼克斯赢了吗，怎么赢的"
    observations = [
        {
            "status": "completed",
            "intent": "nba_query",
            "answer_markdown": "尼克斯客场以 94–90 击败马刺，布伦森得到 45 分。",
            "evidence_state": "verified",
        },
        {
            "status": "completed",
            "intent": "web_search",
            "answer_markdown": (
                "公开资料线索（待交叉核验）：\n"
                "- **布伦森关键抢断锁定胜局**：尼克斯 94–90 赢下收官战。"
            ),
            "evidence_state": "partial",
        },
    ]
    hermes_answer = (
        "尼克斯客场以 94–90 赢下比赛。"
        "他们靠关键篮板、连续封盖和关键抢断决定胜负。"
    )

    answer = ChatUseCase._ground_agent_answer(question, hermes_answer, observations)

    assert "尼克斯客场以 94–90 赢下比赛" in answer
    assert "关键篮板" not in answer
    assert "连续封盖" not in answer
    assert "关键抢断" not in answer


def test_agent_grounding_keeps_supported_search_detail_but_not_unrelated_aggregate() -> None:
    question = "最近的一场比赛尼克斯赢了吗，怎么赢的"
    observations = [
        {
            "status": "completed",
            "intent": "nba_query",
            "answer_markdown": "尼克斯客场以 94–90 击败马刺，布伦森得到 45 分。",
            "evidence_state": "verified",
        },
        {
            "status": "completed",
            "intent": "web_search",
            "answer_markdown": (
                "公开资料线索（待交叉核验）：\n"
                "- **收官战复盘**：报道写道，尼克斯末节通过连续抢断守住胜利；"
                "布伦森整届季后赛场均末节 9.9 分。"
            ),
            "evidence_state": "partial",
        },
    ]
    hermes_answer = (
        "尼克斯客场以 94–90 赢下比赛。"
        "从比赛过程看，他们靠末节连续抢断守住胜利。"
        "布伦森整届季后赛场均末节 9.9 分，这也是本场取胜原因。"
    )

    answer = ChatUseCase._ground_agent_answer(question, hermes_answer, observations)

    assert "末节连续抢断守住胜利" in answer
    assert "整届季后赛" not in answer
    assert "9.9" not in answer


def _verified_g5_observation() -> dict[str, object]:
    return {
        "status": "completed",
        "intent": "nba_query",
        "answer_markdown": (
            "总决赛 G5，尼克斯客场以 94–90 击败马刺，"
            "布伦森得到 45 分。"
        ),
        "evidence_state": "verified",
    }


def _search_recap_observation(summary: str) -> dict[str, object]:
    return {
        "status": "completed",
        "intent": "web_search",
        "answer_markdown": (
            "公开资料线索（待交叉核验）：\n"
            f"- **总决赛复盘**：{summary}"
        ),
        "evidence_state": "partial",
    }


@pytest.mark.parametrize(
    "causal_claim",
    (
        "关键抢断锁定比赛",
        "关键抢断奠定胜局",
        "尼克斯胜在关键抢断",
        "关键抢断是关键",
        "关键抢断是制胜点",
        "关键抢断是胜负手",
    ),
    ids=(
        "locks_game",
        "seals_win",
        "wins_because",
        "is_key",
        "winning_point",
        "decisive_factor",
    ),
)
def test_agent_grounding_rejects_unobserved_causal_trigger_variants(
    causal_claim: str,
) -> None:
    answer = ChatUseCase._ground_agent_answer(
        "总决赛 G5 尼克斯怎么赢的？",
        f"尼克斯客场以 94–90 赢下比赛。{causal_claim}。",
        [_verified_g5_observation()],
    )

    assert "尼克斯客场以 94–90 赢下比赛" in answer
    assert "关键抢断" not in answer


def test_broad_hedge_cannot_bypass_unobserved_causal_claim_guard() -> None:
    answer = ChatUseCase._ground_agent_answer(
        "总决赛 G5 尼克斯怎么赢的？",
        (
            "尼克斯客场以 94–90 赢下比赛。"
            "可能还有其他因素，但事实是靠关键抢断赢球。"
        ),
        [_verified_g5_observation()],
    )

    assert "尼克斯客场以 94–90 赢下比赛" in answer
    assert "关键抢断" not in answer


@pytest.mark.parametrize(
    "evidence_summary",
    (
        "报道明确写道，尼克斯并未通过关键抢断锁定胜局。",
        "报道指出，关键抢断并不是尼克斯本场的胜因。",
        "报道记录马刺依靠关键抢断一度反超比分。",
        "报道记录尼克斯防住了马刺的关键抢断。",
        "总决赛 G4 中尼克斯通过关键抢断锁定胜局；G5 没有这一事件。",
        "总决赛G4中尼克斯通过关键抢断锁定胜局；G5没有这一事件。",
        "总决赛 G4 尼克斯通过关键抢断锁定胜局，而 G5 没有这一事件。",
    ),
    ids=(
        "negated_event",
        "postposed_negation",
        "opponent_event",
        "opponent_possessive",
        "other_game_event",
        "other_game_event_without_spaces",
        "other_game_and_current_game_in_one_sentence",
    ),
)
def test_wrong_scope_search_evidence_cannot_support_current_game_cause(
    evidence_summary: str,
) -> None:
    answer = ChatUseCase._ground_agent_answer(
        "总决赛 G5 尼克斯怎么赢的？",
        (
            "尼克斯客场以 94–90 赢下比赛。"
            "尼克斯靠关键抢断锁定胜局。"
        ),
        [_verified_g5_observation(), _search_recap_observation(evidence_summary)],
    )

    assert "尼克斯客场以 94–90 赢下比赛" in answer
    assert "关键抢断" not in answer


def test_other_date_event_cannot_support_current_game_cause() -> None:
    answer = ChatUseCase._ground_agent_answer(
        "2026-06-14 尼克斯怎么赢的？",
        "尼克斯客场以 94–90 赢下比赛。尼克斯靠关键抢断锁定胜局。",
        [
            _verified_g5_observation(),
            _search_recap_observation(
                "2026年6月11日，尼克斯通过关键抢断锁定胜局。"
            ),
        ],
    )

    assert "尼克斯客场以 94–90 赢下比赛" in answer
    assert "关键抢断" not in answer


def test_other_game_search_title_can_restrict_but_not_authorize_summary() -> None:
    answer = ChatUseCase._ground_agent_answer(
        "总决赛 G5 尼克斯怎么赢的？",
        "尼克斯客场以 94–90 赢下比赛。尼克斯靠关键抢断锁定胜局。",
        [
            _verified_g5_observation(),
            {
                "status": "completed",
                "intent": "web_search",
                "answer_markdown": (
                    "公开资料线索（待交叉核验）：\n"
                    "- **总决赛 G4 复盘**：尼克斯通过关键抢断锁定胜局。"
                ),
                "evidence_state": "partial",
            },
        ],
    )

    assert "尼克斯客场以 94–90 赢下比赛" in answer
    assert "关键抢断" not in answer


def test_single_game_recap_drops_unrelated_other_game_sentence() -> None:
    observations = [
        _verified_g5_observation(),
        _search_recap_observation(
            "G5 布伦森末节得分；东部决赛 G7 他命中关键球。"
        ),
    ]
    answer = ChatUseCase._ground_agent_answer(
        "最近的一场比赛尼克斯赢了吗，怎么赢的",
        (
            "尼克斯 94–90 赢下总决赛 G5。"
            "布伦森在末节完成关键得分。"
            "另外，东部决赛对阵凯尔特人的 G7，"
            "他连续命中关键球锁定胜局。"
        ),
        observations,
    )

    assert "尼克斯 94–90 赢下总决赛 G5" in answer
    assert "布伦森在末节完成关键得分" in answer
    assert "东部决赛" not in answer
    assert "凯尔特人" not in answer
    assert "G7" not in answer


def test_single_game_recap_keeps_duration_but_drops_redundant_score_totals() -> None:
    answer = ChatUseCase._ground_agent_answer(
        "最近的一场比赛尼克斯赢了吗，怎么赢的",
        (
            "尼克斯以 94–90 取胜。"
            "比赛实际耗时 2小时46分钟。"
            "这场比赛：分差 4。"
            "这场比赛：总得分 184。"
        ),
        [_verified_g5_observation()],
    )

    assert answer == "尼克斯以 94–90 取胜。比赛实际耗时 2小时46分钟。"


@pytest.mark.parametrize(
    "wording",
    (
        "布伦森在比赛结束后获得 FMVP。",
        "布伦森凭借本场表现当选 FMVP。",
    ),
    ids=("after_game", "based_on_game_performance"),
)
def test_series_award_scope_preserves_valid_temporal_or_basis_wording(
    wording: str,
) -> None:
    assert ChatUseCase._normalize_series_award_scope(wording) == wording


@pytest.mark.parametrize(
    ("out_of_scope_clause", "forbidden_fragments"),
    (
        (
            "并在本场当选 FMVP",
            ("FMVP", "总决赛 MVP"),
        ),
        (
            "整届季后赛场均末节 9.9 分",
            ("场均", "9.9"),
        ),
    ),
    ids=("fmvp_clause", "per_game_clause"),
)
def test_same_sentence_keeps_verified_points_when_scope_clause_is_removed(
    out_of_scope_clause: str,
    forbidden_fragments: tuple[str, ...],
) -> None:
    observations = [
        _verified_g5_observation(),
        _search_recap_observation(
            "布伦森最终当选总决赛 MVP，整届季后赛场均末节 9.9 分。"
        ),
    ]
    answer = ChatUseCase._ground_agent_answer(
        "总决赛 G5 尼克斯怎么赢的？",
        (
            "尼克斯客场以 94–90 赢下比赛。"
            f"布伦森得到 45 分，{out_of_scope_clause}。"
        ),
        observations,
    )

    assert "尼克斯客场以 94–90 赢下比赛" in answer
    assert "布伦森得到 45 分" in answer
    for fragment in forbidden_fragments:
        assert fragment not in answer


def test_single_game_recap_omits_unasked_historical_drought_context() -> None:
    observations = [
        _verified_g5_observation(),
        _search_recap_observation(
            "尼克斯以 4–1 夺冠，终结队史长达 53 年的冠军荒。"
        ),
    ]

    answer = ChatUseCase._ground_agent_answer(
        "总决赛 G5 尼克斯怎么赢的？",
        (
            "尼克斯客场以 94–90 赢下 G5。"
            "尼克斯以总比分 4–1 夺冠，终结队史长达 53 年的冠军荒。"
        ),
        observations,
    )

    assert "尼克斯以总比分 4–1 夺冠" in answer
    assert "53" not in answer
    assert "冠军荒" not in answer


def test_live_single_game_recap_drops_history_comparison_totals_and_process_note() -> None:
    """Keep the Agent's recap while hiding unrelated and implementation prose."""

    observations = [
        _verified_g5_observation(),
        _search_recap_observation(
            "布伦森得到 45 分，尼克斯靠他第四节的火力拿下胶着的收官战。"
        ),
    ]
    answer = ChatUseCase._ground_agent_answer(
        "最近的一场比赛尼克斯赢了吗，怎么赢的",
        (
            "最新一场（G5）尼克斯赢了，而且是赢得总冠军的关键一战。\n\n"
            "**结果**：尼克斯客场 **94–90** 击败马刺，系列赛 **4–1** 夺冠，"
            "时隔53年再捧总冠军。\n\n"
            "**怎么赢的**：\n"
            "- **核心是布伦森**：全场砍下 **45 分**（投篮27中14，三分7中4，"
            "罚球15中13），出战41分钟，另有3篮板3助攻2抢断。"
            "这是客场夺冠战中仅次于乔丹（1998对爵士45分）的历史第二高分后卫表现。\n"
            "- 比赛分差仅 **4 分**，是一场收官战打得相当胶着的硬仗，"
            "靠布伦森第四节的火力咬住并拿下；总比分 **184**。\n\n"
            "需要说明：结构化记录只提供了最终比分和布伦森的技术统计，"
            "没有逐节走势，所以具体每一节的攻防节奏无法完全还原；"
            "过程细节来自公开报道，措辞上以胜因分析为主。"
        ),
        observations,
    )

    assert "最新一场（G5）尼克斯赢了" in answer
    assert "尼克斯客场 **94–90** 击败马刺" in answer
    assert "全场砍下 **45 分**" in answer
    assert "靠布伦森第四节的火力咬住并拿下" in answer
    for hidden in (
        "时隔53年",
        "乔丹",
        "历史第二",
        "总比分 **184**",
        "需要说明",
        "结构化记录",
        "公开报道",
        "措辞上",
    ):
        assert hidden not in answer


def test_live_recap_projection_rewrites_evidence_labels_into_fan_language() -> None:
    raw = (
        "**赢了，尼克斯 94–90 客场战胜马刺，以 4–1 拿下系列赛。**\n\n"
        "**怎么赢的，可确认与谨慎限定的分析：**\n"
        "- 硬事实：尼克斯以 94–90 取胜，布伦森 45 分全场最高。\n"
        "- 过程（来自赛后报道，措辞保留）：马刺首节领先 10 分，"
        "末节布伦森连续冲击内线实现逆转。"
        "逐回合与分节完整走势尚无结构化记录，"
        "恢复单场录像或官方 PBP 可核查更多细节。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert answer.startswith("**赢了，尼克斯 94–90 客场战胜马刺")
    assert "**怎么赢的：**" in answer
    assert "- **关键数据**：尼克斯以 94–90 取胜" in answer
    assert "- **比赛过程**：马刺首节领先 10 分" in answer
    assert "目前无法完整还原逐回合和分节走势" in answer
    for hidden in (
        "可确认与谨慎限定",
        "硬事实",
        "来自赛后报道",
        "措辞保留",
        "结构化记录",
        "恢复单场录像",
        "官方 PBP",
    ):
        assert hidden not in answer


def test_live_recap_projection_handles_unbulleted_evidence_label_variants() -> None:
    raw = (
        "综合结构化事实和过程材料，给结论。\n\n"
        "**是的，尼克斯赢了。** 尼克斯客场 **94–90** 胜马刺。\n\n"
        "关键数据（可确认）：布伦森拿到全场最高 **45 分**。\n\n"
        "过程（来自报道，谨慎表述）：马刺首节曾领先，"
        "尼克斯靠末节发力完成逆转。\n\n"
        "说明一点范围：目前缺少逐节走势，上述内容来自公开复盘材料而非"
        "官方逐回合记录，细节口径存在轻微出入。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert answer.startswith("**是的，尼克斯赢了。**")
    assert "**关键数据**：布伦森拿到全场最高 **45 分**" in answer
    assert "**比赛过程**：马刺首节曾领先" in answer
    assert "尼克斯靠末节发力完成逆转" in answer
    for hidden in (
        "综合结构化事实",
        "过程材料",
        "可确认",
        "来自报道",
        "谨慎表述",
        "说明一点范围",
        "公开复盘材料",
        "官方逐回合记录",
        "口径存在",
    ):
        assert hidden not in answer


def test_live_recap_projection_removes_duplicate_intro_and_dangling_meta_prose() -> None:
    raw = (
        "这场是总决赛 G5，尼克斯客场 94–90 击败马刺，以 4–1 夺冠。"
        "回答如下。\n\n"
        "**结论：尼克斯赢了，客场 94–90 击败马刺。** "
        "这场是总决赛第五场，尼克斯由此以 4–1 夺冠。\n\n"
        "赢下的方式：布伦森拿到全场最高 **45 分**，"
        "并在客场领先场面下完成逆转。可以确认的是。\n\n"
        "需要说明的一点：目前缺少逐节、逐回合的攻防走势，"
        "过程报道的细节来自公开复盘资料，需以官方整场录像为准。"
    )

    answer = ChatUseCase._strip_public_search_sections(raw)

    assert answer.startswith("**结论：尼克斯赢了")
    assert "**怎么赢的：**" in answer
    assert "布伦森拿到全场最高 **45 分**" in answer
    assert "在客场完成逆转" in answer
    for hidden in (
        "回答如下",
        "赢下的方式",
        "领先场面下完成逆转",
        "可以确认的是",
        "需要说明的一点",
        "过程报道",
        "公开复盘资料",
        "官方整场录像",
    ):
        assert hidden not in answer


def test_live_recap_treats_g5_as_game_number_and_drops_championship_gap() -> None:
    observations = [
        _verified_g5_observation(),
        _search_recap_observation(
            "尼克斯末节依靠布伦森的得分完成逆转，最终夺冠。"
        ),
    ]
    answer = ChatUseCase._ground_agent_answer(
        "最近的一场比赛尼克斯赢了吗，怎么赢的",
        (
            "尼克斯客场 94–90 战胜马刺，拿下了系列赛第五场胜利。"
            "这场之后以 4–1 淘汰马刺夺冠。"
            "尼克斯这次夺冠终结了漫长的冠军空缺"
            "（自 1973 年以来首次登顶）。"
        ),
        observations,
    )

    assert "尼克斯客场 94–90 战胜马刺" in answer
    assert "赢下系列赛第五场比赛" in answer
    assert "以 4–1 击败马刺夺冠" in answer
    for hidden in ("第五场胜利", "冠军空缺", "1973", "首次登顶"):
        assert hidden not in answer


@pytest.mark.parametrize(
    "agent_answer",
    (
        "布伦森本场得到 45 分并且系列赛场均 32.6 分。",
        "系列赛场均 32.6 分，但布伦森本场得到 45 分。",
        "布伦森本场得到 45 分，同时获得 FMVP。",
    ),
)
def test_scope_pruning_keeps_same_sentence_game_stat_without_bad_punctuation(
    agent_answer: str,
) -> None:
    observations = [
        _verified_g5_observation(),
        _search_recap_observation(
            "布伦森最终当选总决赛 MVP，系列赛场均 32.6 分。"
        ),
    ]

    answer = ChatUseCase._ground_agent_answer(
        "总决赛 G5 尼克斯怎么赢的？",
        agent_answer,
        observations,
    )

    assert "布伦森本场得到 45 分" in answer
    assert "场均" not in answer
    assert "32.6" not in answer
    assert "FMVP" not in answer
    assert "，。" not in answer


def test_search_supported_tactics_are_not_labelled_as_verified_facts() -> None:
    observations = [
        _verified_g5_observation(),
        _search_recap_observation("尼克斯末节改用换防并收缩禁区。"),
    ]
    answer = ChatUseCase._ground_agent_answer(
        "总决赛 G5 尼克斯怎么赢的？",
        (
            "尼克斯客场以 94–90 赢下比赛。\n\n"
            "**已核验事实**：\n"
            "- 末节改用换防并收缩禁区。"
        ),
        observations,
    )

    assert "末节改用换防并收缩禁区" in answer
    assert "已核验事实" not in answer
    assert "综合判断" in answer


def test_agent_grounding_keeps_series_award_for_explicit_award_question() -> None:
    observations = [
        {
            "status": "completed",
            "intent": "web_search",
            "answer_markdown": (
                "公开资料线索（待交叉核验）：\n"
                "- **总决赛结果**：布伦森当选总决赛 MVP。"
            ),
            "evidence_state": "partial",
        }
    ]

    answer = ChatUseCase._ground_agent_answer(
        "2026 年总决赛 MVP 是谁？",
        "布伦森最终当选总决赛 MVP。",
        observations,
    )

    assert answer == "布伦森最终当选总决赛 MVP。"


def test_compact_news_answer_does_not_expose_truncation_phrase() -> None:
    raw = "标题\n" + "\n".join(f"- 线索 {index}" for index in range(5))

    answer = ChatUseCase._compact_news_answer(raw, max_items=2)

    assert "其余线索已省略" not in answer
    assert "- 线索 0" in answer
    assert "- 线索 1" in answer
