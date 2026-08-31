"""Deterministic Chinese intent/entity parser.

This parser intentionally favours an explicit clarification over a guessed team
or game.  It is small enough to audit and can later be replaced behind the same
``ParseResult`` seam.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from apps.api.src.domain.models import (
    Category,
    Claim,
    ConversationContext,
    EntityKind,
    EntityRef,
    IntentName,
    MetricRef,
    Operation,
    QueryIntent,
    QueryMode,
    Slot,
    StatScope,
    TimeWindow,
    TimeWindowScope,
)
from apps.api.src.domain.time_policy import (
    Clock,
    current_season,
    game_end_window,
    local_date_range,
    make_season_label,
    parse_season_label,
    resolve_relative_date,
    resolve_season_phrase,
    season_label_for_date,
)


@dataclass(slots=True)
class ParseResult:
    intent: QueryIntent
    entity_candidates: dict[str, list[EntityRef]] = field(default_factory=dict)
    normalized_filters: Any | None = None
    missing_slots: list[Slot] = field(default_factory=list)
    ambiguity_reasons: list[str] = field(default_factory=list)
    confidence: dict[str, float] = field(default_factory=dict)

    @property
    def ambiguous(self) -> bool:
        return bool(self.ambiguity_reasons)


def _team(canonical_id: str, name: str, *aliases: str) -> EntityRef:
    return EntityRef(
        kind=EntityKind.TEAM,
        canonical_id=canonical_id,
        display_name=name,
        aliases=list(aliases),
        confidence=1,
    )


def _player(canonical_id: str, name: str, *aliases: str) -> EntityRef:
    return EntityRef(
        kind=EntityKind.PLAYER,
        canonical_id=canonical_id,
        display_name=name,
        aliases=list(aliases),
        confidence=1,
    )


TEAMS: tuple[EntityRef, ...] = (
    _team("bos", "凯尔特人", "Boston Celtics", "Celtics", "BOS", "波士顿凯尔特人"),
    _team("okc", "雷霆", "Oklahoma City Thunder", "Thunder", "OKC", "俄克拉荷马雷霆"),
    _team("den", "掘金", "Denver Nuggets", "Nuggets", "DEN"),
    _team("lal", "湖人", "Los Angeles Lakers", "Lakers", "LAL"),
    _team("gsw", "勇士", "Golden State Warriors", "Warriors", "GSW"),
    _team("sas", "马刺", "San Antonio Spurs", "Spurs", "SAS", "圣安东尼奥马刺"),
    _team("nyk", "尼克斯", "New York Knicks", "Knicks", "NYK", "纽约尼克斯"),
)
PLAYERS: tuple[EntityRef, ...] = (
    _player("jaylen-brown", "杰伦·布朗", "Jaylen Brown", "J. Brown", "布朗"),
    _player("jayson-tatum", "杰森·塔图姆", "Jayson Tatum", "J. Tatum", "塔图姆"),
    _player(
        "shai-gilgeous-alexander",
        "谢伊·吉尔杰斯-亚历山大",
        "Shai Gilgeous-Alexander",
        "S. Gilgeous-Alexander",
        "Shai",
        "亚历山大",
    ),
    _player("derrick-white", "德里克·怀特", "Derrick White", "D. White", "怀特"),
    _player("lu-dort", "吕冈茨·多尔特", "Luguentz Dort", "L. Dort", "Dort", "多尔特"),
    _player("kevin-durant", "凯文·杜兰特", "杜兰特", "Kevin Durant", "K. Durant", "KD"),
)
GAMES: dict[str, EntityRef] = {
    "2026-finals-g4": EntityRef(
        kind=EntityKind.GAME,
        canonical_id="2026-finals-g4",
        display_name="2025-26 总决赛 G4",
        aliases=["G4", "第四场"],
        confidence=1,
    ),
    "2026-finals-g3": EntityRef(
        kind=EntityKind.GAME,
        canonical_id="2026-finals-g3",
        display_name="2025-26 总决赛 G3",
        aliases=["G3", "第三场"],
        confidence=1,
    ),
    "2026-finals-g2": EntityRef(
        kind=EntityKind.GAME,
        canonical_id="2026-finals-g2",
        display_name="2025-26 总决赛 G2",
        aliases=["G2", "第二场"],
        confidence=1,
    ),
    "2026-finals-g1": EntityRef(
        kind=EntityKind.GAME,
        canonical_id="2026-finals-g1",
        display_name="2025-26 总决赛 G1",
        aliases=["G1", "第一场"],
        confidence=1,
    ),
}

# Chinese and Arabic ordinal forms share one canonical conversion.  Keeping
# this map next to the fixture game references avoids treating ``第4场`` as a
# quarter later in the parser.
_ORDINAL_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
}


def _contains_alias(text: str, alias: str) -> bool:
    if alias.isascii() and re.fullmatch(r"[A-Za-z0-9 .'-]+", alias):
        return (
            re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, re.I) is not None
        )
    return alias in text


_EXPLICIT_SEASON_RE = re.compile(r"(?<!\d)\d{4}[-/]\d{2,4}(?:赛季)?(?!\d)(?![-/]\d{1,2}(?:日)?)")
# A standalone calendar year is common shorthand for a Finals season (for
# example, ``1999 G4`` means the 1998-99 Finals).  It must be kept separate
# from a ``YYYY-YY`` season label and from a full date: silently treating the
# year as absent would make the generic ``G4`` alias bind the current fixture.
_EXPLICIT_YEAR_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*年"
    # Chinese dates such as ``2026年6月12日`` are dates, not season hints.
    r"(?!\s*\d{1,2}\s*月)(?!\d)"
)
_BARE_YEAR_RE = re.compile(
    r"(?<![\d/\-–—])((?:19|20)\d{2})"
    # Do not pull the first four digits out of ``YYYY-MM[-DD]`` or
    # ``YYYY年MM月``.  The season/date parsers handle those forms separately.
    r"(?!\s*(?:[-/–—]\s*\d{1,4}|年\s*\d{1,2}\s*月))(?!\d)"
)
_PREDICTION_RE = re.compile(
    r"(?:谁|哪(?:支|个)?队?|[\u4e00-\u9fffA-Za-z]{2,20})"
    # ``最有希望/最有机会/最可能`` are frequent forms in Chinese sports
    # copy; the optional ``最`` must sit before the whole phrase rather than
    # before just ``可能``.
    r"(?:最?(?:有希望|有机会|有望|可能)|会不会|能不能|会|将|能|能否|看好)"
    # ``谁会是冠军``/``哪队将是总冠军`` put a copula between the modal
    # and the championship noun.  Treat the copula as optional so the more
    # common ``谁会夺冠`` form remains covered by the same rule.
    r"(?:(?:赢得?|拿下?|拿到?|成为|是)?(?:总)?冠军|夺冠)",
    re.IGNORECASE,
)
_PREDICTION_META_RE = re.compile(
    r"(?:预测|预判|看好).{0,20}(?:总?冠军|夺冠)"
    r"|(?:总?冠军|夺冠).{0,8}(?:预测|预判)"
    # Questions phrased as “夺冠热门是谁/谁是夺冠热门” contain no modal
    # verb, but are still forward-looking ranking requests.  Keep the window
    # bounded so an objective sentence such as “历史冠军，后来预测……” is
    # not accidentally swallowed as a forecast for the current season.
    r"|(?:总?冠军|夺冠|争冠).{0,4}(?:热门|大热|候选)(?:是谁|是哪(?:支|个)?队?|哪(?:支|个)?队?)?"
    r"|(?:谁|哪(?:支|个)?队?).{0,2}(?:是|为)?(?:总?冠军|夺冠|争冠)(?:热门|大热|候选)"
    # Subject-first forms such as “今年总冠军会是谁” put the modal after
    # the championship noun, so they cannot be covered by ``_PREDICTION_RE``.
    r"|(?:总?冠军|夺冠|争冠).{0,4}(?:会|将|可能|最终).{0,4}(?:谁|哪(?:支|个)?队?)",
    re.IGNORECASE,
)

# Current-season winner questions are easy to confuse with the historical
# ``championship`` lookup below.  A phrase such as ``今年总冠军是谁`` carries
# no explicit modal verb, but it still asks for an outcome that may not have
# happened yet.  Keep the marker deliberately narrow: past-tense/explicit
# season wording remains eligible for the history provider, while current or
# upcoming temporal markers with a winner question take the bounded prediction
# path in the use case.
_CURRENT_PREDICTION_RE = re.compile(
    r"(?:今年|本赛季|当前赛季|这个赛季|新赛季|下赛季|接下来|未来|明年)"
    r".{0,12}(?:总?冠军|夺冠|争冠)"
    r".{0,8}(?:谁|哪(?:支|个)?队?|哪队|会|将|可能|热门|候选|已经确定)"
    r"|(?:今年|本赛季|当前赛季|这个赛季|新赛季|下赛季|接下来|未来|明年)"
    r".{0,8}(?:谁|哪(?:支|个)?队?|哪队).{0,8}(?:总?冠军|夺冠|争冠)"
    r"|(?:谁|哪(?:支|个)?队?|哪队|会|将|可能|热门|候选)"
    r".{0,8}(?:今年|本赛季|当前赛季|这个赛季|新赛季|下赛季|接下来|未来|明年)"
    r".{0,8}(?:总?冠军|夺冠|争冠)",
    re.IGNORECASE,
)

_GAME_PREDICTION_RE = re.compile(
    # Prediction wording about a game outcome is in scope when it is not tied
    # to money.  Keep an explicit forecasting verb in this rule so an ordinary
    # result question such as ``G4 谁赢了`` remains a schedule lookup.
    r"(?:预测|预判|看好|猜(?:测|一下)?|预计|判断)"
    r".{0,20}(?:谁|哪(?:支|个)?队?|哪队)"
    r".{0,8}(?:赢|获胜|取胜|胜出)"
    r"|(?:预测|预判|看好|猜(?:测|一下)?|预计|判断)"
    r".{0,20}(?:赢|获胜|取胜|胜出)"
    r".{0,8}(?:谁|哪(?:支|个)?队?|哪队)",
    re.IGNORECASE,
)


def _explicit_season_start(text: str) -> int | None:
    """Return an explicit season start year, or ``None`` when absent/invalid.

    Fixture game references are only safe to expand when the user either
    omitted a season or named the fixture's season.  Keeping malformed tokens
    as ``None`` lets the caller treat them as unavailable rather than silently
    binding a different season's G4.
    """

    match = _EXPLICIT_SEASON_RE.search(text)
    if match:
        try:
            return parse_season_label(match.group(0)).start_year
        except (TypeError, ValueError):
            return -1
    # In a historical finals/record question, a standalone calendar year is
    # conventionally the season's ending year (1999 → 1998-99).  Full dates
    # are intentionally left to ``resolve_relative_date`` and do not reach
    # this branch because the season regex excludes a month/day suffix.
    year = _EXPLICIT_YEAR_RE.search(text)
    if year:
        return int(year.group(1)) - 1
    year = _BARE_YEAR_RE.search(text)
    if year:
        return int(year.group(1)) - 1
    return None


def resolve_entities(text: str) -> list[EntityRef]:
    found: list[EntityRef] = []
    for candidate in (*TEAMS, *PLAYERS):
        if any(
            _contains_alias(text, alias) for alias in [candidate.display_name, *candidate.aliases]
        ):
            found.append(candidate)
    # Game references are checked separately to avoid matching a generic
    # ``第四节``.  Accept both ``G4``/``第4场`` and Chinese ordinals.  The
    # built-in fixture IDs represent 2025-26 only; an explicitly different (or
    # malformed) season must not be mapped to a current fixture by accident.
    upper = text.upper()
    match = re.search(
        r"(?:G\s*([1-7])(?!\d)|第\s*(?:(\d{1,2})|([一二三四五六七]))\s*场)",
        upper,
    )
    explicit_season_start = _explicit_season_start(text)
    if match and explicit_season_start in (None, 2025):
        raw_number = match.group(1) or match.group(2)
        number = int(raw_number) if raw_number else _ORDINAL_DIGITS.get(match.group(3))
        if number is not None and 1 <= number <= 7:
            ref = GAMES.get(f"2026-finals-g{number}")
            if ref:
                found.append(ref)
    return found


def _metric_refs(text: str, *, scope: StatScope = StatScope.GAME) -> list[MetricRef]:
    metrics: list[MetricRef] = []
    mapping = (
        ("得分", "points", "分"),
        ("篮板", "rebounds", "个"),
        ("助攻", "assists", "次"),
        ("三分", "three_pointers", "个"),
        ("命中率", "field_goal_percentage", "%"),
        ("战绩", "record", "场"),
        ("排名", "rank", "名"),
        ("出场", "games", "场"),
        ("出赛", "games", "场"),
        ("上场", "games", "场"),
        ("冠军", "championship", "次"),
        ("夺冠", "championship", "次"),
        ("队史", "franchise_record", "次"),
    )
    for token, name, unit in mapping:
        if token in text:
            metrics.append(MetricRef(name=name, unit=unit, scope=scope))
    if not metrics:
        metrics.append(MetricRef(name="points", unit="分", scope=scope))
    return metrics


def _normalize_common_typos(text: str) -> str:
    """Correct a narrowly scoped typo without changing the user's wording broadly."""

    # “出场” is frequently entered as “出厂”; only normalize it when the
    # surrounding phrase clearly asks for an appearance count/statistic.
    return re.sub(r"出厂(?=次数|场次|数|率|记录)", "出场", text)


# Standings questions often scope the requested rank to one conference.  Keep
# the parser's output canonical so downstream providers do not have to know
# about Chinese/English wording variants.  ``QueryIntent.conference`` remains
# optional: a question that mentions both conferences intentionally falls back
# to an unscoped standings result rather than silently choosing one side.
_CONFERENCE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "East",
        (
            "东部联盟",
            "东部赛区",
            "东部",
            "东区",
            "Eastern Conference",
            "Eastern",
            "East Conference",
            "East",
        ),
    ),
    (
        "West",
        (
            "西部联盟",
            "西部赛区",
            "西部",
            "西区",
            "Western Conference",
            "Western",
            "West Conference",
            "West",
        ),
    ),
)


def _parse_conference(text: str) -> str | None:
    """Return ``East``/``West`` when exactly one conference is mentioned."""

    # ``东西部`` is a compact spelling for both conferences; neither
    # substring contains the other conference's two-character alias, so mark
    # it explicitly before scanning the individual aliases.
    if "东西部" in text or "东西区" in text:
        return None
    found: list[str] = []
    for canonical, aliases in _CONFERENCE_ALIASES:
        if any(_contains_alias(text, alias) for alias in aliases):
            found.append(canonical)
    return found[0] if len(found) == 1 else None


def _parse_game_number(text: str) -> int | None:
    match = re.search(
        r"(?:G\s*([1-7])(?!\d)|第\s*(?:(\d{1,2})|([一二三四五六七]))\s*场)",
        text,
        re.I,
    )
    if not match:
        return None
    raw_number = match.group(1) or match.group(2)
    number = int(raw_number) if raw_number else _ORDINAL_DIGITS.get(match.group(3))
    return number if number is not None and 1 <= number <= 20 else None


def _parse_clock_window(text: str) -> TimeWindow | None:
    if "最后那个球" in text or "最后一球" in text:
        each_period = bool(re.search(r"(?:每|各)(?:个|一)?\s*(?:节|节次)", text))
        if each_period:
            return TimeWindow(
                start_seconds=0,
                end_seconds=5,
                scope=TimeWindowScope.PERIOD_END,
                all_periods=True,
            )
        return game_end_window(5)
    match = re.search(
        r"(?:最后|末节|终场)?\s*(\d{1,2}|[零一二三四五六七八九十]+)\s*秒",
        text,
    )
    if not match:
        return None
    raw_seconds = match.group(1)
    if raw_seconds.isdigit():
        seconds = float(raw_seconds)
    else:
        # The common Chinese forms are enough for a user-facing clock window
        # (一…九、十、十一…十九、二十…九十九).  Keeping this conversion
        # deterministic avoids sending natural-language arithmetic to a model.
        digits = {
            "零": 0,
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if raw_seconds == "十":
            seconds = 10.0
        elif raw_seconds.startswith("十") and len(raw_seconds) == 2:
            seconds = float(10 + digits.get(raw_seconds[1], 0))
        elif raw_seconds.endswith("十") and len(raw_seconds) == 2:
            seconds = float(digits.get(raw_seconds[0], 0) * 10)
        elif len(raw_seconds) == 3 and raw_seconds[1] == "十":
            seconds = float(digits.get(raw_seconds[0], 0) * 10 + digits.get(raw_seconds[2], 0))
        elif len(raw_seconds) == 1 and raw_seconds in digits:
            seconds = float(digits[raw_seconds])
        else:
            return None
    if seconds > 60:
        return None
    each_period = bool(re.search(r"(?:每|各)(?:个|一)?\s*(?:节|节次)", text))
    scope = (
        TimeWindowScope.PERIOD_END
        if (
            "末节" in text
            or each_period
            or "某节" in text
            or re.search(r"第\s*(?:4|四)\s*节", text)
        )
        else TimeWindowScope.GAME_END
    )
    return TimeWindow(
        start_seconds=0,
        end_seconds=seconds,
        scope=scope,
        all_periods=each_period,
    )


def _claims(text: str, entities: Iterable[EntityRef]) -> list[Claim]:
    game = next((item for item in entities if item.kind is EntityKind.GAME), None)
    if game is None:
        return []
    team_mentions = [item for item in entities if item.kind is EntityKind.TEAM]
    if not team_mentions:
        # Common premise phrasing names a team not otherwise selected by intent.
        team_mentions = [
            team
            for team in TEAMS
            if any(_contains_alias(text, alias) for alias in [team.display_name, *team.aliases])
        ]
    if (
        any(token in text for token in ("赢了", "获胜", "赢家", "胜者", "拿下比赛"))
        and team_mentions
    ):
        return [
            Claim(subject=game, predicate="winner", claimed_value=team_mentions[0].display_name)
        ]
    score_match = re.search(
        r"([凯尔特人雷霆湖人勇士掘金A-Za-z]+)\s*(?:是|拿了|得到)?\s*(\d{1,3})\s*分", text
    )
    if score_match and team_mentions:
        return [
            Claim(
                subject=team_mentions[0], predicate="score", claimed_value=int(score_match.group(2))
            )
        ]
    # Event-level trap questions from the brief (last attack/shot, assist,
    # three-pointer or bank shot) need typed claims so the PBP verifier can
    # distinguish a wrong premise from an unavailable field.  Claims are only
    # created for verification wording; an open question such as “谁投的？”
    # should simply render the verified event details.
    verification_wording = any(
        token in text for token in ("是不是", "是否", "对吗", "我记得", "非说", "据说")
    )
    event_wording = any(
        token in text
        for token in (
            "最后一攻",
            "最后一球",
            "最后那个球",
            "最后一投",
            "最后一次投篮",
            "决胜球",
            "绝杀",
            "反超",
            "扳平",
            "关键球",
        )
    )
    if verification_wording and event_wording:
        players = [item for item in entities if item.kind is EntityKind.PLAYER]
        claims: list[Claim] = []
        if players and any(
            token in text for token in ("投", "出手", "上篮", "扣篮", "跳投", "三分", "打板")
        ):
            claims.append(
                Claim(
                    subject=game,
                    predicate="last_shooter",
                    claimed_value=players[0].display_name,
                )
            )
        if players and "助攻" in text:
            claims.append(
                Claim(
                    subject=game,
                    predicate="last_assister",
                    claimed_value=players[0].display_name,
                )
            )
        shot_claim: str | None = None
        if "三分" in text:
            shot_claim = "THREE_POINT"
        elif any(token in text for token in ("上篮", "扣篮", "跳投", "打板")):
            shot_claim = "TWO_POINT"
        elif "罚球" in text:
            shot_claim = "FREE_THROW"
        if shot_claim is not None:
            claims.append(Claim(subject=game, predicate="last_shot_type", claimed_value=shot_claim))
        score_match = re.search(
            r"(?:比分|分数)(?:是|为|变成|来到)?\s*(\d{1,3})\s*[-－—:：比]\s*(\d{1,3})",
            text,
        )
        if score_match:
            claims.append(
                Claim(
                    subject=game,
                    predicate="last_score_after",
                    claimed_value={
                        "home": int(score_match.group(1)),
                        "away": int(score_match.group(2)),
                    },
                )
            )
        return claims
    return []


class IntentParser:
    def __init__(
        self, *, clock: Clock | None = None, input_timezone: str = "Asia/Shanghai"
    ) -> None:
        self.clock = clock
        self.input_timezone = input_timezone

    def parse(self, text: str, context: ConversationContext | None = None) -> ParseResult:
        message = _normalize_common_typos(text.strip())
        entities = resolve_entities(message)
        # Follow-up pronouns resolve only from the current session.  Keep the
        # shorthand marker around so a new session can explicitly ask for a
        # game instead of silently searching the whole scoreboard.
        # Game-level pronouns inherit only an active game.  When the same
        # wording is attached to “系列赛” (for example “这轮系列赛目前
        # 大比分”), it denotes a valid series-wide query and must not be
        # forced through the single-game clarification branch.
        series_phrase = "系列赛" in message
        shorthand = any(
            token in message for token in ("那场", "这场", "这轮", "最后那个球", "刚才")
        ) and not (series_phrase and any(token in message for token in ("这场", "这轮")))
        # “某场最后一攻” (and similar wording) leaves the game unresolved.
        # Mark it for clarification rather than falling through to the newest
        # scoreboard entry and returning an unrelated fact.
        unspecified_game = any(
            token in message for token in ("某场", "某一场", "某场比赛", "那一场比赛")
        )
        explicit_game = any(item.kind is EntityKind.GAME for item in entities)
        if context and shorthand:
            if context.active_game and not any(item.kind is EntityKind.GAME for item in entities):
                entities.append(context.active_game)
            for active in (context.active_team, context.active_player):
                if active and not any(
                    item.canonical_id == active.canonical_id for item in entities
                ):
                    entities.append(active)

        lower = message.lower()
        clock_window_hint = _parse_clock_window(message)
        event_focus = any(
            token in message
            for token in (
                "最后一攻",
                "最后一球",
                "最后那个球",
                "最后一投",
                "最后一次投篮",
                "决胜球",
                "绝杀",
                "反超",
                "扳平",
                "关键球",
                "谁助攻",
                "出手者",
            )
        )
        # Event-focused wording is a PBP request even when it is phrased as
        # an open question (for example ``谁命中了最后一投？`` or
        # ``最后一攻是谁？``).  Requiring a second detail token here used to
        # route those questions to the box-score summary, which cannot answer
        # an event-level shooter/assist/type query.  Keep the explicit event
        # vocabulary narrow enough that ordinary ``最后一节``/``最后一场``
        # references are not accidentally treated as a play-by-play window.
        is_pbp = (
            any(token in message for token in ("逐回合", "关键球", "关键回合", "最后几秒", "回放"))
            or clock_window_hint is not None
            or event_focus
        )
        is_fact = any(
            token in message for token in ("核验", "核查", "我记得", "是不是", "对吗", "记得")
        )
        is_tactical = any(
            token in message
            for token in ("战术", "挡拆", "防守策略", "为什么能", "怎么限制", "假设", "如果")
        )
        is_recap = any(
            token in message for token in ("复盘", "表现如何", "评价", "主观", "关键转折")
        )
        # News/background is an objective retrieval mode.  Keep explicit
        # news wording ahead of generic history/schedule/data markers (for
        # example “总决赛赛后新闻”), while the branches above retain
        # precedence for PBP, premise checks and tactical/recap analysis.
        is_news = (
            any(
                token in message
                for token in ("新闻", "消息", "资讯", "报道", "动态", "近况", "背景")
            )
            or re.search(
                r"\b(?:news|headline|headlines|update|updates|background)\b",
                lower,
                re.IGNORECASE,
            )
            is not None
        )
        # Future-outcome wording must not be routed to the historical
        # championship provider.  Keep this as a normal, in-scope analysis
        # intent (predictions are allowed by product policy), but let the use
        # case return a clearly bounded/no-data answer instead of presenting a
        # previous champion as a forecast.
        is_prediction = (
            _PREDICTION_RE.search(message) is not None
            or _PREDICTION_META_RE.search(message) is not None
            or _CURRENT_PREDICTION_RE.search(message) is not None
            or _GAME_PREDICTION_RE.search(message) is not None
            or any(
                token in message
                for token in (
                    "谁会夺冠",
                    "谁将夺冠",
                    "谁会成为冠军",
                    "谁将成为冠军",
                    "能夺冠吗",
                    "能否夺冠",
                    "有机会夺冠",
                    "有望夺冠",
                    "夺冠概率",
                    "夺冠可能",
                    "预测冠军",
                    "预测一下总冠军",
                    "预测谁夺冠",
                    "看好谁夺冠",
                )
            )
        )
        is_history = any(
            token in message
            for token in (
                "历史",
                "冠军",
                "夺冠",
                "总冠军",
                "纪录",
                "记录",
                "历届",
                "队史",
                "几座",
            )
        )
        is_schedule = any(
            token in message
            for token in ("赛程", "赛果", "排名", "战绩", "比赛", "系列赛", "大比分")
        )
        # A follow-up may omit an explicit pronoun and only narrow the clock
        # window (e.g. after selecting G4, “每节最后五秒”).  Inherit the
        # active game for that game-scoped PBP form, but never for a
        # series-wide request or a fresh session.
        if context and context.active_game and not explicit_game and is_pbp and not series_phrase:
            if not any(item.canonical_id == context.active_game.canonical_id for item in entities):
                entities.append(context.active_game)
            for active in (context.active_team, context.active_player):
                if active and not any(
                    item.canonical_id == active.canonical_id for item in entities
                ):
                    entities.append(active)
            shorthand = True
        if is_prediction:
            category, intent_name, mode, operation = (
                Category.F,
                IntentName.TACTICAL,
                QueryMode.ANALYSIS,
                Operation.EXPLAIN,
            )
        elif is_pbp:
            category, intent_name, mode, operation = (
                Category.E,
                IntentName.PLAY_BY_PLAY,
                QueryMode.OBJECTIVE,
                Operation.EXPLAIN,
            )
        # An analytical request may include words such as “核验/已核验” to
        # constrain the evidence source (for example “基于已核验数据分析
        # 挡拆”).  Those words must not downgrade the request to FACT_CHECK:
        # tactical/recap intent is what decides whether the constrained model
        # composer is selected.  A pure “我记得…帮我核验” request still has
        # no tactical/recap marker and therefore remains FACT_CHECK.
        elif is_fact and not (is_tactical or is_recap):
            category, intent_name, mode, operation = (
                Category.D,
                IntentName.FACT_CHECK,
                QueryMode.FACT_CHECK,
                Operation.LOOKUP,
            )
        elif is_tactical:
            category, intent_name, mode, operation = (
                Category.F,
                IntentName.TACTICAL,
                QueryMode.ANALYSIS,
                Operation.EXPLAIN,
            )
        elif is_recap:
            category, intent_name, mode, operation = (
                Category.G,
                IntentName.RECAP,
                QueryMode.ANALYSIS,
                Operation.EXPLAIN,
            )
        elif is_news:
            category, intent_name, mode, operation = (
                Category.A,
                IntentName.DATA,
                QueryMode.OBJECTIVE,
                Operation.LOOKUP,
            )
        elif is_history:
            category, intent_name, mode, operation = (
                Category.C,
                IntentName.HISTORY,
                QueryMode.OBJECTIVE,
                Operation.LOOKUP,
            )
        elif is_schedule:
            category, intent_name, mode, operation = (
                Category.B,
                IntentName.SCHEDULE_RESULT,
                QueryMode.OBJECTIVE,
                Operation.LOOKUP,
            )
        else:
            category, intent_name, mode, operation = (
                Category.A,
                IntentName.DATA,
                QueryMode.OBJECTIVE,
                Operation.LOOKUP,
            )

        # Only a shorthand resolved from an active game is a FOLLOW_UP.  An
        # explicit G4/G3 reference wins, and a shorthand without an active
        # game is represented as a missing slot below.
        if context and shorthand and context.active_game and not explicit_game:
            category, intent_name = Category.H, IntentName.FOLLOW_UP

        season_value = resolve_season_phrase(message, self.clock, timezone_name=self.input_timezone)
        # A standalone calendar year in a historical/finals/championship
        # question conventionally denotes the season-ending year (for example
        # ``1999 年总决赛`` means NBA season ``1998-99``).  The generic season
        # resolver intentionally does not interpret bare years because they
        # can also be part of a date or an unrelated statistic.  Restrict the
        # fallback to history/championship/finals language so ordinary
        # questions remain date-neutral and cannot silently select a season.
        if season_value is None:
            # ``YYYY`` is accepted as a Finals ending year only when the
            # wording establishes a historical/game context.  This keeps a
            # bare year in an unrelated sentence from changing the query's
            # season while still making ``1999 G4`` explicit and safe.
            historical_year = _EXPLICIT_YEAR_RE.search(message) or _BARE_YEAR_RE.search(message)
            historical_context = (
                category is Category.C
                or _parse_game_number(message) is not None
                or any(
                    token in message
                    for token in ("总决赛", "总冠军", "冠军", "夺冠", "历届", "队史")
                )
            )
            if historical_year and historical_context:
                season_value = make_season_label(int(historical_year.group(1)) - 1)
        date_range = None
        relative_date = resolve_relative_date(
            message, self.clock, timezone_name=self.input_timezone
        )
        if relative_date is not None:
            date_range = local_date_range(relative_date, self.input_timezone)
        # A standings request without an explicit season is interpreted in the
        # current NBA-season context.  If a concrete calendar date was supplied,
        # derive its cross-calendar season; otherwise use the injected clock so
        # production and deterministic tests follow the same policy.
        if season_value is None and category is Category.B and "排名" in message:
            season_value = (
                season_label_for_date(
                    relative_date,
                    timezone_name=self.input_timezone,
                )
                if relative_date is not None
                else current_season(self.clock, timezone_name=self.input_timezone)
            )
        conference = _parse_conference(message)
        game_number = _parse_game_number(message)
        clock_window = clock_window_hint
        period = None
        # A quarter must carry the ``节``/``期`` suffix when written as
        # ``第4节``.  An earlier optional suffix consumed ``第4场`` and
        # incorrectly turned a game number into a period.
        period_match = re.search(
            r"(?:Q\s*([1-4])(?:\s*(?:节|期))?|第\s*([1-4一二三四])\s*(?:节|期))",
            message,
            re.I,
        )
        if period_match:
            raw_period = period_match.group(1) or period_match.group(2)
            period = (
                int(raw_period)
                if raw_period and raw_period.isdigit()
                else _ORDINAL_DIGITS.get(raw_period)
            )
        if period is None and "末节" in message:
            period = 4
        if period is not None:
            if clock_window and clock_window.scope is TimeWindowScope.GAME_END:
                clock_window = TimeWindow(
                    start_seconds=clock_window.start_seconds,
                    end_seconds=clock_window.end_seconds,
                    scope=TimeWindowScope.PERIOD_END,
                )
        missing: list[Slot] = []
        # Keep an explicit game number in the parsed intent for telemetry even
        # when this fixture has no corresponding entity.  Without a missing
        # slot, a request such as “1999 G4 赛果” would fall through to a broad
        # search and could return the newest unrelated fixture.
        has_game_entity = any(item.kind is EntityKind.GAME for item in entities)
        if game_number is not None and not has_game_entity:
            missing.append(
                Slot(
                    name="game",
                    reason="该赛季的比赛暂未匹配，请补充可核验的具体比赛",
                )
            )
        if (shorthand or unspecified_game) and not has_game_entity:
            if not any(slot.name == "game" for slot in missing):
                missing.append(Slot(name="game", reason="请指定比赛或在同一会话中先选择一场比赛"))
        elif is_pbp and not has_game_entity:
            reason = (
                "请从精彩回顾选择最近一场比赛，或补充对阵双方"
                if re.search(r"(?:最近|上一场|刚刚).{0,4}(?:场比赛|比赛)", message)
                else "请指定比赛或在同一会话中先选择一场比赛"
            )
            if not any(slot.name == "game" for slot in missing):
                missing.append(Slot(name="game", reason=reason))
        if (
            clock_window is not None
            and clock_window.scope is TimeWindowScope.PERIOD_END
            and period is None
            and not clock_window.all_periods
        ):
            missing.append(Slot(name="period", reason="请指定具体节次，例如第四节"))
        # A shorthand with no active game already carries the concrete
        # ``game`` missing slot; avoid adding a redundant generic subject slot
        # that would make the clarification noisy.
        if (
            category is Category.A
            and not entities
            and not shorthand
            and game_number is None
            and not is_news
        ):
            missing.append(Slot(name="subject", reason="请指定球队、球员或比赛"))
        claims = _claims(message, entities) if is_fact else []
        confidence = 1.0 if entities else (0.82 if category in {Category.B, Category.C} else 0.65)
        if missing:
            confidence = min(confidence, 0.5)
        metrics = _metric_refs(
            message,
            scope=StatScope.SERIES if "系列赛" in message else StatScope.GAME,
        )
        if is_prediction:
            is_game_prediction = _GAME_PREDICTION_RE.search(message) is not None and not (
                _PREDICTION_RE.search(message)
                or _PREDICTION_META_RE.search(message)
                or _CURRENT_PREDICTION_RE.search(message)
            )
            metrics = [
                MetricRef(
                    name=(
                        "game_outcome_prediction"
                        if is_game_prediction
                        else "championship_prediction"
                    ),
                    unit=None,
                    scope=(StatScope.GAME if is_game_prediction else StatScope.SEASON),
                )
            ]
        # History has two materially different questions: a latest champion
        # lookup and a franchise title count.  Preserve that distinction in
        # the typed metric so the planner can select the right record source.
        if (
            is_history
            and not is_prediction
            and any(
                token in message
                # “队史上一次夺冠是哪一年” asks for the latest title season,
                # not the franchise's total.  Treat ``队史`` as context only;
                # require an actual quantity/count expression for the numeric
                # franchise-record branch.
                for token in (
                    "多少",
                    "几个",
                    "几次",
                    "次数",
                    "数量",
                    "总共",
                    "几座",
                    "几冠",
                    "冠军数",
                    "夺冠数",
                )
            )
        ):
            metrics = [
                MetricRef(
                    name="franchise_record",
                    unit="次",
                    scope=StatScope.CAREER,
                )
            ]
        elif (
            is_history
            and not is_prediction
            and not any(token in message for token in ("历届", "历年", "各届", "每届", "所有"))
            and any(
                token in message
                for token in ("最近", "最新", "最近一次", "卫冕", "冠军是谁", "总冠军", "夺冠")
            )
        ):
            metrics = [
                MetricRef(
                    name="championship",
                    unit=None,
                    scope=StatScope.SEASON,
                )
            ]
        # A news marker is authoritative even when the sentence also contains
        # generic history words such as “总决赛背景”.  Use one stable metric
        # name so the planner can route the typed search_news operation.
        if is_news and not is_prediction:
            metrics = [
                MetricRef(
                    name="news",
                    unit=None,
                    scope=StatScope.SEASON if season_value is not None else StatScope.GAME,
                )
            ]
        intent = QueryIntent(
            category=category,
            intent_name=intent_name,
            mode=mode,
            confidence=confidence,
            entities=entities,
            metrics=metrics,
            season=season_value,
            conference=conference,
            date_range=date_range,
            game_number=game_number,
            period=period,
            clock_window=clock_window,
            operation=operation,
            premise_claims=claims,
            missing_slots=missing,
        )
        ambiguity: list[str] = []
        if len(
            {item.canonical_id for item in entities if item.kind is not EntityKind.GAME}
        ) > 2 and category not in {Category.B, Category.C}:
            ambiguity.append("实体过多")
        return ParseResult(
            intent=intent,
            entity_candidates={"entities": entities},
            missing_slots=missing,
            ambiguity_reasons=ambiguity,
            confidence={
                "intent": float(confidence),
                "entities": 1.0 if entities else 0.5,
                "time": 1.0 if season_value or date_range else 0.9,
            },
        )


def parse_query(
    text: str, context: ConversationContext | None = None, *, clock: Clock | None = None
) -> ParseResult:
    return IntentParser(clock=clock).parse(text, context)


__all__ = [
    "GAMES",
    "PLAYERS",
    "TEAMS",
    "IntentParser",
    "ParseResult",
    "parse_query",
    "resolve_entities",
]
