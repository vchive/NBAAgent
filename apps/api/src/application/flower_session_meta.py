"""Deterministic session-state answers for the flower assistant.

Session metadata is an application concern, not a plant fact and not a model
task.  Keeping it in a small, domain-neutral helper prevents questions such as
``我问了几个问题`` or ``我第三个问题问的什么`` from falling through to the
flower parser and producing an irrelevant request for a plant name.

The renderer accepts the current :class:`GardenContext` by duck typing.  This
is deliberate: deployments that have not yet migrated their context model can
still use the helper, while newer contexts may expose an accurate
``completed_user_turn_count`` or an optional ``recent_answers`` list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class FlowerSessionMetaKind(StrEnum):
    """Kinds of questions answered exclusively from application session state."""

    TURN_COUNT = "TURN_COUNT"
    LAST_USER_MESSAGE = "LAST_USER_MESSAGE"
    INDEXED_USER_MESSAGE = "INDEXED_USER_MESSAGE"
    LAST_ASSISTANT_MESSAGE = "LAST_ASSISTANT_MESSAGE"
    CONVERSATION_SUMMARY = "CONVERSATION_SUMMARY"
    ACTIVE_SUBJECT = "ACTIVE_SUBJECT"
    INTELLIGENCE_MODE = "INTELLIGENCE_MODE"
    IDENTITY = "IDENTITY"


@dataclass(frozen=True, slots=True)
class FlowerSessionMetaQuery:
    kind: FlowerSessionMetaKind
    turn_index: int | None = None


_END = r"(?:[!！,.，。?？\s]*)$"
_INDEXED_RE = re.compile(
    r"^(?:我)?(?:刚才|刚刚|之前)?(?:的)?第"
    r"(?P<index>\d{1,4}|[零〇一二两三四五六七八九十百千]+)个"
    r"(?:问题|提问)(?:(?:是|问的是|问的|问了)?(?:啥|什么)(?:内容)?|呢)"
    + _END,
    re.IGNORECASE,
)


def _patterns() -> tuple[tuple[FlowerSessionMetaKind, re.Pattern[str]], ...]:
    # Constructing these once at import time keeps matching deterministic and
    # makes the full-match boundary explicit.  Narrow phrases are intentional:
    # a factual question containing “刚才” must remain a normal care query.
    return (
        (
            FlowerSessionMetaKind.TURN_COUNT,
            re.compile(
                r"^(?:"
                r"我(?:已经|刚才|之前|一共|总共)?(?:问了你|问了|问过你|问过)"
                r"(?:多少(?:个问题|次)?|几个问题|几次)|"
                r"这是我(?:问你的|问的)?第几个问题|"
                r"这是第几轮(?:对话|聊天)?|"
                r"算上(?:这句|这一句|这个问题)(?:一共|总共)?(?:问了)?"
                r"(?:多少(?:个问题|次)?|几个问题|几次)|"
                r"我们(?:已经|一共|总共)?(?:聊了|对话了)(?:多少|几)(?:轮|次)|"
                r"(?:到现在|截至现在|目前|前面|之前)(?:我)?(?:一共|总共)?"
                r"(?:问了你|问了|问过你|问过)?(?:多少(?:个问题|次)?|几个问题|几次)"
                r")" + _END,
                re.IGNORECASE,
            ),
        ),
        (
            FlowerSessionMetaKind.LAST_USER_MESSAGE,
            re.compile(
                r"^(?:我刚才问了什么|我上一个问题是什么|我的上一条问题是什么|"
                r"上一问是什么|你记得我上一个问题吗|还记得我刚才问的什么吗|"
                r"复述一下我刚才的问题|重复一下我刚才的问题)" + _END,
                re.IGNORECASE,
            ),
        ),
        (
            FlowerSessionMetaKind.LAST_ASSISTANT_MESSAGE,
            re.compile(
                r"^(?:你刚才回答了什么|你上一个回答是什么|你的上一条回答是什么|"
                r"上一次你怎么回答的|复述一下你刚才的回答|重复一下你刚才的回答)"
                + _END,
                re.IGNORECASE,
            ),
        ),
        (
            FlowerSessionMetaKind.CONVERSATION_SUMMARY,
            re.compile(
                r"^(?:总结一下我们刚才聊了什么|总结一下刚才的对话|总结下刚才的对话|我们刚才聊了什么|"
                r"回顾一下(?:这段|当前)?对话|总结一下(?:这段|当前)?对话)" + _END,
                re.IGNORECASE,
            ),
        ),
        (
            FlowerSessionMetaKind.ACTIVE_SUBJECT,
            re.compile(
                r"^(?:当前(?:选中|正在养|在养)的(?:花|植物)?是什么|"
                r"我们现在在聊什么植物|我们现在养的是什么|当前植物是什么|"
                r"你还记得我在养什么吗|刚才说的是什么花)" + _END,
                re.IGNORECASE,
            ),
        ),
        (
            FlowerSessionMetaKind.INTELLIGENCE_MODE,
            re.compile(
                r"^(?:我(?:现在)?开启全智能(?:模式)?了吗|现在是全智能模式吗|"
                r"(?:当前|现在)是什么模式|我现在用的是什么模式|这轮是什么模式)" + _END,
                re.IGNORECASE,
            ),
        ),
    )


_PATTERNS = _patterns()
_IDENTITY_RE = re.compile(
    r"^(?:你是谁(?:呀|啊|呢)?|你叫什么(?:名字)?|你是什么(?:助手|agent|ai)|"
    r"你是做什么的|你能做什么|你会什么|你可以做什么|介绍一下你自己|"
    r"who\s+are\s+you|what\s+can\s+you\s+do)[？?!！。\s]*$",
    re.IGNORECASE,
)


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _positive_index(value: str) -> int | None:
    """Parse a bounded Arabic/Chinese ordinal without guessing malformed text."""

    if value.isdigit():
        parsed = int(value)
        return parsed if 1 <= parsed <= 9999 else None
    total = 0
    section = 0
    number = 0
    units = {"十": 10, "百": 100, "千": 1000}
    for token in value:
        if token in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[token]
            continue
        unit = units.get(token)
        if unit is None:
            return None
        if number == 0:
            number = 1
        section += number * unit
        number = 0
    total = section + number
    return total if 1 <= total <= 9999 else None


def classify_flower_session_meta_question(message: str) -> FlowerSessionMetaQuery | None:
    """Classify narrow session-state questions only.

    Full-string matching is important.  For example, “刚才那盆花为什么黄叶”
    contains a recency word but is a real plant question and must not be
    diverted to this route.
    """

    text = " ".join(str(message or "").strip().split())
    if _IDENTITY_RE.fullmatch(text):
        return FlowerSessionMetaQuery(FlowerSessionMetaKind.IDENTITY)
    indexed = _INDEXED_RE.fullmatch(text)
    if indexed is not None:
        turn_index = _positive_index(indexed.group("index"))
        if turn_index is not None:
            return FlowerSessionMetaQuery(
                FlowerSessionMetaKind.INDEXED_USER_MESSAGE,
                turn_index=turn_index,
            )
    for kind, pattern in _PATTERNS:
        if pattern.fullmatch(text):
            return FlowerSessionMetaQuery(kind)
    return None


def _safe_text(value: Any, *, limit: int = 500) -> str:
    """Bound history text and remove obvious credentials/precise addresses."""

    text = " ".join(str(value or "").replace("\u200b", "").split())
    # User history is private session data, but it should still not echo a
    # phone number or street-level address back into a model/public response.
    text = re.sub(r"(?<!\d)(?:1[3-9]\d{9}|0\d{2,3}[- ]?\d{7,8})(?!\d)", "[已隐藏联系方式]", text)
    text = re.sub(
        r"(?:[一-鿿]{2,12}(?:省|市|自治区|区|县|镇|街道))?"
        r"[一-鿿A-Za-z0-9]{1,20}(?:路|街|大道|弄|巷)\s*\d{1,6}\s*号",
        "[已隐藏详细地址]",
        text,
    )
    return text[:limit]


def _escape_inline(value: Any) -> str:
    text = _safe_text(value)
    text = text.replace("\\", "\\\\")
    return re.sub(r"([`*_{}\[\]()<>#+.!|~-])", r"\\\1", text)


def _history_questions(context: Any, current_question: str | None) -> list[str]:
    raw = getattr(context, "recent_questions", None)
    if not isinstance(raw, (list, tuple)):
        raw = []
    questions = [_safe_text(item) for item in raw if _safe_text(item)]
    # FlowerContext currently records the current question while constructing
    # its immutable update.  Remove exactly one trailing copy so “上一问” never
    # echoes the metadata question itself.  A caller using the pre-turn context
    # simply sees no match and remains unchanged.
    current = _safe_text(current_question)
    if current and questions and questions[-1] == current:
        questions.pop()
    return questions[-8:]


def _history_answers(context: Any) -> list[str]:
    raw = getattr(context, "recent_answers", None)
    if not isinstance(raw, (list, tuple)):
        return []
    return [_safe_text(item, limit=1200) for item in raw if _safe_text(item)]


def _completed_count(context: Any, retained_questions: list[str]) -> int:
    explicit = getattr(context, "completed_user_turn_count", None)
    if isinstance(explicit, int) and not isinstance(explicit, bool) and explicit >= 0:
        return explicit
    # Legacy GardenContext only has a bounded/rolling turn_count.  It is still
    # a useful lower bound for old sessions; never claim more than its value.
    rolling = getattr(context, "turn_count", 0)
    return max(len(retained_questions), int(rolling) if isinstance(rolling, int) else 0)


def _plant_label(context: Any) -> str | None:
    value = getattr(context, "plant_name", None)
    if value:
        return _safe_text(value, limit=100)
    plant = getattr(context, "plant", None)
    if isinstance(plant, str) and plant.strip():
        return _safe_text(plant, limit=100)
    if plant is not None:
        name = getattr(plant, "canonical_name", None)
        if name:
            return _safe_text(name, limit=100)
    return None


def render_flower_session_meta_answer(
    query: FlowerSessionMetaQuery,
    context: Any,
    *,
    requested_full: bool = False,
    effective_full: bool = False,
    current_question: str | None = None,
    recent_answers: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Render a provider/model-free answer from one session context snapshot."""

    questions = _history_questions(context, current_question)
    count = _completed_count(context, questions)
    kind = query.kind

    if kind is FlowerSessionMetaKind.IDENTITY:
        return (
            "我是**种花 Agent**，可以帮您选花、安排浇水和施肥，"
            "处理光照、换盆、修剪以及常见病虫害排查。"
        )
    if kind is FlowerSessionMetaKind.TURN_COUNT:
        return (
            f"在当前会话里，您此前问了 **{count}** 个问题；"
            f"算上这句，这是第 **{count + 1}** 个问题。"
        )
    if kind is FlowerSessionMetaKind.LAST_USER_MESSAGE:
        if not questions:
            return "这是当前会话的第一条问题，之前没有可复述的问题记录。"
        return f"您上一条问题是：“{_escape_inline(questions[-1])}”"
    if kind is FlowerSessionMetaKind.INDEXED_USER_MESSAGE:
        requested = query.turn_index or 0
        if requested > count:
            return (
                f"当前会话此前只有 **{count}** 个已记录问题，"
                f"还没有第 **{requested}** 个问题。"
            )
        # The absolute index of the first retained question follows the
        # monotonic count.  If a legacy context has only a rolling count, this
        # still gives the best truthful answer for retained entries.
        first_index = max(1, count - len(questions) + 1)
        offset = requested - first_index
        if 0 <= offset < len(questions):
            return f"您第 **{requested}** 个问题是：“{_escape_inline(questions[offset])}”"
        kept = len(questions)
        return (
            f"第 **{requested}** 个问题已超出当前保留的最近 **{kept}** 条对话记录，"
            "因此现在无法准确复述。"
        )
    if kind is FlowerSessionMetaKind.LAST_ASSISTANT_MESSAGE:
        answers = (
            [_safe_text(item, limit=1200) for item in recent_answers if _safe_text(item)]
            if recent_answers is not None
            else _history_answers(context)
        )
        if answers:
            return f"我上一条回答是：“{_escape_inline(answers[-1])}”"
        return "当前会话没有可复述的上一条回答记录。"
    if kind is FlowerSessionMetaKind.CONVERSATION_SUMMARY:
        if not questions:
            return "当前会话还没有可总结的历史问题。"
        recent = questions[-5:]
        lines = [f"当前会话此前已记录 **{count}** 个问题。最近 {len(recent)} 个是："]
        lines.extend(
            f"{index}. {_escape_inline(message)}"
            for index, message in enumerate(recent, start=1)
        )
        return "\n\n".join(lines)
    if kind is FlowerSessionMetaKind.ACTIVE_SUBJECT:
        plant = _plant_label(context)
        if not plant:
            return "当前会话还没有确定具体花卉。您可以先告诉我植物名称或描述光照和空间。"
        extras: list[str] = []
        for attr, label in (("location", "地点"), ("season", "季节")):
            value = getattr(context, attr, None)
            if value:
                extras.append(f"{label}：{_safe_text(value, limit=80)}")
        suffix = f"（{'；'.join(extras)}）" if extras else ""
        return f"当前会话正在围绕 **{_escape_inline(plant)}** 交流{suffix}。"
    if effective_full:
        return "当前这轮使用 **全智能模式**。"
    if requested_full:
        return "您请求了全智能模式，但服务当前未启用；本轮使用 **普通模式**。"
    return "当前这轮使用 **普通模式**。"


__all__ = [
    "FlowerSessionMetaKind",
    "FlowerSessionMetaQuery",
    "classify_flower_session_meta_question",
    "render_flower_session_meta_answer",
]
