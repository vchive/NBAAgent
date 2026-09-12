"""Bounded, fixed-host parser for public Hupu NBA schedule and box-score pages."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from apps.api.src.application.parser import PLAYERS, TEAMS
from apps.api.src.application.ports import ProviderResult, RequestBudget
from apps.api.src.domain.errors import ProviderError, ProviderErrorKind
from apps.api.src.domain.models import (
    EntityKind,
    EntityRef,
    Evidence,
    Freshness,
    Game,
    GameBundle,
    GameStatus,
    PlayByPlayBundle,
    PlayEvent,
    PlayEventType,
    SeasonLabel,
    ShotType,
    SourceClass,
    StatLine,
    StatScope,
    TrustLevel,
    Venue,
)
from apps.api.src.domain.safety import neutralize_external_internal_names

HUPU_BASE_URL = "https://nba.hupu.com"
HUPU_HOST = "nba.hupu.com"
_BEIJING = ZoneInfo("Asia/Shanghai")
_GAME_ID_RE = re.compile(r"^(?:hupu:)?(\d{1,12})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_CLOCK_RE = re.compile(r"^(?:(\d{1,2}):(\d{2}(?:\.\d+)?)|(\d{1,3}(?:\.\d+)?)[\"']?)$")
_SCORE_RE = re.compile(r"^(\d{1,3})\s*[-–]\s*(\d{1,3})$")
_PERIOD_RE = re.compile(r"第([\u4e00\u4e8c\u4e09\u56db1234])节开始")
_OVERTIME_RE = re.compile(
    r"(?:第([\u4e00\u4e8c\u4e09\u56db\u4e94\u516d123456])个?)?加时(?:赛)?开始"
)
_PLAYER_PREFIX_RE = re.compile(
    r"^(.{1,80}?)(?=(?:第[\u4e00\u4e8c\u4e09四1234]罚|[\u4e24\u4e09]罚|"
    r"罚球|三分|两分|二分|突破|急停|后撤|转身|跑动|命中|投中|完成|"
    r"空切|空接|快攻|补篮|上篮|扣篮|暴扣|反扣|勾手|跳投|抛投|投篮))"
)
_ASSISTER_RE = re.compile(r"[（(]([^\uff08\uff09()]{1,80}?)助攻[\uff09)]")
_SHOT_MARKER_RE = re.compile(
    r"(?:三分|3分|两分|二分|罚球|上篮|扣篮|暴扣|灌篮|挑篮|勾手|抛投|跳投|投篮|投中|命中|"
    r"得分|成功|不中|不进|未中|打铁|反扣|补篮|空接)"
)
_SHOT_ATTEMPT_RE = re.compile(
    r"(?:三分|3分|两分|二分|上篮|扣篮|暴扣|灌篮|挑篮|勾手|抛投|跳投|投篮|反扣|补篮|空接)"
)
_FREE_THROW_MARKER_RE = re.compile(
    r"(?:第[一二三四五六两123456]罚|[一二三四五六两123456]罚|罚球)"
)
_CHINESE_NUMERALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
}

HUPU_TEAM_SLUGS: dict[str, str] = {
    "hawks": "atl",
    "celtics": "bos",
    "nets": "bkn",
    "hornets": "cha",
    "bulls": "chi",
    "cavaliers": "cle",
    "mavericks": "dal",
    "nuggets": "den",
    "pistons": "det",
    "warriors": "gsw",
    "rockets": "hou",
    "pacers": "ind",
    "clippers": "lac",
    "lakers": "lal",
    "grizzlies": "mem",
    "heat": "mia",
    "bucks": "mil",
    "timberwolves": "min",
    "pelicans": "nop",
    "knicks": "nyk",
    "thunder": "okc",
    "magic": "orl",
    "76ers": "phi",
    "suns": "phx",
    "blazers": "por",
    "kings": "sac",
    "spurs": "sas",
    "raptors": "tor",
    "jazz": "uta",
    "wizards": "was",
}

_TEAM_BY_ID = {team.canonical_id: team for team in TEAMS}
_PLAYER_BY_ID = {player.canonical_id: player for player in PLAYERS}
_HUPU_PLAYER_IDS = {
    "jalenbrunson-150988": "jalen-brunson",
    "victorwembanyama-152987": "victor-wembanyama",
    "karlanthonytowns-150578": "karl-anthony-towns",
    "oganunoby-150508": "og-anunoby",
}


class _Row:
    def __init__(self, table_id: str | None, row_class: str, row_id: str | None) -> None:
        self.table_id = table_id
        self.row_class = row_class
        self.row_id = row_id
        self.cells: list[str] = []
        self.links: list[tuple[str, str]] = []


class _TableRows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_Row] = []
        self._tables: list[str | None] = []
        self._row: _Row | None = None
        self._cell_parts: list[str] | None = None
        self._link_href: str | None = None
        self._link_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "table":
            self._tables.append(values.get("id"))
        elif tag == "tr":
            self._row = _Row(
                self._tables[-1] if self._tables else None,
                values.get("class") or "",
                values.get("id"),
            )
        elif tag == "td" and self._row is not None:
            self._cell_parts = []
        elif tag == "a" and self._row is not None:
            self._link_href = values.get("href")
            self._link_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)
        if self._link_href is not None:
            self._link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._row is not None and self._link_href is not None:
            text = " ".join("".join(self._link_parts).split())
            self._row.links.append((self._link_href, text))
            self._link_href = None
            self._link_parts = []
        elif tag == "td" and self._row is not None and self._cell_parts is not None:
            self._row.cells.append(" ".join("".join(self._cell_parts).split()))
            self._cell_parts = None
        elif tag == "tr" and self._row is not None:
            if self._row.cells:
                self.rows.append(self._row)
            self._row = None
            self._cell_parts = None
        elif tag == "table" and self._tables:
            self._tables.pop()


def _team_from_slug(slug: str) -> EntityRef | None:
    team_id = HUPU_TEAM_SLUGS.get(slug.strip().lower())
    return _TEAM_BY_ID.get(team_id or "")


def _season_for_day(day: datetime) -> SeasonLabel:
    year = day.astimezone(_BEIJING).year
    month = day.astimezone(_BEIJING).month
    start = year if month >= 9 else year - 1
    return SeasonLabel(
        start_year=start,
        end_year=start + 1,
        label=f"{start:04d}-{(start + 1) % 100:02d}",
    )


def _number(value: str) -> int | None:
    value = "".join(value.split()).replace(",", "")
    if not re.fullmatch(r"[+-]?\d+", value):
        return None
    return int(value)


def _shooting(value: str) -> tuple[int | None, int | None]:
    match = re.fullmatch(r"(\d+)-(\d+)", "".join(value.split()))
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _plain_action_text(value: str) -> str | None:
    """Project an untrusted HTML cell into bounded provider-neutral text."""

    text = " ".join(str(value or "").split())
    text = "".join(char for char in text if ord(char) >= 32 and ord(char) != 127)
    text = neutralize_external_internal_names(text).strip()
    return text[:500].rstrip() or None


def _clock_seconds(value: str) -> Decimal | None:
    match = _CLOCK_RE.fullmatch(" ".join(str(value or "").split()))
    if match is None:
        return None
    if match.group(3) is not None:
        return Decimal(match.group(3))
    return Decimal(match.group(1)) * 60 + Decimal(match.group(2))


def _numbered_period(value: str) -> int:
    return _CHINESE_NUMERALS.get(value, int(value) if value.isdigit() else 1)


def _play_event_type(text: str) -> PlayEventType:
    if "换人" in text:
        return PlayEventType.SUBSTITUTION
    if "罚球" in text or re.search(r"(?:第)?[一二三四五六两三四123456]罚", text):
        return PlayEventType.FREE_THROW
    if "犯规" in text:
        return PlayEventType.FOUL
    if any(token in text for token in ("失误", "失球", "违例")):
        return PlayEventType.TURNOVER
    if "篮板" in text:
        return PlayEventType.REBOUND
    if _SHOT_MARKER_RE.search(text):
        return PlayEventType.SHOT
    return PlayEventType.OTHER


def _shot_type(text: str, event_type: PlayEventType) -> ShotType:
    if event_type is PlayEventType.FREE_THROW:
        return ShotType.FREE_THROW
    if event_type is not PlayEventType.SHOT:
        return ShotType.NONE
    if "三分" in text or "3分" in text:
        return ShotType.THREE_POINT
    if any(
        token in text
        for token in (
            "两分",
            "二分",
            "上篮",
            "扣篮",
            "勾手",
            "抛投",
            "补篮",
            "挑篮",
            "反扣",
        )
    ):
        return ShotType.TWO_POINT
    return ShotType.UNKNOWN


def _normalise_person_name(value: str) -> str:
    return re.sub(r"[\s·•・\-‐-―·_]", "", str(value or "")).casefold()


_HUPU_NAME_ALIASES: dict[str, str] = {
    # The live feed is not consistent about transliteration or punctuation.
    "莎伊吉尔乔斯亚历山大": "shai-gilgeous-alexander",
    "谢伊吉尔乔斯亚历山大": "shai-gilgeous-alexander",
    "谢伊吉尔杰斯亚历山大": "shai-gilgeous-alexander",
    "维克多文班亚马": "victor-wembanyama",
    "维克托文班亚马": "victor-wembanyama",
    "维克多温巴亚马": "victor-wembanyama",
    "切特霍姆格伦": "chet-holmgren",
    "艾赛亚哈滕斯坦": "isaiah-hartenstein",
    "以赛亚哈滕斯坦": "isaiah-hartenstein",
    "达龙福克斯": "deaaron-fox",
    "迪亚伦福克斯": "deaaron-fox",
    "德亚伦福克斯": "deaaron-fox",
    # Do these before the generic roster-alias scan.  The source commonly
    # renders compound transliterations with a middle dot or hyphen; without
    # an exact alias, ``乔丹-克拉克森`` was captured by the shorter ``乔丹``
    # alias and surfaced as Michael Jordan in the play-by-play UI.
    "米卡尔布里奇斯": "mikal-bridges",
    "迈克尔布里奇斯": "mikal-bridges",
    "乔丹克拉克森": "jordan-clarkson",
}


def _player_ref(value: str) -> EntityRef | None:
    name = " ".join(str(value or "").strip(" （）()").split())
    name = re.sub(r"^[，,：:]+|[，,：:]+$", "", name)
    if not name or name.endswith("队") or len(name) > 80:
        return None
    key = _normalise_person_name(name)
    mapped_id = _HUPU_NAME_ALIASES.get(key)
    if mapped_id:
        known = _PLAYER_BY_ID.get(mapped_id)
        if known is not None:
            return known
        return EntityRef(
            kind=EntityKind.PLAYER,
            canonical_id=mapped_id,
            display_name=name[:200],
        )
    for player in PLAYERS:
        candidates = (player.display_name, *player.aliases)
        if any(
            _normalise_person_name(candidate) == key
            or key.endswith(_normalise_person_name(candidate))
            for candidate in candidates
        ):
            return player
    # The PBP table contains names as plain text rather than player links.
    # Keep an opaque, deterministic identifier instead of guessing a roster ID.
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return EntityRef(
        kind=EntityKind.PLAYER,
        canonical_id=f"hupu-player-{digest}",
        display_name=name[:200],
    )


def _participant(text: str, *, assister: bool = False) -> EntityRef | None:
    if assister:
        match = _ASSISTER_RE.search(text)
        return _player_ref(match.group(1)) if match else None
    match = _PLAYER_PREFIX_RE.search(text)
    # Prefer an exact source-name alias before fuzzy prefix matching.  This is
    # important for compound names whose first component is itself a famous
    # player's short alias, for example ``乔丹-克拉克森`` versus ``乔丹``.
    if match and _normalise_person_name(match.group(1)) in _HUPU_NAME_ALIASES:
        return _player_ref(match.group(1))
    normalised_text = _normalise_person_name(text)
    for player in PLAYERS:
        if any(
            normalised_text.startswith(_normalise_person_name(candidate))
            for candidate in (player.display_name, *player.aliases)
        ):
            return player
    return _player_ref(match.group(1)) if match else None


def _shot_participant(text: str) -> EntityRef | None:
    """Extract a shooter only when the action text identifies one clearly."""

    marker = _SHOT_ATTEMPT_RE.search(text) or _SHOT_MARKER_RE.search(text)
    if marker is None:
        return None
    prefix = text[: marker.start()]
    # In ``封盖<blocker><shooter><shot>`` the first name is the blocker; the
    # shooter is the portion after the marker.
    if "封盖" in prefix:
        prefix = prefix.rsplit("封盖", 1)[-1]
        return _player_ref(prefix)
    # Resolve from the beginning of the full description first; this lets a
    # known player alias terminate before qualifiers such as ``底角`` or
    # ``突破后指尖``.  The generic prefix fallback handles names absent from
    # the small canonical roster.
    participant = _participant(text)
    if participant is not None:
        return participant
    # Results can precede the distance marker (e.g. ``投中三分``).  Remove
    # those result words before resolving an opaque source name.
    prefix = re.sub(r"(?:不中|不进|未中|命中|投中|得分|成功)$", "", prefix)
    return _player_ref(prefix)


def _free_throw_participant(text: str) -> EntityRef | None:
    marker = _FREE_THROW_MARKER_RE.search(text)
    if marker is None:
        return None
    prefix = re.sub(r"(?:队|个人|恶意犯规)$", "", text[: marker.start()])
    return _player_ref(prefix)


class HupuAdapter:
    """Fetch only known schedule slugs and numeric box-score identifiers."""

    max_date_slices = 1

    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        max_response_bytes: int = 2_000_000,
        client: Any | None = None,
    ) -> None:
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_response_bytes = max(65_536, int(max_response_bytes))
        self.client = client
        self.calls = 0

    @staticmethod
    def _error(
        kind: ProviderErrorKind,
        message: str,
        *,
        retryable: bool,
        retrieved: datetime | None = None,
    ) -> ProviderResult[Any]:
        return ProviderResult(
            data=None,
            evidence=[],
            partial=False,
            error=ProviderError(kind=kind, retryable=retryable, safe_message=message),
            retrieved_at_utc=retrieved or datetime.now(UTC),
        )

    async def _get(
        self, path: str, budget: RequestBudget
    ) -> tuple[str, datetime] | ProviderResult[Any]:
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            return self._error(
                ProviderErrorKind.AUTH,
                "public data path is not allowed",
                retryable=False,
            )
        url = HUPU_BASE_URL + path
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != HUPU_HOST:
            return self._error(
                ProviderErrorKind.AUTH,
                "public data endpoint is not allowed",
                retryable=False,
            )
        if not budget.reserve_operation():
            return self._error(
                ProviderErrorKind.TIMEOUT,
                "public data deadline exceeded",
                retryable=True,
            )
        remaining = budget.remaining_ms() / 1000
        if remaining <= 0:
            return self._error(
                ProviderErrorKind.TIMEOUT,
                "public data deadline exceeded",
                retryable=True,
            )
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 NBAChat/1.0"},
            follow_redirects=False,
        )
        self.calls += 1
        retrieved = datetime.now(UTC)
        try:
            try:
                response = await client.get(
                    url,
                    timeout=min(self.timeout_seconds, remaining),
                    follow_redirects=False,
                )
            except TypeError:
                response = await client.get(url)
            retrieved = datetime.now(UTC)
            if response.is_redirect:
                return self._error(
                    ProviderErrorKind.AUTH,
                    "unexpected public data redirect",
                    retryable=False,
                    retrieved=retrieved,
                )
            if response.status_code == 404:
                return self._error(
                    ProviderErrorKind.NOT_FOUND,
                    "public game record not found",
                    retryable=False,
                    retrieved=retrieved,
                )
            if response.status_code == 429:
                return self._error(
                    ProviderErrorKind.RATE_LIMITED,
                    "public data source rate limited",
                    retryable=True,
                    retrieved=retrieved,
                )
            if response.status_code in {401, 403}:
                return self._error(
                    ProviderErrorKind.AUTH,
                    "public data source rejected the request",
                    retryable=False,
                    retrieved=retrieved,
                )
            if response.status_code >= 400:
                return self._error(
                    ProviderErrorKind.HTTP,
                    "public data source unavailable",
                    retryable=response.status_code >= 500,
                    retrieved=retrieved,
                )
            content = bytes(response.content)
            if len(content) > self.max_response_bytes:
                return self._error(
                    ProviderErrorKind.SCHEMA_MISMATCH,
                    "public data response exceeded the safe size",
                    retryable=False,
                    retrieved=retrieved,
                )
            return content.decode("utf-8", errors="replace"), retrieved
        except (httpx.TimeoutException, asyncio.TimeoutError):
            return self._error(
                ProviderErrorKind.TIMEOUT,
                "public data source timed out",
                retryable=True,
                retrieved=retrieved,
            )
        except httpx.HTTPError:
            return self._error(
                ProviderErrorKind.HTTP,
                "public data source unavailable",
                retryable=True,
                retrieved=retrieved,
            )
        finally:
            if own_client:
                await client.aclose()

    @staticmethod
    def _evidence(path: str, identifier: str, retrieved: datetime) -> Evidence:
        return Evidence(
            evidence_id=f"hupu:{identifier}",
            source_class=SourceClass.ESTABLISHED_SPORTS,
            source_ref=f"hupu:{identifier}",
            url=HUPU_BASE_URL + path,
            fetched_at_utc=retrieved,
            data_as_of_utc=retrieved,
            trust=TrustLevel.MEDIUM,
            freshness=Freshness.FRESH,
        )

    async def fetch_team_schedule(
        self,
        team_slug: str,
        season: SeasonLabel,
        budget: RequestBudget,
    ) -> ProviderResult[list[Game]]:
        slug = str(team_slug or "").strip().lower()
        if slug not in HUPU_TEAM_SLUGS:
            return self._error(
                ProviderErrorKind.AUTH,
                "team schedule is not allow-listed",
                retryable=False,
            )
        path = f"/schedule/{slug}"
        response = await self._get(path, budget)
        if isinstance(response, ProviderResult):
            return response
        html, retrieved = response
        parser = _TableRows()
        parser.feed(html)
        games: list[Game] = []
        evidence: list[Evidence] = []
        for row in parser.rows:
            team_links = [link for link in row.links if "/teams/" in link[0]]
            game_link = next((link for link in row.links if "/games/boxscore/" in link[0]), None)
            day_text = next((cell for cell in row.cells if _DATE_RE.fullmatch(cell)), None)
            if len(team_links) < 2 or game_link is None or day_text is None:
                continue
            away_slug = team_links[0][0].rstrip("/").split("/")[-1]
            home_slug = team_links[1][0].rstrip("/").split("/")[-1]
            away = _team_from_slug(away_slug)
            home = _team_from_slug(home_slug)
            game_match = re.search(r"/games/boxscore/(\d{1,12})", game_link[0])
            if away is None or home is None or game_match is None:
                continue
            local_start = datetime.strptime(day_text, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=_BEIJING
            )
            if _season_for_day(local_start).label != season.label:
                continue
            score_match = next(
                (
                    re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", cell)
                    for cell in row.cells
                    if re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", cell)
                ),
                None,
            )
            # Schedule table is labelled 客队 vs 主队 and lists score in that
            # same away-home order.
            away_score = int(score_match.group(1)) if score_match else None
            home_score = int(score_match.group(2)) if score_match else None
            status = GameStatus.FINAL if score_match else GameStatus.SCHEDULED
            source_id = game_match.group(1)
            game = Game(
                game_id=f"hupu:{source_id}",
                season=season,
                start_utc=local_start.astimezone(UTC),
                home=home,
                away=away,
                status=status,
                home_score=home_score,
                away_score=away_score,
            )
            games.append(game)
            evidence.append(self._evidence(path, f"schedule:{source_id}", retrieved))
        if not games:
            return self._error(
                ProviderErrorKind.SCHEMA_MISMATCH,
                "public schedule format was not recognized",
                retryable=False,
                retrieved=retrieved,
            )
        return ProviderResult(
            data=games,
            evidence=evidence,
            partial=False,
            retrieved_at_utc=retrieved,
        )

    async def get_game_summary(
        self, game_id: str, budget: RequestBudget
    ) -> ProviderResult[GameBundle]:
        match = _GAME_ID_RE.fullmatch(str(game_id or "").strip())
        if match is None:
            return self._error(
                ProviderErrorKind.AUTH,
                "game identifier is not allowed",
                retryable=False,
            )
        source_id = match.group(1)
        path = f"/games/boxscore/{source_id}"
        response = await self._get(path, budget)
        if isinstance(response, ProviderResult):
            return response
        html, retrieved = response
        parser = _TableRows()
        parser.feed(html)
        game = self._parse_detail_game(source_id, html)
        if game is None:
            return self._error(
                ProviderErrorKind.SCHEMA_MISMATCH,
                "public box score format was not recognized",
                retryable=False,
                retrieved=retrieved,
            )
        evidence = self._evidence(path, f"game:{source_id}", retrieved)
        stats = self._parse_player_stats(parser.rows, game, evidence.evidence_id)
        leaders = sorted(
            stats,
            key=lambda line: int(line.metrics.get("points") or 0),
            reverse=True,
        )[:10]
        return ProviderResult(
            data=GameBundle(game=game, stat_lines=stats, leaders=leaders),
            evidence=[evidence],
            partial=False,
            retrieved_at_utc=retrieved,
        )

    async def get_play_by_play(
        self, game_id: str, budget: RequestBudget
    ) -> ProviderResult[PlayByPlayBundle]:
        """Fetch and type the public chronological text commentary for one game."""

        match = _GAME_ID_RE.fullmatch(str(game_id or "").strip())
        if match is None:
            return self._error(
                ProviderErrorKind.AUTH,
                "game identifier is not allowed",
                retryable=False,
            )
        source_id = match.group(1)
        path = f"/games/playbyplay/{source_id}"
        response = await self._get(path, budget)
        if isinstance(response, ProviderResult):
            return response
        html, retrieved = response
        parser = _TableRows()
        parser.feed(html)
        events: list[PlayEvent] = []
        period = 1
        previous_away_score: int | None = None
        previous_home_score: int | None = None
        for row in parser.rows:
            if len(row.cells) < 4:
                continue
            clock = _clock_seconds(row.cells[0])
            score_match = _SCORE_RE.fullmatch(" ".join(row.cells[3].split()))
            action_text = _plain_action_text(row.cells[2])
            if clock is None or score_match is None or action_text is None:
                continue
            period_match = _PERIOD_RE.search(action_text)
            overtime_match = _OVERTIME_RE.search(action_text)
            if period_match is not None:
                period = _numbered_period(period_match.group(1))
            elif overtime_match is not None:
                overtime_number = overtime_match.group(1)
                period = 4 + (
                    _numbered_period(overtime_number) if overtime_number is not None else 1
                )

            away_score = int(score_match.group(1))
            home_score = int(score_match.group(2))
            event_type = _play_event_type(action_text)
            points = None
            if event_type in {PlayEventType.SHOT, PlayEventType.FREE_THROW}:
                score_deltas = []
                if previous_away_score is not None:
                    score_deltas.append(away_score - previous_away_score)
                if previous_home_score is not None:
                    score_deltas.append(home_score - previous_home_score)
                positive = [value for value in score_deltas if 1 <= value <= 4]
                # A miss remains unknown/null rather than being rewritten as a
                # made zero-point shot.  This also keeps the existing replay
                # renderer from labelling a miss as "命中".
                missed = any(token in action_text for token in ("不中", "不进", "未中", "打铁"))
                if not missed:
                    if event_type is PlayEventType.FREE_THROW:
                        points = 1 if positive else None
                    elif "三分" in action_text or "3分" in action_text:
                        points = 3 if positive else None
                    elif any(token in action_text for token in ("两分", "二分")):
                        points = 2 if positive else None
                    elif positive:
                        points = max(positive)
            previous_away_score = away_score
            previous_home_score = home_score
            provider_index = len(events)
            row_identifier = (
                row.row_id
                if row.row_id and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", row.row_id)
                else str(provider_index)
            )
            try:
                events.append(
                    PlayEvent(
                        event_id=f"hupu:{source_id}:play:{row_identifier}",
                        game_id=f"hupu:{source_id}",
                        sequence=provider_index,
                        provider_index=provider_index,
                        period=period,
                        clock_seconds_remaining=clock,
                        event_type=event_type,
                        shooter=(
                            (
                                _free_throw_participant(action_text)
                                if event_type is PlayEventType.FREE_THROW
                                else _shot_participant(action_text)
                            )
                            if event_type in {PlayEventType.SHOT, PlayEventType.FREE_THROW}
                            else None
                        ),
                        assister=(
                            _participant(action_text, assister=True)
                            if event_type is PlayEventType.SHOT
                            else None
                        ),
                        shot_type=_shot_type(action_text, event_type),
                        points=points,
                        # Hupu labels this column as away-team score followed
                        # by home-team score, matching its team_a/team_b header.
                        home_score_after=home_score,
                        away_score_after=away_score,
                        action_text=action_text,
                        raw_text_hash=hashlib.sha256(action_text.encode("utf-8")).hexdigest()[:32],
                    )
                )
            except ValueError:
                # One malformed public row must not discard the remaining
                # chronological record.  The returned bundle is marked partial
                # through sequence_valid below.
                continue
        if not events:
            return self._error(
                ProviderErrorKind.SCHEMA_MISMATCH,
                "public play-by-play format was not recognized",
                retryable=False,
                retrieved=retrieved,
            )
        sequence_valid = all(
            event.sequence == index and event.provider_index == index
            for index, event in enumerate(events)
        )
        return ProviderResult(
            data=PlayByPlayBundle(
                game_id=f"hupu:{source_id}",
                events=events,
                sequence_valid=sequence_valid,
            ),
            evidence=[self._evidence(path, f"playbyplay:{source_id}", retrieved)],
            partial=not sequence_valid,
            retrieved_at_utc=retrieved,
        )

    @staticmethod
    def _parse_detail_game(source_id: str, html: str) -> Game | None:
        sides: dict[str, tuple[EntityRef, int]] = {}
        for side_name, key in (("team_a", "away"), ("team_b", "home")):
            match = re.search(
                rf'<div class="{side_name}">.*?<h2>\s*(\d+)\s*</h2>.*?'
                rf'href="https://nba\.hupu\.com/teams/([^"/]+)"',
                html,
                re.DOTALL,
            )
            if match is None:
                return None
            team = _team_from_slug(match.group(2))
            if team is None:
                return None
            sides[key] = (team, int(match.group(1)))
        start_match = re.search(
            r'class="time_f"[^>]*>\s*开赛：\s*(\d{4})年(\d{2})月(\d{2})日\s*'
            r"(\d{2}):(\d{2})",
            html,
        )
        if start_match is None:
            return None
        local_start = datetime(*(int(value) for value in start_match.groups()), tzinfo=_BEIJING)
        duration = None
        duration_match = re.search(
            r'class="consumTime"[^>]*>\s*耗时：\s*(\d{1,2}):(\d{2})', html
        )
        if duration_match:
            duration = int(duration_match.group(1)) * 3600 + int(duration_match.group(2)) * 60
        venue = None
        venue_match = re.search(r'class="arena"[^>]*>\s*球馆：\s*([^<]+)', html)
        if venue_match and venue_match.group(1).strip():
            venue = Venue(name=" ".join(venue_match.group(1).split()))
        attendance = None
        attendance_match = re.search(r'class="peopleNum"[^>]*>\s*上座：\s*([\d,]+)人', html)
        if attendance_match:
            attendance = int(attendance_match.group(1).replace(",", ""))
        away, away_score = sides["away"]
        home, home_score = sides["home"]
        return Game(
            game_id=f"hupu:{source_id}",
            season=_season_for_day(local_start),
            start_utc=local_start.astimezone(UTC),
            home=home,
            away=away,
            status=GameStatus.FINAL,
            home_score=home_score,
            away_score=away_score,
            venue=venue,
            duration_seconds=duration,
            attendance=attendance,
        )

    @staticmethod
    def _parse_player_stats(
        rows: list[_Row], game: Game, evidence_id: str
    ) -> list[StatLine]:
        stats: list[StatLine] = []
        for row in rows:
            if row.table_id not in {"J_away_content", "J_home_content"}:
                continue
            player_link = next((link for link in row.links if "/players/" in link[0]), None)
            if player_link is None or len(row.cells) < 15:
                continue
            slug = player_link[0].rstrip("/").split("/")[-1].removesuffix(".html")
            name = " ".join(player_link[1].split())
            if not slug or not name:
                continue
            field_goals = _shooting(row.cells[3])
            threes = _shooting(row.cells[4])
            free_throws = _shooting(row.cells[5])
            metrics = {
                "minutes": _number(row.cells[2]),
                "field_goals_made": field_goals[0],
                "field_goals_attempted": field_goals[1],
                "three_points_made": threes[0],
                "three_points_attempted": threes[1],
                "free_throws_made": free_throws[0],
                "free_throws_attempted": free_throws[1],
                "offensive_rebounds": _number(row.cells[6]),
                "defensive_rebounds": _number(row.cells[7]),
                "rebounds": _number(row.cells[8]),
                "assists": _number(row.cells[9]),
                "fouls": _number(row.cells[10]),
                "steals": _number(row.cells[11]),
                "turnovers": _number(row.cells[12]),
                "blocks": _number(row.cells[13]),
                "points": _number(row.cells[14]),
                "plus_minus": _number(row.cells[15]) if len(row.cells) > 15 else None,
            }
            stats.append(
                StatLine(
                    subject=_PLAYER_BY_ID.get(_HUPU_PLAYER_IDS.get(slug, ""))
                    or EntityRef(
                        kind=EntityKind.PLAYER,
                        canonical_id=slug[:128],
                        display_name=name[:200],
                    ),
                    game_id=game.game_id,
                    scope=StatScope.GAME,
                    metrics=metrics,
                    evidence_ids=[evidence_id],
                )
            )
        return stats


__all__ = ["HUPU_TEAM_SLUGS", "HupuAdapter"]
