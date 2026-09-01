from __future__ import annotations

import pytest

from apps.api.src.application.session_meta import (
    SessionMetaKind,
    classify_session_meta_question,
)


@pytest.mark.parametrize(
    "message,kind",
    [
        ("我问了你几个问题", SessionMetaKind.TURN_COUNT),
        ("这是我第几个问题？", SessionMetaKind.TURN_COUNT),
        ("算上这句一共问了多少次", SessionMetaKind.TURN_COUNT),
        ("我问了多少次", SessionMetaKind.TURN_COUNT),
        ("我刚才问了什么？", SessionMetaKind.LAST_USER_MESSAGE),
        ("上一问是什么", SessionMetaKind.LAST_USER_MESSAGE),
        ("我第三个问题问的啥", SessionMetaKind.INDEXED_USER_MESSAGE),
        ("我第3个问题是什么？", SessionMetaKind.INDEXED_USER_MESSAGE),
        ("你刚才回答了什么", SessionMetaKind.LAST_ASSISTANT_MESSAGE),
        ("上一次你怎么回答的", SessionMetaKind.LAST_ASSISTANT_MESSAGE),
        ("总结一下我们刚才聊了什么", SessionMetaKind.CONVERSATION_SUMMARY),
        ("总结下刚才的对话", SessionMetaKind.CONVERSATION_SUMMARY),
        ("你还记得我们在聊哪场吗", SessionMetaKind.ACTIVE_SUBJECT),
        ("当前选中的比赛是什么", SessionMetaKind.ACTIVE_SUBJECT),
        ("我开启全智能了吗", SessionMetaKind.INTELLIGENCE_MODE),
        ("现在是什么模式", SessionMetaKind.INTELLIGENCE_MODE),
    ],
)
def test_classifies_narrow_session_meta_questions(
    message: str, kind: SessionMetaKind
) -> None:
    query = classify_session_meta_question(message)
    assert query is not None
    assert query.kind is kind


@pytest.mark.parametrize(
    "message,index",
    [
        ("我第三个问题问的啥", 3),
        ("我第3个问题是什么？", 3),
        ("第十个问题问了什么", 10),
        ("我第12个问题是啥", 12),
    ],
)
def test_indexed_question_preserves_requested_turn(message: str, index: int) -> None:
    query = classify_session_meta_question(message)
    assert query is not None
    assert query.kind is SessionMetaKind.INDEXED_USER_MESSAGE
    assert query.turn_index == index


@pytest.mark.parametrize(
    "message",
    [
        "刚才那个球是谁投的？",
        "你刚才说谁拿了 32 分？",
        "把刚才的比赛结论再说一遍",
        "这场比赛谁打谁？",
        "当前赛季是哪一年？",
        "你用的是什么模型？",
    ],
)
def test_referential_nba_facts_do_not_enter_session_meta_route(message: str) -> None:
    assert classify_session_meta_question(message) is None
