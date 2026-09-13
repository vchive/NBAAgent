from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.api.src.application.flower_session_meta import (
    FlowerSessionMetaKind,
    classify_flower_session_meta_question,
    render_flower_session_meta_answer,
)


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("我问了你几个问题", FlowerSessionMetaKind.TURN_COUNT),
        ("这是我第几个问题？", FlowerSessionMetaKind.TURN_COUNT),
        ("算上这句一共问了多少次", FlowerSessionMetaKind.TURN_COUNT),
        ("我刚才问了什么？", FlowerSessionMetaKind.LAST_USER_MESSAGE),
        ("上一问是什么", FlowerSessionMetaKind.LAST_USER_MESSAGE),
        ("我第三个问题问的啥", FlowerSessionMetaKind.INDEXED_USER_MESSAGE),
        ("我第3个问题是什么？", FlowerSessionMetaKind.INDEXED_USER_MESSAGE),
        ("你刚才回答了什么", FlowerSessionMetaKind.LAST_ASSISTANT_MESSAGE),
        ("总结一下我们刚才聊了什么", FlowerSessionMetaKind.CONVERSATION_SUMMARY),
        ("当前植物是什么", FlowerSessionMetaKind.ACTIVE_SUBJECT),
        ("现在是什么模式", FlowerSessionMetaKind.INTELLIGENCE_MODE),
        ("你是谁", FlowerSessionMetaKind.IDENTITY),
        ("who are you", FlowerSessionMetaKind.IDENTITY),
    ],
)
def test_classifies_flower_session_meta(message: str, kind: FlowerSessionMetaKind) -> None:
    result = classify_flower_session_meta_question(message)
    assert result is not None
    assert result.kind is kind


@pytest.mark.parametrize(
    "message",
    [
        "刚才那盆花为什么黄叶？",
        "它刚才浇水了吗？",
        "你刚才说的月季多久浇水？",
        "我想知道上一场花展的时间",
    ],
)
def test_factual_or_care_questions_do_not_enter_meta_route(message: str) -> None:
    assert classify_flower_session_meta_question(message) is None


def _context(**kwargs):
    defaults = {
        "completed_user_turn_count": 3,
        "turn_count": 3,
        "recent_questions": ["北阳台适合什么花？", "那绣球怎么浇水？", "绣球需要施肥吗？"],
        "recent_answers": ["可以先看耐阴品种。", "表土稍干再浇透。", "生长期薄肥勤施。"],
        "plant_name": "绣球",
        "plant": "绣球",
        "location": "上海",
        "season": "9月",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_count_and_index_use_monotonic_count_and_exclude_current_question() -> None:
    context = _context(
        completed_user_turn_count=10,
        turn_count=8,
        recent_questions=[f"问题{i}" for i in range(3, 11)] + ["我问了几个问题"],
    )
    count = render_flower_session_meta_answer(
        classify_flower_session_meta_question("我问了几个问题"),
        context,
        current_question="我问了几个问题",
    )
    assert "此前问了 **10** 个问题" in count
    assert "第 **11** 个问题" in count

    indexed = render_flower_session_meta_answer(
        classify_flower_session_meta_question("我第3个问题问的啥"),
        _context(completed_user_turn_count=10, recent_questions=[f"问题{i}" for i in range(3, 11)]),
        current_question="我第3个问题问的啥",
    )
    assert "第 **3** 个问题" in indexed
    assert "问题3" in indexed


def test_last_question_summary_and_active_subject_are_session_local() -> None:
    context = _context(recent_questions=["北阳台适合什么花？", "那绣球怎么浇水？"])
    last = render_flower_session_meta_answer(
        classify_flower_session_meta_question("我刚才问了什么"),
        context,
    )
    summary = render_flower_session_meta_answer(
        classify_flower_session_meta_question("总结一下刚才的对话"),
        context,
    )
    active = render_flower_session_meta_answer(
        classify_flower_session_meta_question("当前植物是什么"),
        context,
    )
    assert "绣球怎么浇水" in last
    assert "北阳台适合什么花" in summary
    assert "绣球" in active
    assert "上海" in active


def test_last_answer_without_answer_history_is_honest() -> None:
    query = classify_flower_session_meta_question("你刚才回答了什么")
    answer = render_flower_session_meta_answer(query, _context(recent_answers=[]))
    assert "没有可复述" in answer


def test_identity_and_mode_answers_do_not_expose_implementation() -> None:
    identity = render_flower_session_meta_answer(
        classify_flower_session_meta_question("你是谁"), _context()
    )
    mode = render_flower_session_meta_answer(
        classify_flower_session_meta_question("现在是什么模式"),
        _context(),
        requested_full=True,
        effective_full=True,
    )
    assert "种花 Agent" in identity
    assert "全智能模式" in mode
    assert all(
        term not in (identity + mode).casefold()
        for term in ("hermes", "provider", "prompt")
    )


def test_history_echo_redacts_phone_and_street_address() -> None:
    query = classify_flower_session_meta_question("我刚才问了什么")
    context = _context(
        recent_questions=["我住在上海市浦东新区花木路88号，电话13812345678"],
        completed_user_turn_count=1,
    )
    answer = render_flower_session_meta_answer(query, context)
    assert "13812345678" not in answer
    assert "花木路88号" not in answer
    assert "已隐藏" in answer
