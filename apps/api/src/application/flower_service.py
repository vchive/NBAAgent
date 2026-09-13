"""Safe deterministic flower fallback used by the chat orchestration layer.

Full-intelligence requests should normally be phrased by the configured model.
This service is intentionally still complete enough to answer common questions
offline, and gives that runtime a typed plant/context/care-plan foundation.  It
never performs network access and never emits private implementation details.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from apps.api.src.domain.flower import (
    CarePlan,
    ContainerInfo,
    FlowerIntentName,
    FlowerParseResult,
    FlowerTurn,
    GardenContext,
    LightLevel,
    PlantProfile,
    SafetyNotice,
)

from .flower_knowledge import DEFAULT_KNOWLEDGE_BASE, FlowerKnowledgeBase
from .flower_parser import DEFAULT_FLOWER_PARSER, FlowerParser

_IDENTITY_RE = re.compile(
    r"^(?:你是谁(?:呀|啊|呢)?|你叫什么(?:名字)?|你是什么(?:助手|agent|ai)|你是做什么的|"
    r"你能做什么|你会什么|你可以做什么|介绍一下你自己|"
    r"who\s+are\s+you|what\s+can\s+you\s+do)[？?!！。\s]*$",
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


def _compact_text(value: str) -> str:
    """Normalise a query for catalogue alias containment checks."""

    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _profiles_mentioned(
    message: str,
    knowledge_base: FlowerKnowledgeBase,
) -> list[PlantProfile]:
    """Return every unambiguous catalogue plant explicitly named in a query.

    ``FlowerKnowledgeBase.detect`` intentionally returns one longest match for
    ordinary care questions.  Comparison questions need all named plants, so
    this helper scans aliases without changing the single-target parser
    contract.  Shared/ambiguous aliases are skipped rather than guessed.
    """

    compact = _compact_text(message)
    matches: list[PlantProfile] = []
    for profile in knowledge_base.all():
        terms = (profile.canonical_name, *profile.aliases)
        if any(_compact_text(term) and _compact_text(term) in compact for term in terms):
            # ``get`` returns None for a shared ambiguous alias.  The
            # canonical name itself is always safe when present.
            if profile not in matches:
                matches.append(profile)
    return matches


def _render_comparison(profiles: list[PlantProfile]) -> str:
    """Render a concise, conditional comparison without invented precision."""

    names = " vs ".join(f"**{item.canonical_name}**" for item in profiles)
    lines = [
        f"{names}怎么选，先看这三个差异：",
        "",
        "| 花卉 | 光照 | 浇水判断 | 土壤/风险 |",
        "|---|---|---|---|",
    ]
    for item in profiles:
        risk = item.common_issues[0] if item.common_issues else "留意积水和病虫害"
        lines.append(
            f"| {item.canonical_name} | {item.light_preference} | "
            f"{item.watering_principle} | {item.soil_preference}；常见问题：{risk} |"
        )
    lines.extend(
        [
            "",
            "没有绝对的‘更好’：光照充足优先选耐直射的品种，明亮散射光则选耐柔光的品种；最终还要结合盆径、温度和通风。",
        ]
    )
    return "\n".join(lines)


def _render_weather(parsed: FlowerParseResult, context: GardenContext) -> str:
    """Give a useful, honest weather response when live data is unavailable."""

    location = parsed.location or context.location
    if not location:
        return (
            "要查当前天气或降雨，请先告诉我所在城市或地区。\n\n"
            "在拿到地点前，先按盆土干湿和叶片状态调整浇水：连续阴雨时注意排水，"
            "高温大风时增加观察频率，先不要按固定天数盲目浇水。"
        )
    return (
        f"你问的是**{location}**的近期天气对养花的影响。实时温度和降雨需要当前资料核验；"
        "在未核验前可先这样安排：\n\n"
        "- 高温或大风：缩短检查盆土的间隔，避开正午浇水和施肥。\n"
        "- 连续阴雨或湿度高：先确认盆底排水，减少浇水，保持通风。\n"
        "- 低温、霜冻预警：暂停浓肥，保护根系并避免冷水刺激。\n\n"
        "如果你告诉我具体植物和日期，我可以再按它的耐寒/耐热范围细化。"
    )


def _render_safety(notice: SafetyNotice) -> str:
    lines = [notice.message]
    if notice.immediate_actions:
        lines.extend(["", "现在先这样做："])
        lines.extend(f"- {item}" for item in notice.immediate_actions)
    if notice.do_not_do:
        lines.extend(["", "不要这样做："])
        lines.extend(f"- {item}" for item in notice.do_not_do)
    if notice.seek_help:
        lines.extend(["", notice.seek_help])
    return "\n".join(lines)


def _light_label(value: LightLevel | str | None) -> str:
    labels = {
        LightLevel.FULL_SUN: "直射光较充足",
        LightLevel.PARTIAL_SUN: "半日照",
        LightLevel.BRIGHT_INDIRECT: "明亮散射光",
        LightLevel.SHADE: "光照偏弱",
        LightLevel.UNKNOWN: "光照未确认",
    }
    if isinstance(value, LightLevel):
        return labels[value]
    return str(value or "光照未确认")


def _selection_score(profile: PlantProfile, light: LightLevel | str | None) -> int:
    text = profile.light_preference
    if light is LightLevel.FULL_SUN:
        return 3 if any(term in text for term in ("充足直射", "6 小时", "尽量充足")) else 1
    if light is LightLevel.PARTIAL_SUN:
        return 3 if any(term in text for term in ("上午", "温和直射", "午后遮阴")) else 2
    if light is LightLevel.BRIGHT_INDIRECT:
        return 3 if "散射光" in text else (2 if "午后遮阴" in text else 1)
    if light is LightLevel.SHADE:
        # No flowering plant should be promised to thrive in a dark place.
        return 1 if "散射光" in text else 0
    return 2


def _select_profiles(
    knowledge_base: FlowerKnowledgeBase,
    light: LightLevel | str | None,
    *,
    limit: int = 3,
) -> list[PlantProfile]:
    ranked = sorted(
        knowledge_base.all(),
        key=lambda item: (_selection_score(item, light), -len(item.canonical_name)),
        reverse=True,
    )
    return [item for item in ranked if _selection_score(item, light) > 0][:limit]


def _render_selection(
    parsed: FlowerParseResult,
    context: GardenContext,
    knowledge_base: FlowerKnowledgeBase,
) -> str:
    light = parsed.light or context.light
    location = parsed.location or context.location
    candidates = _select_profiles(knowledge_base, light)
    if light is LightLevel.SHADE:
        intro = (
            "如果这个位置几乎没有自然光，常见开花植物都很难稳定开花；"
            "先测一天里是否有明亮散射光。"
        )
    else:
        conditions = [_light_label(light)]
        if location:
            conditions.append(str(location))
        intro = f"按目前的条件（{'、'.join(conditions)}），可以先看这 {len(candidates)} 种："
    lines = [intro, ""]
    for profile in candidates:
        lines.append(
            f"- **{profile.canonical_name}**：{profile.light_preference}；"
            f"浇水以“{profile.watering_principle}”为准。"
        )
    lines.extend(
        [
            "",
            "第一步：先连续观察 2–3 天，记录直射光时段和盆土变干速度，再从上面选一种开始。",
        ]
    )
    if parsed.clarification:
        lines.append(parsed.clarification)
    return "\n".join(lines)


def _profile_context_suffix(context: GardenContext) -> str:
    parts: list[str] = []
    if context.location:
        parts.append(f"地点 {context.location}")
    if context.light:
        parts.append(f"光照 {_light_label(context.light)}")
    if isinstance(context.container, ContainerInfo) and context.container.diameter_cm:
        parts.append(f"盆径约 {context.container.diameter_cm:g} 厘米")
    elif context.container:
        parts.append("已提供容器信息")
    return f"（{'，'.join(parts)}）" if parts else ""


def _render_plan_for_intent(
    intent: FlowerIntentName,
    profile: PlantProfile,
    context: GardenContext,
    plan: CarePlan,
) -> str:
    suffix = _profile_context_suffix(context)
    if intent is FlowerIntentName.WATERING:
        return (
            f"**{profile.canonical_name}浇水**{suffix}\n\n"
            f"{profile.watering_principle}\n\n"
            "不要只按“几天一次”执行：把手指探入表土约 2–3 厘米，"
            "并结合盆重、叶片状态和盆底排水再决定。"
            "天气变热、风大或小盆时检查得更勤；阴雨、低温和大盆则延后。"
        )
    if intent is FlowerIntentName.LIGHT:
        return (
            f"**{profile.canonical_name}光照**{suffix}\n\n{profile.light_preference}\n\n"
            "调整位置时分几天逐步增加光照；若出现局部褪色、焦边，先退回较柔和的位置观察。"
        )
    if intent is FlowerIntentName.SOIL:
        return (
            f"**{profile.canonical_name}配土/换盆**{suffix}\n\n{profile.soil_preference}\n\n"
            "先确认盆底有排水孔。刚换盆时不要立刻重肥；若根系发黑、发软或有异味，先隔离并清理受损组织。"
        )
    if intent is FlowerIntentName.FERTILIZING:
        return (
            f"**{profile.canonical_name}施肥**{suffix}\n\n{profile.fertilization_principle}\n\n"
            "优先按产品标签从低浓度开始，土壤过干时先浇水；出现病弱、烂根或极端温度时先停肥排查。"
        )
    if intent is FlowerIntentName.PRUNING:
        pruning = profile.pruning_principle or (
            "先去除已枯死、病弱和互相摩擦的枝叶，健康主枝暂不重剪。"
        )
        return (
            f"**{profile.canonical_name}修剪**{suffix}\n\n"
            f"{pruning}\n\n"
            "工具先消毒；不知道品种或花芽位置时，先轻剪残花，不做一次性重剪。"
        )
    if intent is FlowerIntentName.PROPAGATION:
        return (
            f"**{profile.canonical_name}繁殖**{suffix}\n\n"
            f"{profile.propagation_principle or '可先从健壮无病虫的母株取材，小规模尝试。'}\n\n"
            "新苗先用干净、透气介质并避免强光直晒；出现腐烂立即隔离。"
        )
    if intent is FlowerIntentName.SEASONAL_PLAN:
        season = context.season or "当前季节"
        action_lines = [f"- **{item.category}**：{item.action}" for item in plan.actions[:5]]
        return "\n".join(
            [
                f"**{profile.canonical_name} · {season}养护清单**{suffix}",
                "",
                *action_lines,
                "",
                plan.uncertainty or "具体频率请按温度、盆土干湿和植株状态调整。",
            ]
        )
    return "\n".join(
        [
            f"**{profile.canonical_name}养护要点**{suffix}",
            "",
            f"- 光照：{profile.light_preference}",
            f"- 浇水：{profile.watering_principle}",
            f"- 土壤：{profile.soil_preference}",
            f"- 施肥：{profile.fertilization_principle}",
            "",
            plan.uncertainty or "具体频率需要结合环境观察。",
        ]
    )


def _render_symptom(
    parsed: FlowerParseResult,
    profile: PlantProfile,
    context: GardenContext,
) -> str:
    observations = parsed.observations or context.recent_observations[-1:]
    observation_text = "、".join(observations) if observations else "状态异常"
    possibilities: list[str] = []
    if any(item in observation_text for item in ("黄叶", "叶子发黄", "掉叶")):
        possibilities.extend(
            ["盆土长期过湿或根系缺氧", "浇水忽干忽湿", "光照突变", "缺素或盐分累积"]
        )
    if "萎蔫" in observation_text:
        possibilities.extend(["短时缺水", "根系受损导致吸水困难", "午后高温蒸腾过强"])
    if any(item in observation_text for item in ("虫子", "蚜虫", "红蜘蛛", "介壳虫")):
        possibilities.extend(["蚜虫/介壳虫等可见害虫", "红蜘蛛等需要观察叶背的微小害虫"])
    if any(item in observation_text for item in ("黑斑", "白粉", "叶斑", "霉")):
        possibilities.extend(["通风差、叶面长期潮湿相关的病害", "日灼、药害等非传染性损伤"])
    if not possibilities:
        possibilities = ["水分或根系问题", "光照/温度变化", "病虫害或机械损伤"]
    # Preserve order while de-duplicating and keep a manageable differential.
    possibilities = list(dict.fromkeys(possibilities))[:4]
    lines = [
        f"**观察**：这盆{profile.canonical_name}出现了“{observation_text}”。仅凭文字不能确定病因。",
        "",
        "**优先排查的可能原因**：",
        *[f"{index}. {item}" for index, item in enumerate(possibilities, start=1)],
        "",
        "**先做的低风险处理**：",
        "- 暂停施肥和喷药，检查盆底是否积水、根颈是否发软或有异味。",
        "- 把植株放在通风、符合其光照需求的位置；严重病叶先隔离观察。",
        "- 拍下整株、叶片正反面和盆土近照，连续 2–3 天记录干湿与变化。",
        "",
        "**还需要确认**：最近一次浇水时间、盆土是否一直湿、光照时长，以及叶背有没有虫或蛛网。",
    ]
    return "\n".join(lines)


class FlowerAssistantCore:
    """One-turn, network-free assistant core with explicit context output."""

    def __init__(
        self,
        *,
        knowledge_base: FlowerKnowledgeBase | None = None,
        parser: FlowerParser | None = None,
    ) -> None:
        self.knowledge_base = knowledge_base or DEFAULT_KNOWLEDGE_BASE
        self.parser = parser or (
            DEFAULT_FLOWER_PARSER
            if self.knowledge_base is DEFAULT_KNOWLEDGE_BASE
            else FlowerParser(self.knowledge_base)
        )

    def answer(
        self,
        message: str,
        *,
        context: GardenContext | None = None,
        session_id: Any | None = None,
        now: datetime | None = None,
    ) -> FlowerTurn:
        parsed, updated = self.parser.parse_and_update(
            message,
            context=context,
            session_id=session_id,
            now=now or datetime.now(UTC),
        )
        if parsed.safety_notice is not None:
            return FlowerTurn(
                parse=parsed,
                context=updated,
                answer_markdown=_render_safety(parsed.safety_notice),
                safety_notice=parsed.safety_notice,
            )
        if _IDENTITY_RE.fullmatch(message.strip()):
            answer = (
                "我是**种花 Agent**。我可以帮你按光照和空间选花，安排浇水、施肥、换盆与修剪，"
                "也能用低风险步骤排查常见黄叶和病虫害。告诉我植物名称和大致环境即可开始。"
            )
        elif parsed.intent_name is FlowerIntentName.OUT_OF_SCOPE:
            answer = parsed.clarification or "我可以帮助处理花卉种植与养护问题。"
        elif (
            _WEATHER_RE.search(message)
            and parsed.intent_name is FlowerIntentName.SEASONAL_PLAN
        ):
            answer = _render_weather(parsed, updated)
        elif parsed.intent_name is FlowerIntentName.PLANT_SELECTION:
            answer = _render_selection(parsed, updated, self.knowledge_base)
        elif _COMPARISON_RE.search(message) and len(
            profiles := _profiles_mentioned(message, self.knowledge_base)
        ) >= 2:
            answer = _render_comparison(profiles[:3])
        elif parsed.resolved_plant is None:
            answer = parsed.clarification or "请补充花卉名称或可观察到的特征。"
            if parsed.intent_name is FlowerIntentName.PEST_DISEASE:
                answer += (
                    "\n\n在确认品种前，先暂停施肥和喷药，检查是否积水并隔离明显有虫或病斑的植株。"
                )
        else:
            profile = parsed.resolved_plant
            if parsed.intent_name is FlowerIntentName.PEST_DISEASE:
                answer = _render_symptom(parsed, profile, updated)
            else:
                plan = self.knowledge_base.care_plan(profile, updated, now=now)
                if plan is None:  # defensive; a resolved profile always produces a plan
                    answer = "当前无法生成养护清单，请稍后重试。"
                else:
                    answer = _render_plan_for_intent(parsed.intent_name, profile, updated, plan)
        return FlowerTurn(parse=parsed, context=updated, answer_markdown=answer)

    handle = answer


DEFAULT_FLOWER_ASSISTANT = FlowerAssistantCore()


__all__ = ["DEFAULT_FLOWER_ASSISTANT", "FlowerAssistantCore"]
