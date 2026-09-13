"""Natural-language parsing and session-context resolution for flower queries."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from apps.api.src.domain.flower import (
    ContainerInfo,
    ContainerType,
    FlowerIntentName,
    FlowerParseResult,
    GardenContext,
    LightLevel,
    PlantMatch,
    PlantProfile,
    SafetyNotice,
)

from .flower_knowledge import DEFAULT_KNOWLEDGE_BASE, FlowerKnowledgeBase
from .flower_safety import FlowerSafetyGuard

_PRONOUN_RE = re.compile(
    r"(?:它|这盆(?:花)?|这株(?:花)?|刚才(?:那盆|那个|这盆)?|"
    r"我的(?:花|植物)?|那盆(?:花)?|这个(?:花|植物)?)"
)
_FLOWER_NOUN_RE = re.compile(
    r"(?:花|植物|盆栽|园艺|种植|养护|浇水|施肥|光照|土壤|换盆|修剪|扦插|繁殖|病虫|黄叶|萎蔫|花苞|开花|季节|阳台|庭院|花盆|天气|温度|湿度|降雨|阴雨|霜冻|寒潮|高温|台风)",
    re.IGNORECASE,
)

_SELECTION_RE = re.compile(
    r"(?:适合种|种什么|养什么|推荐(?:种|养|花|植物|品种)?|"
    r"(?:花卉|植物|品种)(?:推荐|怎么选)|选什么|哪种花|哪类植物|"
    r"适宜(?:种植|养)|适合我的|买什么(?:花|植物)?|想养(?:什么|哪)|"
    r"新手(?:养什么|适合养)|耐阴(?:植物|花卉)?(?:推荐)?|"
    r"室内(?:植物|花卉)?(?:推荐)?|阳台(?:植物|花卉)?(?:推荐)?)",
    re.IGNORECASE,
)
_WATER_RE = re.compile(r"(?:浇水|浇几次|多久浇|干湿|缺水|补水|水量|浇透)", re.IGNORECASE)
_LIGHT_RE = re.compile(r"(?:光照|日照|晒|阳台朝|阴凉|遮阴|暴晒|散射光|直射光)", re.IGNORECASE)
_SOIL_RE = re.compile(r"(?:土壤|基质|配土|换盆|盆土|排水|透气|酸碱|pH)", re.IGNORECASE)
_FEED_RE = re.compile(r"(?:施肥|肥料|营养液|追肥|底肥|氮肥|磷肥|钾肥)", re.IGNORECASE)
_PRUNE_RE = re.compile(r"(?:修剪|剪枝|打顶|摘心|残花|整形)", re.IGNORECASE)
_PROPAGATE_RE = re.compile(r"(?:扦插|繁殖|分株|播种|压条|嫁接|育苗)", re.IGNORECASE)
_PEST_RE = re.compile(
    r"(?:病虫|虫害|杀虫|白粉|黑斑|叶斑|霉|烂根|黄叶|叶子发黄|萎蔫|掉叶|花苞掉|虫子|蚜虫|红蜘蛛|介壳)",
    re.IGNORECASE,
)
_SEASON_RE = re.compile(
    r"(?:春|夏|秋|冬)(?:季)?|(?:1[0-2]|[1-9])\s*月|季度|季节|全年|入冬|过冬",
    re.IGNORECASE,
)
_IDENTIFY_RE = re.compile(
    r"(?:这是什么花|叫什么花|品种|辨认|识别|认不出|是什么植物)",
    re.IGNORECASE,
)
_WEATHER_RE = re.compile(
    r"(?:天气|温度|湿度|降雨|下雨|阴雨|晴天|高温|低温|霜冻|寒潮|台风|暴雨|风大|气温|气候)",
    re.IGNORECASE,
)
_COMPARISON_RE = re.compile(
    r"(?:比较|对比|区别|差别|哪个好|怎么选|哪个更|分别|和|与|还是)",
    re.IGNORECASE,
)

_CITY_RE = re.compile(
    r"(?:在|位于|坐标|地点是|地区是)\s*([\u4e00-\u9fff]{2,16}(?:省|市|区|县|自治区|特别行政区)?)"
)
_KNOWN_CITIES = (
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "南京",
    "苏州",
    "成都",
    "重庆",
    "武汉",
    "西安",
    "天津",
    "青岛",
    "厦门",
    "昆明",
    "香港",
    "澳门",
    "台北",
)
_LOCATION_RE = re.compile(
    r"(?:北|南|东|西|东北|西北|东南|西南)?(?:阳台|露台|窗台|庭院|院子|室内|客厅|卧室|花园|办公室)"
)
_OBSERVATION_TERMS = (
    "黄叶",
    "叶子发黄",
    "萎蔫",
    "掉叶",
    "黑斑",
    "白粉",
    "叶斑",
    "烂根",
    "根腐",
    "徒长",
    "花苞掉",
    "不开花",
    "虫子",
    "蚜虫",
    "红蜘蛛",
    "介壳虫",
    "叶片卷曲",
    "晒伤",
)


def _norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _contains_pronoun(text: str) -> bool:
    return _PRONOUN_RE.search(text) is not None


def _extract_location(text: str) -> str | None:
    # Keep only a broad city/region or room/balcony label.  Do not retain an
    # apartment number, street or other precise address in session context.
    # Prefer a known city anywhere in the sentence.  A greedy “在……养花，北
    # 阳台……” match must not swallow the city together with the plant phrase.
    for city in _KNOWN_CITIES:
        if city in text:
            return city
    for match in _CITY_RE.finditer(text):
        value = match.group(1).strip()
        value = re.split(r"[，。；,;\s]", value, maxsplit=1)[0]
        if value:
            for suffix in ("省", "市", "自治区", "特别行政区", "区", "县"):
                if value.endswith(suffix) and len(value) > len(suffix):
                    return value
            if value in _KNOWN_CITIES:
                return value
    match = _LOCATION_RE.search(text)
    return match.group(0) if match else None


def _extract_climate(text: str) -> str | None:
    for term in (
        "湿热",
        "干燥",
        "寒冷",
        "温暖",
        "沿海",
        "高原",
        "多雨",
        "少雨",
        "梅雨",
        "北方",
        "南方",
    ):
        if term in text:
            return term
    return None


def _extract_light(text: str) -> LightLevel | str | None:
    value = _norm(text)
    # Directional balconies are useful context but are not themselves a claim
    # about exact lux; retain the original phrase for the model.
    if re.search(r"(?:全日照|全天(?:直射)?|充足直射|南向阳台|南阳台|暴晒)", value):
        return LightLevel.FULL_SUN
    if re.search(r"(?:半日照|上午(?:直射|有太阳)?|东向阳台|西向阳台|东阳台|西阳台)", value):
        return LightLevel.PARTIAL_SUN
    if re.search(r"(?:散射光|明亮(?:散射)?|北向阳台|北阳台|窗边明亮)", value):
        return LightLevel.BRIGHT_INDIRECT
    if re.search(r"(?:阴凉|背阴|少光|阴暗|无光|耐阴|低光)", value):
        return LightLevel.SHADE
    return None


def _extract_container(text: str) -> ContainerInfo | str | None:
    value = _norm(text)
    if re.search(r"(?:地栽|地里|庭院种)", value):
        return ContainerInfo(type=ContainerType.GROUND)
    diameter = re.search(r"(?:直径|口径|盆径)\s*(\d{1,3}(?:\.\d+)?)\s*(?:厘米|cm)?", value)
    if "水培" in value:
        return ContainerInfo(type=ContainerType.HYDROPONIC)
    if "长条花箱" in value or "窗台花箱" in value:
        return ContainerInfo(type=ContainerType.WINDOW_BOX)
    if diameter:
        return ContainerInfo(
            type=ContainerType.POT,
            diameter_cm=float(diameter.group(1)),
        )
    if "花盆" in value or "盆栽" in value or "盆" in value:
        return ContainerInfo(type=ContainerType.POT)
    return None


def _extract_season(text: str) -> str | None:
    value = _norm(text)
    month = re.search(r"(1[0-2]|[1-9])\s*月", value)
    if month:
        return f"{month.group(1)}月"
    for season in ("春季", "夏季", "秋季", "冬季", "春", "夏", "秋", "冬", "梅雨", "过冬"):
        if season in value:
            return season
    return None


def _extract_observations(text: str) -> list[str]:
    value = str(text or "")
    return [term for term in _OBSERVATION_TERMS if term in value][:8]


def _has_flower_signal(text: str) -> bool:
    return _FLOWER_NOUN_RE.search(text) is not None


def _classify_intent(
    text: str,
    *,
    safety: SafetyNotice | None,
    plant_match: PlantMatch | None = None,
    contextual_reference: bool = False,
) -> tuple[FlowerIntentName, float]:
    if safety is not None:
        return FlowerIntentName.SAFETY, 1.0
    value = _norm(text)
    # Weather/current-condition questions are useful gardening requests even
    # when the user has not named a plant yet (for example, “上海最近天气怎样”
    # before deciding what to grow).  Treat them as a garden turn so the
    # application can request only the missing city instead of replying with a
    # generic out-of-scope message.  Specific care intents below still win when
    # a user asks how that weather affects watering or pests.
    has_weather_signal = _WEATHER_RE.search(value) is not None
    has_selection_signal = _SELECTION_RE.search(value) is not None
    if (
        not _has_flower_signal(value)
        and plant_match is None
        and not contextual_reference
        and not has_weather_signal
        and not has_selection_signal
    ):
        return FlowerIntentName.OUT_OF_SCOPE, 0.92
    # More specific requests win when they contain several care words.
    if _SELECTION_RE.search(value):
        return FlowerIntentName.PLANT_SELECTION, 0.95
    if _PEST_RE.search(value):
        return FlowerIntentName.PEST_DISEASE, 0.93
    if _IDENTIFY_RE.search(value):
        return FlowerIntentName.IDENTIFICATION, 0.9
    if _WATER_RE.search(value):
        return FlowerIntentName.WATERING, 0.95
    if _LIGHT_RE.search(value):
        return FlowerIntentName.LIGHT, 0.94
    if _SOIL_RE.search(value):
        return FlowerIntentName.SOIL, 0.94
    if _FEED_RE.search(value):
        return FlowerIntentName.FERTILIZING, 0.94
    if _PRUNE_RE.search(value):
        return FlowerIntentName.PRUNING, 0.94
    if _PROPAGATE_RE.search(value):
        return FlowerIntentName.PROPAGATION, 0.94
    if _SEASON_RE.search(value):
        return FlowerIntentName.SEASONAL_PLAN, 0.9
    if has_weather_signal:
        return FlowerIntentName.SEASONAL_PLAN, 0.86
    return FlowerIntentName.GENERAL_CARE, 0.72


class FlowerParser:
    """Parse a turn and resolve deictic plant references from a context."""

    def __init__(
        self,
        knowledge_base: FlowerKnowledgeBase | None = None,
        safety_guard: FlowerSafetyGuard | None = None,
    ) -> None:
        self.knowledge_base = knowledge_base or DEFAULT_KNOWLEDGE_BASE
        self.safety_guard = safety_guard or FlowerSafetyGuard()

    def parse(
        self,
        message: str,
        *,
        context: GardenContext | None = None,
    ) -> FlowerParseResult:
        text = str(message or "").strip()
        if not text:
            raise ValueError("message must not be blank")
        match = self.knowledge_base.detect(text)
        safety = self.safety_guard.classify(text)
        intent_name, confidence = _classify_intent(
            text,
            safety=safety,
            plant_match=match,
            contextual_reference=(context is not None and _contains_pronoun(text)),
        )
        resolved: PlantProfile | None = match.profile if match and not match.ambiguous else None
        used_context = False
        pronoun = _contains_pronoun(text)
        # An explicit known name always wins.  Ambiguous names remain unresolved
        # even when a previous context exists, so the user can choose safely.
        if resolved is None and not (match and match.ambiguous) and context is not None:
            previous = context.plant_profile
            if previous is None and context.plant_name:
                previous = self.knowledge_base.get(context.plant_name)
            # Do not attach a stale plant to an identity/out-of-scope turn.  A
            # bare follow-up without an explicit plant may still inherit the
            # context when it contains a gardening signal (or a clear pronoun).
            can_inherit = pronoun or (
                match is None
                and intent_name
                not in {
                    FlowerIntentName.OUT_OF_SCOPE,
                    # A recommendation/identification request without an
                    # explicit plant must not silently turn into care advice
                    # for a stale plant from an earlier turn.
                    FlowerIntentName.PLANT_SELECTION,
                    FlowerIntentName.IDENTIFICATION,
                }
                and _has_flower_signal(text)
            )
            if previous is not None and can_inherit:
                resolved = previous
                used_context = True

        location = _extract_location(text)
        climate = _extract_climate(text)
        light = _extract_light(text)
        container = _extract_container(text)
        season = _extract_season(text)
        observations = _extract_observations(text)
        missing: list[str] = []
        clarification: str | None = None
        is_weather_question = _WEATHER_RE.search(text) is not None
        if safety is not None:
            clarification = safety.message
        elif is_weather_question and location is None and context is not None and context.location:
            # A weather follow-up may omit the city after it was supplied in a
            # previous turn.  Keep the inherited coarse location explicit in
            # the parsed result so callers can construct a search query without
            # asking the user to repeat it.
            location = context.location
        elif is_weather_question and location is None:
            missing.append("location")
            clarification = (
                "要查当前天气或降雨，请先告诉我所在城市或地区；"
                "在此之前可按盆土干湿和叶片状态调整浇水。"
            )
        elif is_weather_question:
            # A city/region is enough to answer a weather-oriented gardening
            # question; do not force the user to name a plant as an unrelated
            # prerequisite.
            pass
        elif intent_name in {
            FlowerIntentName.WATERING,
            FlowerIntentName.LIGHT,
            FlowerIntentName.SOIL,
            FlowerIntentName.FERTILIZING,
            FlowerIntentName.PRUNING,
            FlowerIntentName.PROPAGATION,
            FlowerIntentName.PEST_DISEASE,
            FlowerIntentName.SEASONAL_PLAN,
            FlowerIntentName.GENERAL_CARE,
        } and resolved is None:
            if match is not None and match.ambiguous:
                missing.append("plant_identity")
                names = "、".join(item.canonical_name for item in match.candidates[:4])
                clarification = f"你提到的名称可能对应多个花卉（{names}），请确认具体是哪一种。"
            else:
                missing.append("plant")
                clarification = (
                    "请告诉我花卉名称；如果不确定，可描述叶片、花朵和生长环境，"
                    "我先给低风险排查建议。"
                )
        elif intent_name is FlowerIntentName.PLANT_SELECTION:
            # Selection can be answered without a plant, but light/location are
            # materially useful.  Ask for at most one compact missing detail.
            if light is None and location is None:
                missing.append("light_or_location")
                clarification = (
                    "可先告诉我光照（如北阳台/每天几小时直射）或所在城市，"
                    "我会按条件推荐。"
                )
        elif intent_name is FlowerIntentName.IDENTIFICATION and match is None:
            missing.append("plant_description")
            clarification = "仅凭文字还不能确认品种；请补充叶形、花色、花期和光照环境。"

        if intent_name is FlowerIntentName.OUT_OF_SCOPE:
            clarification = (
                "我是种花 Agent，可以帮你选花、安排浇水施肥、处理常见病虫害"
                "和制定季节养护计划。"
            )

        return FlowerParseResult(
            intent_name=intent_name,
            original_text=text,
            plant_match=match,
            resolved_plant=resolved,
            used_context_reference=used_context,
            location=location,
            climate=climate,
            light=light,
            container=container,
            season=season,
            observations=observations,
            missing_slots=missing,
            clarification=clarification,
            confidence=(
                confidence
                if resolved is not None or match is None
                else min(confidence, 0.8)
            ),
            safety_notice=safety,
        )

    def update_context(
        self,
        context: GardenContext | None,
        parsed: FlowerParseResult,
        *,
        session_id: Any | None = None,
        now: datetime | None = None,
    ) -> GardenContext:
        """Merge only confirmed slots into a bounded session context."""

        base = context or GardenContext(session_id=session_id)
        updates: dict[str, Any] = {}
        if parsed.resolved_plant is not None:
            updates["plant"] = parsed.resolved_plant
        elif parsed.plant_match is not None and parsed.plant_match.profile is not None:
            updates["plant"] = parsed.plant_match.profile
        for name in ("location", "climate", "light", "container", "season"):
            value = getattr(parsed, name)
            if value is not None:
                updates[name] = value
        return base.remember(
            observation="、".join(parsed.observations) if parsed.observations else None,
            question=parsed.original_text,
            now=now or datetime.now(UTC),
            **updates,
        )

    def parse_and_update(
        self,
        message: str,
        *,
        context: GardenContext | None = None,
        session_id: Any | None = None,
        now: datetime | None = None,
    ) -> tuple[FlowerParseResult, GardenContext]:
        parsed = self.parse(message, context=context)
        updated = self.update_context(context, parsed, session_id=session_id, now=now)
        return parsed, updated


DEFAULT_FLOWER_PARSER = FlowerParser()


def parse_flower_query(
    message: str,
    *,
    context: GardenContext | None = None,
    parser: FlowerParser | None = None,
) -> FlowerParseResult:
    return (parser or DEFAULT_FLOWER_PARSER).parse(message, context=context)


def resolve_flower_context(
    context: GardenContext | None,
    parsed: FlowerParseResult,
    *,
    parser: FlowerParser | None = None,
    session_id: Any | None = None,
    now: datetime | None = None,
) -> GardenContext:
    return (parser or DEFAULT_FLOWER_PARSER).update_context(
        context,
        parsed,
        session_id=session_id,
        now=now,
    )


__all__ = [
    "DEFAULT_FLOWER_PARSER",
    "FlowerParser",
    "parse_flower_query",
    "resolve_flower_context",
]
