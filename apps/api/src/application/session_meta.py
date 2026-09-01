"""Deterministic resolution for questions about the current chat session.

Session metadata belongs to the application, not to an NBA provider or the
language model.  Keeping these intents explicit prevents conversational
questions from falling through to the basketball parser while preserving the
rule that referential NBA facts must be re-verified through approved tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from apps.api.src.domain.models import ConversationContext


class SessionMetaKind(StrEnum):
    TURN_COUNT = "TURN_COUNT"
    LAST_USER_MESSAGE = "LAST_USER_MESSAGE"
    LAST_ASSISTANT_MESSAGE = "LAST_ASSISTANT_MESSAGE"
    CONVERSATION_SUMMARY = "CONVERSATION_SUMMARY"
    ACTIVE_SUBJECT = "ACTIVE_SUBJECT"
    INTELLIGENCE_MODE = "INTELLIGENCE_MODE"


@dataclass(frozen=True, slots=True)
class SessionMetaQuery:
    kind: SessionMetaKind


_END = r"(?:[!！,.，。?？\s]*)$"
_PATTERNS: tuple[tuple[SessionMetaKind, re.Pattern[str]], ...] = (
    (
        SessionMetaKind.TURN_COUNT,
        re.compile(
            r"^(?:"
            r"我(?:已经|刚才|之前|一共|总共)?(?:问了你|问了|问过你|问过)"
            r"(?:多少(?:个问题|次)?|几个问题|几次)|"
            r"这是我(?:问你的|问的)?第几个问题|"
            r"这是第几轮(?:对话|聊天)?|"
            r"算上(?:这句|这一句|这个问题)(?:一共|总共)?(?:问了)?"
            r"(?:多少(?:个问题|次)?|几个问题|几次)|"
            r"我们(?:已经|一共|总共)?(?:聊了|对话了)(?:多少|几)(?:轮|次)"
            r")" + _END,
            re.IGNORECASE,
        ),
    ),
    (
        SessionMetaKind.LAST_USER_MESSAGE,
        re.compile(
            r"^(?:我刚才问了什么|我上一个问题是什么|我的上一条问题是什么|"
            r"上一问是什么|你记得我上一个问题吗|还记得我刚才问的什么吗|复述一下我刚才的问题|"
            r"重复一下我刚才的问题)" + _END,
            re.IGNORECASE,
        ),
    ),
    (
        SessionMetaKind.LAST_ASSISTANT_MESSAGE,
        re.compile(
            r"^(?:你刚才回答了什么|你上一个回答是什么|你的上一条回答是什么|"
            r"上一次你怎么回答的|"
            r"复述一下你刚才的回答|重复一下你刚才的回答)" + _END,
            re.IGNORECASE,
        ),
    ),
    (
        SessionMetaKind.CONVERSATION_SUMMARY,
        re.compile(
            r"^(?:总结一下我们刚才聊了什么|总结下刚才的对话|我们刚才聊了什么|"
            r"回顾一下(?:这段|当前)?对话|总结一下(?:这段|当前)?对话)" + _END,
            re.IGNORECASE,
        ),
    ),
    (
        SessionMetaKind.ACTIVE_SUBJECT,
        re.compile(
            r"^(?:你还记得我们在聊哪场(?:比赛)?吗|当前选中的是哪场(?:比赛)?|"
            r"当前选中的比赛是什么|(?:当前|现在)(?:正在聊|在聊)哪场(?:比赛)?|我们现在聊的是谁|"
            r"当前(?:正在聊|在聊)的是谁|我们现在聊的是什么)" + _END,
            re.IGNORECASE,
        ),
    ),
    (
        SessionMetaKind.INTELLIGENCE_MODE,
        re.compile(
            r"^(?:我(?:现在)?开启全智能(?:模式)?了吗|现在是全智能模式吗|"
            r"(?:当前|现在)是什么模式|我现在用的是什么模式|这轮是什么模式)" + _END,
            re.IGNORECASE,
        ),
    ),
)


def classify_session_meta_question(message: str) -> SessionMetaQuery | None:
    """Classify only narrow, non-factual questions about application state."""

    text = " ".join(str(message or "").strip().split())
    for kind, pattern in _PATTERNS:
        if pattern.fullmatch(text):
            return SessionMetaQuery(kind=kind)
    return None


def _escape_inline_markdown(value: str) -> str:
    text = " ".join(str(value or "").split())
    text = text.replace("\\", "\\\\")
    return re.sub(r"([`*_{}\[\]()<>#+.!|~-])", r"\\\1", text)


def _last_summary(context: ConversationContext):
    summaries = list(context.recent_turn_summaries or [])
    return summaries[-1] if summaries else None


def render_session_meta_answer(
    query: SessionMetaQuery,
    context: ConversationContext,
    *,
    requested_full: bool,
    effective_full: bool,
) -> str:
    """Render a bounded answer from server-owned session state."""

    count = context.completed_user_turn_count
    if query.kind is SessionMetaKind.TURN_COUNT:
        return (
            f"在当前会话里，您此前问了 **{count}** 个问题；"
            f"算上这句，这是第 **{count + 1}** 个问题。"
        )

    last = _last_summary(context)
    if query.kind is SessionMetaKind.LAST_USER_MESSAGE:
        if last is None or not last.user_message:
            return "这是当前会话的第一条问题，之前没有可复述的问题记录。"
        return f"您上一条问题是：“{_escape_inline_markdown(last.user_message)}”"

    if query.kind is SessionMetaKind.LAST_ASSISTANT_MESSAGE:
        if last is None:
            return "当前会话还没有上一条回答。"
        return f"我上一条回答是：“{_escape_inline_markdown(last.text_summary)}”"

    if query.kind is SessionMetaKind.CONVERSATION_SUMMARY:
        recorded = [
            item.user_message
            for item in context.recent_turn_summaries
            if item.user_message
        ][-5:]
        if not recorded:
            return "当前会话还没有可总结的历史问题。"
        lines = [
            f"当前会话此前已记录 **{count}** 个问题。最近 {len(recorded)} 个是："
        ]
        lines.extend(
            f"{index}. {_escape_inline_markdown(message)}"
            for index, message in enumerate(recorded, start=1)
        )
        lines.append("这是对话记录摘要；其中涉及 NBA 的事实仍会在新问题中重新核验。")
        return "\n\n".join(lines)

    if query.kind is SessionMetaKind.ACTIVE_SUBJECT:
        active = [
            item.display_name
            for item in (context.active_game, context.active_player, context.active_team)
            if item is not None
        ]
        if not active:
            return "当前会话还没有确定具体比赛、球员或球队。您可以先选择一场比赛或直接提问。"
        return "当前会话正在围绕 **" + "、".join(active[:3]) + "** 交流。"

    if effective_full:
        return "当前这轮使用 **全智能模式**。"
    if requested_full:
        return "您请求了全智能模式，但服务当前未启用；本轮使用 **混合模式**。"
    return "当前这轮使用 **混合模式**。"


__all__ = [
    "SessionMetaKind",
    "SessionMetaQuery",
    "classify_session_meta_question",
    "render_session_meta_answer",
]
