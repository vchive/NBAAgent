"""Safety and output-boundary policies.

The safety boundary is deliberately local and deterministic.  It runs before a query is
parsed or handed to a provider, so a red-line request can never cause a cache/provider
lookup.  ``OutputGuard`` is the second boundary: it validates the renderer (including a
future Hermes renderer) and rejects leaked implementation details or numbers which cannot
be traced to verified facts.

This module only depends on canonical domain models.  It does not import FastAPI, a
provider, or an LLM runtime; keeping that direction one-way makes the pre-retrieval
invariant easy to test.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import (
    AnswerBlock,
    AnswerBlockType,
    DraftAnswer,
    EvidenceState,
    FactBundle,
    SafetyCategory,
    SafetyDecision,
    SafetyOutcome,
)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _normalise_text(text: str) -> str:
    """Normalise text for matching without changing the user-visible message.

    NFKC folds full-width Latin characters and a lower-case pass makes the English aliases
    deterministic.  Newlines are retained as whitespace; they are harmless for matching.
    """

    return unicodedata.normalize("NFKC", text).casefold()


@dataclass(frozen=True, slots=True)
class _Rule:
    category: SafetyCategory
    template_id: str
    patterns: tuple[re.Pattern[str], ...]
    confidence: Decimal = Decimal("0.99")


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


_GAMBLING_MENTION_RE = re.compile(
    r"(?:博彩|赌博|赌球|下注|投注|盘口|赔率|串关|让分盘|让分|大小分|大小球|买球|菠菜|赌盘)"
    r"|\b(?:bet(?:ting)?|gambl(?:e|ing)|odds|spread|parlay|wager)\b",
    re.IGNORECASE,
)
_GAMBLING_NEGATION_RE = re.compile(
    # Keep the negation attached to the gambling token.  This covers both a
    # short disclaimer (``不下注``) and the common explanatory form
    # ``不参与博彩`` without allowing a later odds/handicap request to slip
    # through; every separate gambling mention is checked independently below.
    r"(?:"
    r"不想(?:参与|参加|进行|支持|需要)?"
    r"|不打算(?:参与|参加|进行|支持|需要)?"
    r"|没打算(?:参与|参加|进行|支持|需要)?"
    r"|无意(?:参与|参加|进行|支持|需要)?"
    r"|拒绝(?:参与|参加|进行|支持|需要)?"
    r"|不参与|不参加|不涉及|不进行|不支持|不需要"
    r"|不用|不会|不要|不|没|无|非"
    r")\s*$",
    re.IGNORECASE,
)
_GAMBLING_EN_NEGATION_RE = re.compile(
    r"(?:not|no|without)\s+(?:participat(?:e|ing)\s+in\s+)?$",
    re.IGNORECASE,
)


def _all_gambling_mentions_negated(text: str) -> bool:
    """Allow a clearly negated betting disclaimer in an otherwise normal query.

    ``不下注，只想知道谁赢`` should remain an ordinary game question, while
    ``不下注，赔率是多少`` must still be blocked because the odds request is
    substantive.  Treat every gambling mention as a token and require a
    nearby negation for all of them; this deliberately errs on the side of a
    block for mixed/ambiguous wording.
    """

    mentions = list(_GAMBLING_MENTION_RE.finditer(text))
    if not mentions:
        return False
    for match in mentions:
        # Keep enough context for natural forms such as “我不想参与博彩”，
        # while treating a previous sentence's disclaimer as unrelated.
        before = text[max(0, match.start() - 48) : match.start()]
        before = re.split(r"[。！？!?；;]", before)[-1]
        after = text[match.end() : match.end() + 4]
        if not (
            _GAMBLING_NEGATION_RE.search(before) or _GAMBLING_EN_NEGATION_RE.search(before)
        ) and not re.match(r"\s*(?:无关|以外)", after, re.IGNORECASE):
            return False
    return True


# Patterns intentionally describe the category, not a response to repeat the matched text.
# Keep the list conservative around ordinary basketball terms (for example, "垃圾时间" is
# not an insult).  Rules are ordered from the most specific/high-risk categories to broader
# categories; a message containing multiple red lines is blocked as a whole.
_DEFAULT_RULES: tuple[_Rule, ...] = (
    _Rule(
        SafetyCategory.GAMBLING,
        "gambling",
        _compile(
            r"(?:博彩|赌博|赌球|下注|投注|盘口|赔率|串关|让分盘|让分|大小分|大小球|买球|菠菜|赌盘)",
            r"\b(?:bet(?:ting)?|gambl(?:e|ing)|odds|spread|parlay|wager)\b",
        ),
    ),
    _Rule(
        SafetyCategory.FIXED_GAME_CONSPIRACY,
        "fixed_game_conspiracy",
        _compile(
            r"(?:假球|假赛|黑哨|吹黑哨|操纵比赛|操控比赛|裁判收钱|裁判被买|裁判偏袒|裁判不公|打假球|比赛被操纵|比赛剧本|比赛有剧本|内定冠军|内定比赛|操盘比赛|内定|剧本)",
            r"\b(?:match[- ]?fix(?:ing|ed)?|rigged|ref(?:eree)?\s*brib(?:e|ery))\b",
        ),
    ),
    _Rule(
        SafetyCategory.LEGAL_CRIME,
        "legal_crime",
        _compile(
            r"(?:犯罪|犯法|违法|被捕|逮捕|拘捕|起诉|诉讼|法院|判刑|坐牢|涉案|刑事|司法纠纷|法律指控|法律纠纷|性侵|强奸|家暴|吸毒|洗钱|逃税|贿赂)",
            r"\b(?:arrest(?:ed)?|crime|criminal|lawsuit|court|convict(?:ed)?|rape|assault)\b",
        ),
    ),
    _Rule(
        SafetyCategory.ABUSE_HATE,
        "abuse_hate",
        _compile(
            r"(?:仇恨|歧视|种族主义|种族歧视|性别歧视|民族歧视|歧视性|人身攻击|去死|傻逼|煞笔|操你|妈的|滚开)",
            # A small set of unambiguous English slurs/profanities.  Deliberately do not
            # include benign words such as "damn" which occur in quoted sports copy.
            r"\b(?:fuck(?:ing)?|shit|bitch|nigger|faggot|kill\s+(?:him|her|them))\b",
        ),
    ),
    _Rule(
        SafetyCategory.OFF_COURT_PRIVACY,
        "off_court_privacy",
        _compile(
            r"(?:私生活|隐私|绯闻|恋情|出轨|老婆|妻子|女友|男友|家人|住址|电话|私人号码|私照|裸照|收入以外的隐私)",
            r"\b(?:private life|privacy|girlfriend|boyfriend|wife|home address|phone number|dox)\b",
        ),
    ),
    _Rule(
        SafetyCategory.GEO_SENSITIVE,
        "geo_sensitive",
        _compile(
            r"(?:涉华|涉政|台独|藏独|疆独|分裂|港独|新疆问题|西藏问题|台湾政治|香港政治|中美关系|地缘政治)",
            r"\b(?:taiwan independence|hong kong politics|xinjiang|tibet(?:an)? politics)\b",
        ),
    ),
    _Rule(
        SafetyCategory.POLITICS,
        "politics",
        _compile(
            r"(?:政治|政治立场|政治观点|选举|总统|政党|政府政策|外交|战争|政治争议|政治问题)",
            r"\b(?:politics?|election|president|political party|government policy|war)\b",
        ),
    ),
    _Rule(
        SafetyCategory.SOCIAL_CONFLICT,
        "social_conflict",
        _compile(
            r"(?:社会争议|社会冲突|民族冲突|抗议活动|群体冲突|公共争议|社会事件|引战话术)",
            r"\b(?:social conflict|social controversy|protest movement|civil unrest)\b",
        ),
    ),
    _Rule(
        SafetyCategory.INSULT_NICKNAME,
        "insult_nickname",
        _compile(
            r"(?:侮辱性绰号|侮辱性昵称|羞辱性绰号|羞辱性昵称|难听外号|难听昵称|恶毒外号|恶毒昵称|辱骂|辱骂球员|给.+(?:起|取).+(?:侮辱|难听|恶毒).*(?:外号|绰号|昵称)|(?:请|去)?(?:骂|辱骂|侮辱)(?:詹姆斯|库里|杜兰特|球员|他|她))",
            r"\b(?:insult(?:ing)?\s*nickname|offensive\s*nickname|derogatory\s*name)\b",
            # Targeted insults, while avoiding the normal phrase "垃圾时间".
            r"(?:垃圾|废物|蠢货|软蛋|毒瘤)(?:球员|教练|队员|詹姆斯|库里|杜兰特|他|她)",
        ),
    ),
    _Rule(
        SafetyCategory.RUMOR,
        "rumor",
        _compile(
            r"(?:绯闻|私生活内幕|八卦|爆料(?:称|说)?|传闻(?:称|说)?|听说).*(?:球员|教练|球队|nba|湖人|勇士|凯尔特人)?",
            r"\b(?:rumou?r|gossip|allegedly|unverified\s+claim)\b",
        ),
    ),
)


# Explicit non-NBA topics are enough for the scope gate.  We intentionally do not classify
# every unknown sentence as out-of-scope: a short query such as "总决赛呢" may need an entity
# clarification rather than an unrelated redirect.
_OUT_OF_SCOPE_PATTERNS = _compile(
    r"(?:天气|气温|股票|股价|汇率|房价|写代码|编程|python|javascript|食谱|做饭|旅游|机票|酒店|医疗|诊断)",
    r"(?:足球|英超|西甲|欧冠|f1|网球|电竞|英雄联盟|lol|电影|电视剧|小说|星座)",
    r"\b(?:weather|stocks?|exchange rate|program(?:ming)?|python|recipe|travel|"
    r"soccer|football|tennis|movie)\b",
)


_NBA_HINTS = _compile(
    r"(?:nba|篮球|比赛|球员|球队|赛季|赛程|赛果|总决赛|季后赛|常规赛|得分|篮板|助攻|投篮|防守|冠军|湖人|勇士|凯尔特人|掘金|太阳|独行侠|骑士|尼克斯|热火|雷霆|快船|雄鹿|76人|火箭|灰熊|马刺|国王|森林狼|鹈鹕|老鹰|篮网|猛龙|公牛|活塞|步行者|黄蜂|奇才|爵士|开拓者|魔术)",
    r"\b(?:nba|basketball|boxscore|play[- ]?by[- ]?play|points?|rebounds?|assists?)\b",
)


class SafetyGuard:
    """Deterministic, pre-retrieval safety classifier.

    ``classify`` has no side effects and performs no network/cache access.  This makes it
    safe to call at the very beginning of both the synchronous and SSE use cases.
    """

    def __init__(self, rules: Iterable[_Rule] | None = None) -> None:
        self._rules = tuple(rules) if rules is not None else _DEFAULT_RULES

    def classify(self, text: str) -> SafetyDecision:
        if not isinstance(text, str):
            raise TypeError("safety text must be a string")
        if not text.strip():
            raise ValueError("safety text must not be blank")
        if _CONTROL_RE.search(text):
            # Control characters are a transport/schema concern, not a reason to retrieve
            # anything.  Treating them as out-of-scope is the safest deterministic branch.
            return SafetyDecision(
                outcome=SafetyOutcome.OUT_OF_SCOPE,
                category=SafetyCategory.OUT_OF_SCOPE,
                confidence=Decimal("0.95"),
                refusal_template_id="out_of_scope",
            )

        normalized = _normalise_text(text)
        for rule in self._rules:
            if rule.category is SafetyCategory.GAMBLING and _all_gambling_mentions_negated(
                normalized
            ):
                continue
            if any(pattern.search(normalized) for pattern in rule.patterns):
                return SafetyDecision(
                    outcome=SafetyOutcome.BLOCK,
                    category=rule.category,
                    confidence=rule.confidence,
                    refusal_template_id=rule.template_id,
                )

        # A clear non-NBA request is redirected.  If it also has a red-line term, the rule
        # pass above wins and the complete message remains blocked.
        if any(pattern.search(normalized) for pattern in _OUT_OF_SCOPE_PATTERNS):
            return SafetyDecision(
                outcome=SafetyOutcome.OUT_OF_SCOPE,
                category=SafetyCategory.OUT_OF_SCOPE,
                confidence=Decimal("0.98"),
                refusal_template_id="out_of_scope",
            )

        return SafetyDecision(
            outcome=SafetyOutcome.ALLOW,
            category=SafetyCategory.ALLOW,
            confidence=Decimal("0.99"),
            refusal_template_id=None,
        )

    # Explicit aliases make the port pleasant to use and preserve compatibility with early
    # prototypes which called the operation ``evaluate`` or ``decision_for``.
    evaluate = classify
    decision_for = classify

    @staticmethod
    def refusal_text(decision: SafetyDecision) -> str:
        """Return a fixed, localised 1–2 sentence response for a decision."""

        if decision.outcome is SafetyOutcome.BLOCK:
            return "这个话题不属于我能讨论的范围。您可以问我比赛、球员或球队数据。"
        if decision.outcome is SafetyOutcome.OUT_OF_SCOPE:
            return "我专注于 NBA 赛事信息，暂时无法处理这个问题。您可以问我比赛、球员或球队数据。"
        return ""

    # Common spelling used by renderers/tests.
    render_refusal = refusal_text
    refusal = refusal_text
    redirect = refusal_text
    render = refusal_text

    @classmethod
    def as_draft(cls, decision: SafetyDecision) -> DraftAnswer:
        """Build a safe draft for a BLOCK/OUT_OF_SCOPE decision.

        The result contains no evidence or factual numbers.  Calling this for ALLOW is an
        error because an allowed query must continue through parsing and retrieval.
        """

        if decision.outcome is SafetyOutcome.ALLOW:
            raise ValueError("an ALLOW decision has no refusal draft")
        text = cls.refusal_text(decision)
        return DraftAnswer(
            markdown=text,
            blocks=[AnswerBlock(type=AnswerBlockType.WARNING, content=text)],
            evidence_state=EvidenceState.NONE,
            corrections=[],
            follow_up=None,
        )

    refusal_draft = as_draft
    build_draft = as_draft


class OutputGuardError(ValueError):
    """Raised when a renderer draft cannot safely cross the public API boundary."""

    code = "OUTPUT_BLOCKED"

    def __init__(self, message: str, *, reasons: Iterable[str] = ()) -> None:
        self.reasons = tuple(reasons)
        super().__init__(message)


class OutputGuard:
    """Validate and sanitise user-visible drafts after composition.

    Numeric traceability is intentionally conservative: when a fact bundle is supplied,
    every non-date numeric token must match a value in that bundle.  This catches a model
    inventing a score while still allowing ordinary list numbering and Beijing freshness
    timestamps.  A renderer may pass ``facts=None`` only for a no-data/refusal draft.
    """

    _LEAK_PATTERNS = _compile(
        r"https?://|www\.",
        r"(?:source_ref|source_id|evidence_id|evidence_ids|canonical_id|canonical_ids|fact_id|fact_ids|"
        r"provider_url|provider_call|raw_json|raw_payload|raw_response|opaque_session_id|trace_id|"
        r"request_id|session_id|verified_facts|contract_version|used_fact_ids|"
        r"finish_reason|error_code|siliconflow|deepseek)",
        r"(?:\bespn\b|nba[_ -]?api|sportsradar|basketball[- ]reference)",
        r"(?:api[_ -]?key|authorization|bearer\s+[a-z0-9._-]+|sk-[a-z0-9]{8,})",
        r"(?:system\s+prompt|developer\s+message|ignore\s+(?:all\s+)?previous\s+instructions|tool\s*call|filesystem|shell\s+command)",
    )
    _HTML_LEAK_PATTERNS = _compile(
        r"<\s*/?\s*(?:script|iframe|object|embed|style|form|img|svg)\b",
        r"(?:javascript|data:text/html)\s*:",
    )
    # Use ASCII boundaries rather than ``\w``: Python considers CJK characters word
    # characters, but a normal Chinese sentence often places a factual number directly
    # next to ``得分``/``篮板``.  Those numbers must still be checked for traceability.
    _NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?(?![A-Za-z0-9_])")
    _DATE_SPAN_RE = re.compile(r"(?<!\w)(?:19\d{2}|20\d{2}|21\d{2})[-/.]\d{1,2}[-/.]\d{1,2}(?!\w)")
    _SEASON_SPAN_RE = re.compile(r"(?<![A-Za-z0-9_])(?:19\d{2}|20\d{2}|21\d{2})-\d{2}")
    _CLOCK_SPAN_RE = re.compile(r"(?<![A-Za-z0-9_])\d{1,2}:\d{2}(?![A-Za-z0-9_])")
    _DATE_TOKEN_RE = re.compile(
        r"^(?:19\d{2}|20\d{2}|21\d{2})(?:[-/.](?:0?[1-9]|1[0-2])(?:[-/.](?:0?[1-9]|[12]\d|3[01]))?)?$"
    )

    @staticmethod
    def _coerce_draft(answer: DraftAnswer | Mapping[str, Any] | str) -> DraftAnswer:
        if isinstance(answer, DraftAnswer):
            return answer.model_copy(deep=True)
        if isinstance(answer, str):
            if not answer.strip():
                raise OutputGuardError("答案不能为空", reasons=("empty",))
            return DraftAnswer(markdown=answer.strip(), evidence_state=EvidenceState.NONE)
        if isinstance(answer, Mapping):
            # Accept both canonical uppercase enum values and public lowercase values.  The
            # domain model is the source of truth for shape validation.
            payload = dict(answer)
            state = payload.get("evidence_state", EvidenceState.NONE)
            if isinstance(state, str):
                payload["evidence_state"] = state.upper()
            blocks = []
            for block in payload.get("blocks", []) or []:
                if isinstance(block, Mapping):
                    item = dict(block)
                    if isinstance(item.get("type"), str):
                        item["type"] = item["type"].upper()
                    blocks.append(item)
                else:
                    blocks.append(block)
            payload["blocks"] = blocks
            corrections = []
            for correction in payload.get("corrections", []) or []:
                if isinstance(correction, Mapping):
                    item = dict(correction)
                    if isinstance(item.get("status"), str):
                        item["status"] = item["status"].upper()
                    corrections.append(item)
                else:
                    corrections.append(correction)
            payload["corrections"] = corrections
            try:
                return DraftAnswer.model_validate(payload)
            except Exception as exc:  # Pydantic's ValidationError is intentionally hidden.
                raise OutputGuardError("答案结构不符合公开格式", reasons=("schema",)) from exc
        raise TypeError("answer must be DraftAnswer, mapping, or string")

    @staticmethod
    def _walk_text(value: Any) -> Iterable[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            for key, item in value.items():
                if isinstance(key, str):
                    yield key
                yield from OutputGuard._walk_text(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from OutputGuard._walk_text(item)
        elif value is not None and not isinstance(value, (bool, bytes)):
            yield str(value)

    @classmethod
    def _numeric_values(cls, facts: Any) -> set[str]:
        values: set[str] = set()
        if facts is None:
            return values
        source = facts.facts if isinstance(facts, FactBundle) else getattr(facts, "facts", facts)

        def add_number(item: Any, *, unit: str | None = None) -> None:
            if isinstance(item, bool) or not isinstance(item, (int, float, Decimal)):
                return
            try:
                number = Decimal(str(item).replace(",", ""))
            except InvalidOperation:
                return
            if not number.is_finite():
                return
            values.add(cls._normalise_number(str(item)))
            # Providers commonly encode percentages as a ratio (0.42) with unit "%",
            # while renderers present the human form (42%).  Both are the same traced fact.
            if unit == "%" and Decimal("-1") <= number <= Decimal("1"):
                values.add(cls._normalise_number(f"{number * 100}%"))

        def visit(item: Any) -> None:
            if item is None or isinstance(item, bool):
                return
            # Never treat an unverified/contradicted assertion as permission to expose its
            # number.  ``FactBundle`` may contain a mixture of verified and missing facts.
            verification = getattr(item, "verification", None)
            if verification is not None:
                state = getattr(verification, "value", verification)
                if str(state).upper() not in {"VERIFIED", "PARTIAL"}:
                    return
                # A FactAssertion's subject confidence, IDs, and timestamps are metadata,
                # not factual values.  Only its explicit ``value`` can trace a number.
                if hasattr(item, "value"):
                    value = getattr(item, "value")
                    unit = getattr(item, "unit", None)
                    if isinstance(value, (int, float, Decimal)):
                        add_number(value, unit=unit)
                    else:
                        visit(value)
                    return
            if isinstance(item, (int, float, Decimal)):
                add_number(item)
                return
            if isinstance(item, str):
                # Values such as "118-110" or "42.5%" commonly arrive as strings.
                for match in cls._NUMBER_RE.finditer(item):
                    values.add(cls._normalise_number(match.group(0)))
                return
            if isinstance(item, Mapping):
                if "verification" in item and "value" in item:
                    state = item.get("verification")
                    state = getattr(state, "value", state)
                    if str(state).upper() not in {"VERIFIED", "PARTIAL"}:
                        return
                    value = item.get("value")
                    unit = item.get("unit")
                    if isinstance(value, (int, float, Decimal)):
                        add_number(value, unit=unit)
                    else:
                        visit(value)
                    return
                for value in item.values():
                    visit(value)
                return
            if isinstance(item, (list, tuple, set)):
                for value in item:
                    visit(value)
                return
            # Generic value wrappers (for example a small derived-fact DTO).
            if hasattr(item, "value"):
                visit(getattr(item, "value"))

        visit(source)
        return values

    @staticmethod
    def _normalise_number(token: str) -> str:
        raw = token.strip().replace(",", "")
        percent = raw.endswith("%")
        if percent:
            raw = raw[:-1]
        try:
            value = Decimal(raw)
        except InvalidOperation:
            return token
        if not value.is_finite():
            return token
        # Decimal's fixed-point form avoids 1.0/1 aliases while preserving meaningful
        # decimal precision.  A percent sign is part of the metric's unit.
        normal = format(value.normalize(), "f")
        if "." in normal:
            normal = normal.rstrip("0").rstrip(".")
        if normal in {"-0", "+0"}:
            normal = "0"
        return f"{normal}%" if percent else normal

    @classmethod
    def _untraceable_numbers(cls, text: str, known: set[str]) -> list[str]:
        text = unicodedata.normalize("NFKC", text)
        unknown: list[str] = []
        metadata_spans = [
            match.span()
            for pattern in (cls._DATE_SPAN_RE, cls._SEASON_SPAN_RE, cls._CLOCK_SPAN_RE)
            for match in pattern.finditer(text)
        ]
        for match in cls._NUMBER_RE.finditer(text):
            token = match.group(0)
            # Markdown list markers ("1."), ordinary sentence punctuation, and date/time
            # metadata are not factual claims.  Season labels and years are likewise safe
            # metadata; scores/statistics are not.
            before, after = text[: match.start()], text[match.end() :]
            if any(match.start() >= start and match.end() <= end for start, end in metadata_spans):
                continue
            # Ordinal period/game labels and a clock value in a PBP description are
            # structural metadata, not newly asserted box-score numbers.
            if before.endswith("第") and after[:1] in {"节", "期", "场", "轮"}:
                continue
            if after.startswith("秒"):
                continue
            if after.startswith(".") and (not after[1:] or after[1].isspace()):
                continue
            normal = cls._normalise_number(token)
            bare = normal.rstrip("%")
            if cls._DATE_TOKEN_RE.match(bare):
                continue
            # Ordered-list markers are presentation structure, not factual
            # claims.  Only accept them at the beginning of a line (or after
            # a Markdown bullet) and keep the ordinal range deliberately
            # small so a real score followed by a parenthesis is still checked.
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_prefix = text[line_start : match.start()].strip()
            if (
                not normal.endswith("%")
                and line_prefix in {"", "-", "*", "+"}
                and after[:1] in {")",
                    "）",
                    "、",
                    ".",
                }
            ):
                try:
                    if 1 <= int(bare) <= 20:
                        continue
                except ValueError:
                    pass
            # Clock/date portions in an as-of phrase (e.g. 21:30) and season ranges.
            clock_prefix = before.endswith((":", "："))
            clock_suffix = after[:1] in {"", " ", "\n"}
            if (clock_prefix and clock_suffix) or after.startswith(":") or before.endswith(":"):
                continue
            if normal not in known:
                unknown.append(token)
        return unknown

    @classmethod
    def redact_untraceable_numbers(cls, text: str, facts: Any) -> str:
        """Replace only untraceable numeric claims in model prose.

        The deterministic answer remains responsible for factual numbers. A
        model occasionally echoes a user-provided count or emits a list index
        that is not present in the verified bundle; rejecting the entire
        analysis makes the model appear disconnected. Replacing those tokens
        with ``若干`` preserves the useful qualitative reasoning while the
        subsequent guard still performs the complete safety/leakage check.
        Date/clock spans and Markdown list markers use the same exceptions as
        :meth:`_untraceable_numbers` and are therefore left untouched.
        """

        if not isinstance(text, str) or not text:
            return text
        known = cls._numeric_values(facts)
        unknown = {
            cls._normalise_number(token)
            for token in cls._untraceable_numbers(text, known)
        }
        if not unknown:
            return text

        def replace(match: re.Match[str]) -> str:
            token = match.group(0)
            return "若干" if cls._normalise_number(token) in unknown else token

        return cls._NUMBER_RE.sub(replace, text)

    @classmethod
    def validate(
        cls,
        answer: DraftAnswer | Mapping[str, Any] | str,
        facts: FactBundle | Iterable[Any] | None = None,
        *,
        allow_unverified_numbers: bool = False,
    ) -> DraftAnswer:
        draft = cls._coerce_draft(answer)
        text_parts = list(cls._walk_text(draft.model_dump(mode="python")))
        all_text = "\n".join(text_parts)
        if _CONTROL_RE.search(all_text):
            raise OutputGuardError("答案包含不可显示的控制字符", reasons=("control",))
        if any(pattern.search(all_text) for pattern in cls._LEAK_PATTERNS):
            raise OutputGuardError("答案包含内部实现信息", reasons=("leakage",))
        if any(pattern.search(all_text) for pattern in cls._HTML_LEAK_PATTERNS):
            raise OutputGuardError("答案包含不安全的标记", reasons=("unsafe_markup",))

        # A second local safety pass prevents a model/composer from drifting into a red-line
        # answer after the original question was allowed.  Refusal templates themselves do
        # not contain the matched sensitive terms and therefore pass this check.
        decision = SafetyGuard().classify(draft.markdown)
        if decision.outcome is not SafetyOutcome.ALLOW:
            raise OutputGuardError("答案未通过安全检查", reasons=("red_line",))

        # Public correction text is intentionally stricter than general markdown: callers
        # cannot smuggle canonical IDs, URLs, or raw claims through the correction channel.
        for correction in draft.corrections:
            if any(pattern.search(correction.message) for pattern in cls._LEAK_PATTERNS):
                raise OutputGuardError("纠偏说明包含内部信息", reasons=("correction_leakage",))
            if SafetyGuard().classify(correction.message).outcome is not SafetyOutcome.ALLOW:
                raise OutputGuardError("纠偏说明未通过安全检查", reasons=("correction_red_line",))

        known = cls._numeric_values(facts)
        if not allow_unverified_numbers:
            # No-data/refusal drafts and drafts carrying a bundle both use the same check;
            # metadata years/timestamps are ignored by ``_untraceable_numbers``.  A supplied
            # bundle containing only unverified facts therefore cannot bless a new number.
            unknown = cls._untraceable_numbers(all_text, known if facts is not None else set())
            if unknown:
                message = (
                    "答案包含无法回溯到核验事实的数字"
                    if facts is not None
                    else "缺少事实依据，不能输出数字"
                )
                raise OutputGuardError(message, reasons=("untraceable_number", *unknown[:8]))
        return draft

    # Compatibility aliases for callers that prefer a predicate or a shorter verb.
    guard = validate

    @classmethod
    def is_safe(cls, answer: DraftAnswer | Mapping[str, Any] | str, facts: Any = None) -> bool:
        try:
            cls.validate(answer, facts)
        except (OutputGuardError, TypeError, ValueError):
            return False
        return True

    check = is_safe


def classify_safety(text: str) -> SafetyDecision:
    """Convenience function for dependency-injection-free callers."""

    return SafetyGuard().classify(text)


def refusal_text(decision: SafetyDecision) -> str:
    return SafetyGuard.refusal_text(decision)


__all__ = [
    "OutputGuard",
    "OutputGuardError",
    "SafetyGuard",
    "SafetyDecision",
    "SafetyCategory",
    "SafetyOutcome",
    "classify_safety",
    "refusal_text",
]
