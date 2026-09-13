"""Offline flower catalogue and deterministic care-plan helpers.

The catalogue is intentionally small and conservative.  It is a fallback that
keeps the assistant useful when search/model services are unavailable; entries
describe principles and triggers rather than pretending that one fixed schedule
works for every cultivar or home.  Online adapters may enrich an answer through
``SearchObservation`` but should not overwrite these curated safety defaults.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from apps.api.src.domain.flower import (
    CareAction,
    CarePlan,
    GardenContext,
    LightLevel,
    PlantMatch,
    PlantProfile,
    ToxicityLevel,
    similarity,
)


def _profile(
    name: str,
    aliases: Sequence[str],
    *,
    scientific: str,
    light: str,
    temp: tuple[float, float],
    soil: str,
    water: str,
    feed: str,
    prune: str,
    propagate: str,
    issues: Sequence[str],
    safety: Sequence[str] = (),
    toxicity: ToxicityLevel = ToxicityLevel.UNKNOWN,
) -> PlantProfile:
    return PlantProfile(
        canonical_name=name,
        scientific_name=scientific,
        aliases=list(aliases),
        light_preference=light,
        temperature_range_c=temp,
        soil_preference=soil,
        watering_principle=water,
        fertilization_principle=feed,
        pruning_principle=prune,
        propagation_principle=propagate,
        common_issues=list(issues),
        safety_notes=list(safety),
        toxicity=toxicity,
    )


# Principles are phrased so they remain valid across common cultivars.  Avoid
# unsupported numeric prescriptions; the care-plan layer adds condition-based
# ranges only where a user supplied enough environmental context.
DEFAULT_PLANTS: tuple[PlantProfile, ...] = (
    _profile(
        "月季",
        ("玫瑰", "现代月季", "Rosa", "rose", "月月红"),
        scientific="Rosa hybrida",
        light="每天约 6 小时以上直射光；炎热地区午后可适度遮阴",
        temp=(5, 32),
        soil="疏松、排水良好且富含有机质的微酸至中性土壤",
        water="表土下约 2–3 厘米变干再浇透，避免长期积水；高温、风大时增加观察频率",
        feed="生长期薄肥勤施，花前偏磷钾；高温、严寒或刚移栽时暂停浓肥",
        prune="花后及时剪除残花，冬末按品种和当地气候整形；先去除病弱枝",
        propagate="扦插需选健壮半木质化枝条并保持通风湿润",
        issues=("红蜘蛛", "蚜虫", "白粉病", "黑斑病", "积水烂根"),
        safety=("农药按标签单独使用并先做小范围药害测试",),
        toxicity=ToxicityLevel.UNKNOWN,
    ),
    _profile(
        "绣球",
        ("绣球花", "八仙花", "Hydrangea", "hydrangea macrophylla"),
        scientific="Hydrangea macrophylla",
        light="明亮散射光或上午直射、午后遮阴；强光暴晒易萎蔫",
        temp=(8, 28),
        soil="保水但不积水、富含有机质的微酸性土壤；花色受品种和土壤影响",
        water="叶片或表土显示缺水时浇透，盆底排水后倒掉积水；盛夏优先早晚观察",
        feed="春季和花后少量缓释肥；现蕾及高温期避免过量氮肥",
        prune="多数大叶绣球在花后修剪，避免误剪来年花芽；先确认品种",
        propagate="花后枝条扦插，保持介质湿润但不闷湿",
        issues=("缺水萎蔫", "叶斑病", "红蜘蛛", "积水烂根", "花色变化"),
        safety=("不要仅凭花色判断土壤酸碱或品种；修剪前先确认当年生花芽位置",),
        toxicity=ToxicityLevel.CAUTION,
    ),
    _profile(
        "蝴蝶兰",
        ("蝴蝶兰花", "Phalaenopsis", "moth orchid"),
        scientific="Phalaenopsis spp.",
        light="明亮散射光，避免中午强直射；叶色和新根状态比固定时长更可靠",
        temp=(18, 30),
        soil="树皮、苔藓等透气基质，盆底和盆壁排水通风良好",
        water="根系由绿色转银白且基质趋干时浇透，避免叶心积水和长期湿根",
        feed="生长期使用充分稀释的兰花肥，先湿根再施肥；休眠或根系受损时减量",
        prune="花梗是否回剪取决于花梗状态和植株活力；枯黄根叶先清理",
        propagate="家庭环境通常通过分株或花梗芽繁殖，需确认芽体和卫生条件",
        issues=("烂根", "叶心积水", "介壳虫", "日灼", "低温冻伤"),
        safety=("浇水后倒净叶心积水，避免把根系长期泡在水里",),
        toxicity=ToxicityLevel.NONE_KNOWN,
    ),
    _profile(
        "长寿花",
        ("伽蓝菜", "圣诞伽蓝菜", "Kalanchoe", "kalanchoe"),
        scientific="Kalanchoe blossfeldiana",
        light="明亮光照，可接受温和直射；夏季午后注意降温和通风",
        temp=(10, 30),
        soil="疏松排水的多肉植物基质，盆底不可积水",
        water="土壤大半干后再浇透，低温或阴雨时减少频率；宁可偏干也不要闷湿",
        feed="生长期少量均衡肥，花芽形成期再按说明补充；休眠和高温期停肥",
        prune="花后剪除残花并轻剪徒长枝，保留健康叶节",
        propagate="枝条或叶片扦插，切口干燥后再上盆",
        issues=("徒长", "烂根", "蚜虫", "介壳虫", "叶片晒伤"),
        safety=("对猫狗可能有毒；宠物家庭避免啃食并把落叶及时清理",),
        toxicity=ToxicityLevel.TOXIC,
    ),
    _profile(
        "茉莉",
        ("茉莉花", "Jasmine", "Jasminum sambac", "阿拉伯茉莉"),
        scientific="Jasminum sambac",
        light="充足直射光，通风良好；光照不足会少花、徒长",
        temp=(12, 32),
        soil="疏松、微酸、排水良好的土壤，兼顾保水和通气",
        water="生长期表土稍干即浇透，盛夏根据蒸发和叶片状态调整；冬季明显减少",
        feed="生长期和花期薄肥，避免连续重肥；新芽受损或低温时停肥",
        prune="花后轻剪促分枝，冬末去除病弱和过密枝",
        propagate="半木质化枝条扦插，维持高湿但必须通风",
        issues=("红蜘蛛", "蚜虫", "黄叶", "积水烂根", "缺光徒长"),
        safety=("香味可能刺激敏感人群；室内保持通风并观察个体反应",),
        toxicity=ToxicityLevel.UNKNOWN,
    ),
    _profile(
        "三角梅",
        ("簕杜鹃", "九重葛", "Bougainvillea", "bougainvillea"),
        scientific="Bougainvillea spp.",
        light="尽量充足直射光；光照不足常导致枝叶旺长而少花",
        temp=(12, 35),
        soil="排水快、颗粒比例适中的土壤，避免盆底积水",
        water="枝叶出现轻微缺水信号后再浇透，花期适度控水但不能让根团完全干透",
        feed="生长期少量均衡肥，花期避免高氮；低温、刚换盆时暂停浓肥",
        prune="花后修剪过长枝并保留骨架，带刺枝条操作时戴手套",
        propagate="春夏枝条扦插，保持介质湿润、通风和散射光",
        issues=("徒长", "介壳虫", "落叶", "积水烂根", "低温冻害"),
        safety=("枝条有刺，修剪时佩戴手套并远离儿童和宠物",),
        toxicity=ToxicityLevel.CAUTION,
    ),
    _profile(
        "栀子花",
        ("栀子", "Gardenia", "gardenia jasminoides"),
        scientific="Gardenia jasminoides",
        light="明亮光照或上午直射，夏季午后遮阴并保持通风",
        temp=(12, 30),
        soil="酸性、富含有机质且排水良好的土壤；水质和盐分会影响叶色",
        water="保持均匀湿润但不积水，表层略干时浇透；避免忽干忽湿",
        feed="生长期使用适合喜酸植物的薄肥，缺铁等黄化须先确认原因再补充",
        prune="花后整形，避免在不明花芽位置时重剪；去除病弱枝",
        propagate="半木质化枝条扦插，注意消毒和通风",
        issues=("缺铁黄化", "介壳虫", "蚜虫", "积水烂根", "花苞脱落"),
        safety=("不要盲目长期施酸化剂；先检查水质、根系和土壤 pH",),
        toxicity=ToxicityLevel.UNKNOWN,
    ),
    _profile(
        "薰衣草",
        ("Lavender", "lavandula", "薰衣草花"),
        scientific="Lavandula angustifolia",
        light="充足直射光和良好空气流动",
        temp=(5, 30),
        soil="偏干、排水迅速、含颗粒介质的土壤，不耐长期闷湿",
        water="介质大部干燥后浇透，潮湿季节延长间隔并加强通风",
        feed="生长期少量薄肥即可，过量氮肥会徒长、香气和开花变差",
        prune="花后轻剪，避免一次剪入无叶老枝；保持株形通风",
        propagate="嫩枝扦插较稳妥，介质需透气并避免过湿",
        issues=("烂根", "徒长", "灰霉病", "红蜘蛛"),
        safety=("精油和浓缩制品不等同于鲜花安全；儿童、宠物误食需咨询专业人员",),
        toxicity=ToxicityLevel.CAUTION,
    ),
    _profile(
        "君子兰",
        ("君子兰花", "Clivia", "clivia miniata"),
        scientific="Clivia miniata",
        light="明亮散射光，避免强烈午后直射；定期转盆保持株形",
        temp=(10, 28),
        soil="疏松肥沃且排水透气的基质，根系粗壮但不耐长期积水",
        water="表土干燥后沿盆边浇透，叶心不要积水；低温期减少浇水",
        feed="生长期少量均衡肥，花前按说明补充；根系受损时不要施浓肥",
        prune="只剪除完全黄化或损伤叶片，避免频繁伤根换盆",
        propagate="分株或种子繁殖，分株时保留完整根系和伤口消毒",
        issues=("烂根", "日灼", "介壳虫", "夹箭", "叶斑"),
        safety=("浆液可能刺激皮肤和消化道；避免儿童、宠物啃食",),
        toxicity=ToxicityLevel.CAUTION,
    ),
    _profile(
        "绿萝",
        ("黄金葛", "魔鬼藤", "Pothos", "Epipremnum aureum"),
        scientific="Epipremnum aureum",
        light="明亮散射光较合适，也能耐较弱光；避免突然暴晒",
        temp=(12, 32),
        soil="疏松、透气且排水良好的通用观叶植物基质",
        water="表土下约 2–3 厘米干后再浇透，倒掉托盘积水；低温少光时延长间隔",
        feed="生长期少量使用稀释均衡肥，根系受损、低温或盆土过湿时暂停",
        prune="剪除黄叶和过长藤蔓，可在叶节上方修剪以促进分枝",
        propagate="带节点的健康茎段可水插或基质扦插，生根后逐步适应盆土",
        issues=("积水烂根", "低温黄叶", "暴晒灼伤", "介壳虫"),
        safety=("汁液含刺激性物质，对猫狗有毒；修剪后洗手并避免儿童或宠物啃食",),
        toxicity=ToxicityLevel.TOXIC,
    ),
    _profile(
        "矮牵牛",
        ("碧冬茄", "Petunia", "petunia hybrida"),
        scientific="Petunia × atkinsiana",
        light="每天约 5–6 小时以上直射光，盛夏极端高温时午后适度遮阴",
        temp=(10, 30),
        soil="疏松肥沃、排水良好的微酸至中性基质",
        water="表土变干后浇透，花期避免长期缺水或盆底积水；雨季加强通风",
        feed="旺盛生长期和花期按标签低浓度补肥，徒长或高温时减少氮肥",
        prune="及时摘除残花，枝条过长时轻剪并保留健康叶节",
        propagate="可播种或取健康嫩枝扦插，幼苗期保持明亮散射光和通风",
        issues=("灰霉病", "蚜虫", "积水烂根", "高温少花"),
        safety=("药剂处理前先改善通风和排水，并严格按标签使用",),
    ),
    _profile(
        "太阳花",
        ("松叶牡丹", "大花马齿苋", "Portulaca", "portulaca grandiflora"),
        scientific="Portulaca grandiflora",
        light="尽量充足的直射光；光照不足容易少花和徒长",
        temp=(12, 35),
        soil="颗粒比例适中、排水迅速的土壤，不耐长期闷湿",
        water="介质大部干燥后再浇透，连续阴雨时延长间隔并避雨通风",
        feed="生长期少量薄肥即可，氮肥过多会枝叶旺而少花",
        prune="花后轻剪徒长枝，保留健康节位促进分枝",
        propagate="枝条切口略干后扦插于透气介质，也可播种繁殖",
        issues=("积水烂根", "缺光少花", "蚜虫"),
        safety=("避免长期积水；不明药剂不要混用",),
    ),
)


_CATALOGUE: dict[str, PlantProfile] = {
    profile.canonical_name: profile for profile in DEFAULT_PLANTS
}
_ALIAS_INDEX: dict[str, PlantProfile] = {}
for _item in DEFAULT_PLANTS:
    for _term in (_item.canonical_name, *_item.aliases):
        _ALIAS_INDEX["".join(ch for ch in _term.casefold() if ch.isalnum())] = _item


def _compact(value: str) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _extract_plant_terms(text: str) -> list[str]:
    """Extract catalogue names/aliases from a natural-language query."""

    value = str(text or "")
    found: list[str] = []
    # Prefer the longest terms so “绣球花” wins over a shorter substring.
    terms = sorted(_ALIAS_INDEX, key=len, reverse=True)
    for compact in terms:
        if compact and compact in _compact(value):
            profile = _ALIAS_INDEX[compact]
            if profile.canonical_name not in found:
                found.append(profile.canonical_name)
    return found


class FlowerKnowledgeBase:
    """In-process read-only catalogue with conservative fuzzy matching."""

    def __init__(self, profiles: Iterable[PlantProfile] = DEFAULT_PLANTS) -> None:
        self.profiles = tuple(profiles)
        self._by_name = {item.canonical_name: item for item in self.profiles}
        # Keep every profile for a shared俗名 instead of silently letting the
        # last catalogue row win.  For example, a deployment may add multiple
        # orchid species under “兰花”; callers must then clarify the species.
        self._aliases: dict[str, tuple[PlantProfile, ...]] = {}
        self._alias_display: dict[str, str] = {}
        for item in self.profiles:
            for term in (item.canonical_name, *item.aliases):
                alias = _compact(term)
                if not alias:
                    continue
                existing = list(self._aliases.get(alias, ()))
                if item not in existing:
                    existing.append(item)
                self._aliases[alias] = tuple(existing)
                self._alias_display.setdefault(alias, str(term))

    def all(self) -> tuple[PlantProfile, ...]:
        return self.profiles

    def get(self, name: str | None) -> PlantProfile | None:
        if not name:
            return None
        candidates = self._aliases.get(_compact(name), ())
        return candidates[0] if len(candidates) == 1 else self._by_name.get(str(name).strip())

    def lookup(
        self,
        query: str,
        *,
        threshold: float = 0.70,
        ambiguity_gap: float = 0.08,
    ) -> PlantMatch:
        value = str(query or "").strip()
        if not value:
            return PlantMatch(
                query="",
                confidence=0.0,
                suggestions=[item.canonical_name for item in self.profiles[:5]],
            )
        compact = _compact(value)
        exact_candidates = self._aliases.get(compact, ())
        if len(exact_candidates) > 1:
            return PlantMatch(
                query=value,
                candidates=list(exact_candidates[:8]),
                confidence=1.0,
                ambiguous=True,
                suggestions=[item.canonical_name for item in exact_candidates[:6]],
            )
        if exact_candidates:
            exact = exact_candidates[0]
            return PlantMatch(
                query=value,
                profile=exact,
                candidates=[exact],
                matched_alias=value,
                confidence=1.0,
            )

        # A query often contains a whole sentence.  First look for a known name
        # as a substring, then use a fuzzy score for misspellings.
        substring_matches: list[tuple[int, PlantProfile, str]] = []
        for alias, profiles in self._aliases.items():
            if alias and alias in compact:
                substring_matches.extend((len(alias), profile, alias) for profile in profiles)
        if substring_matches:
            max_len = max(item[0] for item in substring_matches)
            candidates = []
            for length, profile, alias in sorted(substring_matches, reverse=True):
                if length == max_len and profile not in candidates:
                    candidates.append(profile)
            if len(candidates) > 1:
                return PlantMatch(
                    query=value,
                    candidates=candidates[:8],
                    confidence=0.98,
                    ambiguous=True,
                    suggestions=[item.canonical_name for item in candidates[:6]],
                )
            if len(candidates) == 1:
                profile = candidates[0]
                matched_alias = next(
                    alias for length, item, alias in substring_matches
                    if length == max_len and item is profile
                )
                return PlantMatch(
                    query=value,
                    profile=profile,
                    candidates=[profile],
                    matched_alias=self._alias_display.get(matched_alias, matched_alias),
                    confidence=0.98,
                )

        scored: list[tuple[float, PlantProfile, str]] = []
        for alias, profiles in self._aliases.items():
            score = similarity(compact, alias)
            scored.extend((score, profile, alias) for profile in profiles)
        scored.sort(key=lambda item: (item[0], len(item[2])), reverse=True)
        if not scored or scored[0][0] < threshold:
            return PlantMatch(
                query=value,
                candidates=[],
                confidence=scored[0][0] if scored else 0.0,
                suggestions=[item.canonical_name for item in self.profiles[:6]],
            )
        best_score = scored[0][0]
        best_profiles: list[PlantProfile] = []
        for score, profile, _alias in scored:
            if score < best_score - ambiguity_gap:
                break
            if profile not in best_profiles:
                best_profiles.append(profile)
        if len(best_profiles) > 1:
            return PlantMatch(
                query=value,
                candidates=best_profiles[:8],
                confidence=best_score,
                ambiguous=True,
                suggestions=[item.canonical_name for item in best_profiles[:6]],
            )
        profile = best_profiles[0]
        matched_alias = next(
            alias
            for score, item, alias in scored
            if item is profile and score == best_score
        )
        return PlantMatch(
            query=value,
            profile=profile,
            candidates=[profile],
            confidence=best_score,
            matched_alias=self._alias_display.get(matched_alias, matched_alias),
        )

    def detect(self, text: str) -> PlantMatch | None:
        compact_text = _compact(text)
        # Longest alias wins over generic substrings; if several profiles share
        # that alias, retain the ambiguity rather than guessing.
        present = [alias for alias in self._aliases if alias and alias in compact_text]
        if present:
            alias = max(present, key=len)
            profiles = self._aliases[alias]
            if len(profiles) > 1:
                return PlantMatch(
                    query=self._alias_display.get(alias, alias),
                    candidates=list(profiles[:8]),
                    confidence=1.0,
                    ambiguous=True,
                    suggestions=[item.canonical_name for item in profiles[:6]],
                )
            profile = profiles[0]
            return PlantMatch(
                query=self._alias_display.get(alias, alias),
                profile=profile,
                candidates=[profile],
                matched_alias=self._alias_display.get(alias, alias),
                confidence=1.0,
            )
        # Avoid fuzzy matching arbitrary prose.  A likely plant noun is usually
        # short and follows “种/养/这盆/我的”等 cue words.
        cues = re.findall(
            r"(?:种|养|盆栽|花|植物|这盆|我的)\s*"
            r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 .-]{1,18})",
            text,
        )
        for cue in cues:
            match = self.lookup(cue)
            if match.profile or match.ambiguous:
                return match
        return None

    def care_plan(
        self,
        plant: PlantProfile | str,
        context: GardenContext | None = None,
        *,
        now: datetime | None = None,
    ) -> CarePlan | None:
        profile = plant if isinstance(plant, PlantProfile) else self.get(plant)
        if profile is None:
            return None
        context = context or GardenContext(plant=profile)
        actions: list[CareAction] = [
            CareAction(
                category="浇水",
                action=profile.watering_principle,
                trigger="先摸土或观察根系/叶片，再决定是否浇水；不要按固定日历盲目浇",
                priority=1,
                rationale="浇水需求受光照、温度、盆径和介质共同影响",
            ),
            CareAction(
                category="光照",
                action=profile.light_preference,
                trigger="若出现徒长、叶色异常或灼伤，先调整光照并观察数日",
                priority=1,
            ),
            CareAction(
                category="土壤",
                action=profile.soil_preference,
                trigger="浇水后检查盆底是否顺畅排水，避免托盘长期存水",
                priority=1,
            ),
            CareAction(
                category="施肥",
                action=profile.fertilization_principle,
                trigger="新栽、病弱、极端高温/低温时先停浓肥",
                priority=2,
            ),
        ]
        if profile.pruning_principle:
            actions.append(
                CareAction(category="修剪", action=profile.pruning_principle, priority=2)
            )
        conditions: list[str] = []
        if context.location:
            conditions.append(f"地点：{context.location}；请结合当地温度和降雨调整")
        if context.light:
            light = (
                context.light.value
                if isinstance(context.light, LightLevel)
                else str(context.light)
            )
            conditions.append(f"已知光照：{light}")
        if context.container:
            conditions.append("容器条件会改变干湿速度，请以介质状态为准")
        uncertainty = "具体频率不能脱离品种、盆径、介质、温度和近期天气确定；先小范围观察再调整。"
        if profile.safety_notes:
            uncertainty = f"安全提醒：{'；'.join(profile.safety_notes[:2])}。{uncertainty}"
        return CarePlan(
            plant_name=profile.canonical_name,
            actions=actions,
            conditions=conditions,
            uncertainty=uncertainty,
            generated_at=now or datetime.now(UTC),
        )


DEFAULT_KNOWLEDGE_BASE = FlowerKnowledgeBase()


def lookup_plant(query: str, *, knowledge_base: FlowerKnowledgeBase | None = None) -> PlantMatch:
    return (knowledge_base or DEFAULT_KNOWLEDGE_BASE).lookup(query)


def detect_plant(
    text: str,
    *,
    knowledge_base: FlowerKnowledgeBase | None = None,
) -> PlantMatch | None:
    return (knowledge_base or DEFAULT_KNOWLEDGE_BASE).detect(text)


def make_care_plan(
    plant: PlantProfile | str,
    context: GardenContext | None = None,
    *,
    knowledge_base: FlowerKnowledgeBase | None = None,
) -> CarePlan | None:
    return (knowledge_base or DEFAULT_KNOWLEDGE_BASE).care_plan(plant, context)


__all__ = [
    "DEFAULT_KNOWLEDGE_BASE",
    "DEFAULT_PLANTS",
    "FlowerKnowledgeBase",
    "detect_plant",
    "lookup_plant",
    "make_care_plan",
]
