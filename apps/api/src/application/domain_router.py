"""Composition boundary for the shipped product domain.

The repository still contains the original NBA vertical because a few links and
evaluation fixtures use it.  That does not mean the old vertical should remain
the default experience.  ``DomainRouter`` keeps the public HTTP/SSE contract
unchanged while selecting the flower assistant for ordinary requests and only
delegating to NBA when the request is explicitly sports-scoped (or carries a
server-authorised selected game id).

The router deliberately exposes a small set of compatibility attributes.  The
highlights/health routes predate the domain split and read ``gateway``,
``session_store`` and ``clock`` from ``app.state.chat_use_case``.  Proxying
those attributes keeps those routes working without making them aware of the
flower implementation or allowing a browser to choose a provider.
"""

from __future__ import annotations

import re
import time
from typing import Any

from apps.api.src.application.flower_chat_use_case import FlowerChatUseCase
from apps.api.src.domain.models import Game

# Deliberately explicit rather than a broad English/Chinese token matcher.  A
# generic question such as ``怎么照顾玫瑰`` must stay in the flower domain, while
# historical NBA links and selected scoreboard cards continue to work.
_NBA_SCOPE_RE = re.compile(
    # ``\b`` treats adjacent CJK characters as word characters in Python, so
    # it misses natural input such as ``NBA新闻`` or ``nba总决赛``.  Use an
    # ASCII-only boundary for the brand token; Chinese text may follow it
    # without a separating space.  The same boundary is applied to English
    # team aliases below.
    r"(?:(?<![A-Za-z0-9])nba(?![A-Za-z0-9])|篮球|比赛|赛事|赛程|赛果|球队|球员|比分|得分|篮板|助攻|总决赛|季后赛|"
    r"系列赛|回合|投篮|教练|主帅|场馆|球馆|挡拆|联防|战术|复盘|勇士|湖人|凯尔特人|"
    r"雷霆|掘金|尼克斯|马刺|快船|太阳|独行侠|火箭|森林狼|骑士|热火|雄鹿|76人|"
    r"公牛|篮网|老鹰|活塞|步行者|猛龙|灰熊|鹈鹕|国王|开拓者|爵士|黄蜂|奇才|"
    r"马刺|勇士|湖人|(?<![A-Za-z0-9])(?:celtics|thunder|nuggets|warriors|lakers|spurs|knicks)(?![A-Za-z0-9]))",
    re.IGNORECASE,
)

# A gardening signal wins when a sentence happens to contain a broad sports
# word (for example, “花园里的比赛活动”).  Keeping this matcher separate from
# the NBA compatibility matcher makes the precedence explicit and prevents a
# generic flower question from crossing into the legacy sports application.
_FLOWER_SCOPE_RE = re.compile(
    r"(?:花卉|花园|植物|园艺|种花|种植|养护|浇水|施肥|光照|土壤|换盆|修剪|扦插|繁殖|"
    r"病虫|黄叶|萎蔫|花苞|开花|阳台|庭院|花盆|天气|温度|湿度|降雨|气候|霜冻|高温|低温|"
    r"月季|玫瑰|绣球|蝴蝶兰|兰花|长寿花|三角梅|栀子|茉莉|薰衣草|君子兰|绿萝|矮牵牛|太阳花|"
    r"牡丹|菊花|茶花|杜鹃|多肉)",
    re.IGNORECASE,
)

_PRODUCT_META_RE = re.compile(
    r"^(?:你好|您好|嗨|哈喽|hello|hi|hey|你是谁|你是誰|你叫什么|你是什么助手|"
    r"你能做什么|你会什么|你可以做什么|介绍一下你自己)[!！,.，。?？\s]*$",
    re.IGNORECASE,
)


class DomainRouter:
    """Route one request to a closed, server-owned application vertical."""

    def __init__(
        self,
        flower_usecase: FlowerChatUseCase,
        legacy_usecase: Any,
        *,
        default_domain: str = "flower",
    ) -> None:
        self.flower_usecase = flower_usecase
        self.legacy_usecase = legacy_usecase
        value = str(default_domain or "flower").strip().casefold()
        self.default_domain = "nba" if value in {"nba", "basketball"} else "flower"

        # A logical browser session keeps its domain for ambiguous follow-ups
        # (“为什么赢”“我问了几个问题”).  Entries contain no transcript or
        # user text and are bounded/TTL'd just like the underlying session
        # stores.  Explicit flower/NBA wording can intentionally switch it.
        settings = getattr(flower_usecase, "settings", None)
        self._session_domain_ttl = max(
            60.0, float(getattr(settings, "session_ttl_seconds", 86_400))
        )
        self._session_domain_max = max(
            128, int(getattr(settings, "max_session_entries", 10_000))
        )
        self._session_domains: dict[str, tuple[float, str]] = {}

        # Attributes consumed by the pre-domain-split highlights and health
        # routes.  They intentionally point at the legacy sports gateway: the
        # left rail is a sports-only compatibility surface and must not try to
        # call a flower search provider for ``search_games``.
        self.gateway = getattr(legacy_usecase, "gateway", None)
        self.provider = getattr(legacy_usecase, "provider", None)
        self.fallback_provider = getattr(legacy_usecase, "fallback_provider", None)
        self.session_store = getattr(flower_usecase, "session_store", None)
        self.clock = getattr(flower_usecase, "clock", None)
        self.hermes_runtime = getattr(flower_usecase, "hermes_runtime", None)
        self.agent_runtime = getattr(flower_usecase, "agent_runtime", None)
        self.game_registry = getattr(legacy_usecase, "game_registry", None)
        self.game_origin_registry = getattr(legacy_usecase, "game_origin_registry", None)

    @staticmethod
    def _request_value(request: Any, name: str, default: Any = None) -> Any:
        if isinstance(request, dict):
            return request.get(name, default)
        return getattr(request, name, default)

    def select(self, request: Any) -> Any:
        """Return the application use case for a validated request.

        ``selected_game_id`` is not trusted as a game record here; it is only a
        routing hint.  The legacy use case still resolves it against its
        server-owned registry before using it as context.  Explicit NBA words
        are likewise only a compatibility escape hatch and cannot enable any
        additional tools.
        """

        if self.default_domain == "nba":
            return self.legacy_usecase
        selected_game = self._request_value(request, "selected_game_id")
        message = str(self._request_value(request, "message", "") or "")
        # Product identity/greeting questions always remain in the shipped
        # flower experience, even when the same session previously queried the
        # legacy sports compatibility route.
        if _PRODUCT_META_RE.fullmatch(message):
            return self.flower_usecase
        # Explicit flower vocabulary takes precedence over broad words such as
        # “比赛” or “教练”.  A clearly sports-scoped sentence without a flower
        # signal remains an intentional compatibility escape hatch.
        if _FLOWER_SCOPE_RE.search(message):
            return self.flower_usecase
        if _NBA_SCOPE_RE.search(message):
            return self.legacy_usecase
        # A client-provided id is only a routing hint after the legacy use case
        # resolves it against a server-owned registry.  Treating any non-empty
        # id as authoritative made a stray/stale flower-page value switch the
        # entire request to the NBA path (and allowed guessed fixture ids to
        # influence routing).
        if self._authorised_selected_game(selected_game):
            return self.legacy_usecase
        pinned = self._session_domain(request)
        if pinned == "nba":
            return self.legacy_usecase
        return self.flower_usecase

    def _authorised_selected_game(self, value: Any) -> bool:
        """Check a selected-card id using only server-owned game state.

        The browser sends an opaque id, never a score/team payload.  Prefer the
        legacy use case's existing resolver because it enforces fixture/live
        origin rules; the small registry check is retained for embedding
        doubles that do not expose that private helper.  Resolver failures are
        fail-closed and must not make a normal flower turn fail.
        """

        if value is None:
            return False
        candidate = str(value).strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate):
            return False

        resolver = getattr(self.legacy_usecase, "_selected_game", None)
        if callable(resolver):
            try:
                resolved = resolver(candidate)
            except Exception:
                resolved = None
            if isinstance(resolved, Game) and resolved.game_id == candidate:
                return True

        registry = getattr(self.legacy_usecase, "game_registry", None)
        if not isinstance(registry, dict):
            try:
                registry = dict(registry or {})
            except (TypeError, ValueError):
                registry = {}
        game = registry.get(candidate)
        if not isinstance(game, Game) or game.game_id != candidate:
            return False
        origins = getattr(self.legacy_usecase, "game_origin_registry", None)
        if not isinstance(origins, dict):
            try:
                origins = dict(origins or {})
            except (TypeError, ValueError):
                origins = {}
        origin = str(origins.get(candidate, "none")).strip().casefold()
        # A registered card is trusted only when its source is explicit.  This
        # mirrors ChatUseCase's selected-game authorization and avoids treating
        # an unlabelled/guessed fixture row as a user-selected replay.
        return origin in {"public", "demo_snapshot"}

    @staticmethod
    def _session_key(value: Any) -> str | None:
        """Return a bounded opaque session key without trusting its contents."""

        if value is None:
            return None
        candidate = str(value).strip()
        if not 1 <= len(candidate) <= 128 or re.search(r"[\x00-\x1f\x7f]", candidate):
            return None
        return candidate

    def _request_session_key(self, request: Any) -> str | None:
        return self._session_key(self._request_value(request, "session_id"))

    def _session_domain(self, request: Any) -> str | None:
        key = self._request_session_key(request)
        if key is None:
            return None
        now = time.monotonic()
        entry = self._session_domains.get(key)
        if entry is None:
            return None
        expires_at, domain = entry
        if expires_at <= now or domain not in {"flower", "nba"}:
            self._session_domains.pop(key, None)
            return None
        return domain

    def _remember_session_domain(self, request: Any, target: Any, result: Any) -> None:
        if self.default_domain == "nba":
            return
        key = self._request_session_key(request)
        if key is None:
            result_key = (
                result.get("session_id")
                if isinstance(result, dict)
                else getattr(result, "session_id", None)
            )
            key = self._session_key(result_key)
        if key is None:
            return
        domain = "nba" if target is self.legacy_usecase else "flower"
        now = time.monotonic()
        # Opportunistic expiry keeps this map bounded even when a client never
        # sends another request for an old session.
        for stale, (expires_at, _value) in list(self._session_domains.items()):
            if expires_at <= now:
                self._session_domains.pop(stale, None)
        if (
            len(self._session_domains) >= self._session_domain_max
            and key not in self._session_domains
        ):
            oldest = min(self._session_domains, key=lambda item: self._session_domains[item][0])
            self._session_domains.pop(oldest, None)
        self._session_domains[key] = (now + self._session_domain_ttl, domain)

    async def handle(self, request: Any, **kwargs: Any):
        target = self.select(request)
        result = await target.handle(request, **kwargs)
        self._remember_session_domain(request, target, result)
        return result

    def __getattr__(self, name: str) -> Any:
        # Keep uncommon compatibility attributes available to embedding code,
        # but never silently fall back between domain methods (``handle`` is
        # explicit above).  Missing attributes still raise normally.
        for target in (self.flower_usecase, self.legacy_usecase):
            try:
                return getattr(target, name)
            except AttributeError:
                continue
        raise AttributeError(name)


__all__ = ["DomainRouter"]
