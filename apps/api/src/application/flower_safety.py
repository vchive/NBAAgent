"""Deterministic safety short-circuit for gardening questions.

The guard is intentionally evaluated before plant lookup or online search.  It
does not attempt to diagnose poisoning or recommend a chemical treatment; it
stops high-risk requests and gives low-regret next steps.  Negated educational
phrases (for example, “为什么不能混用农药”) remain allowed, while a request
that could lead to exposure is blocked conservatively.
"""

from __future__ import annotations

import re
import unicodedata

from apps.api.src.domain.flower import SafetyCategory, SafetyNotice


def _normalise(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return " ".join(value.split())


def _has_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns)


_MIX_RE = (
    r"(?:混用|混合|混在一起|兑在一起|一起喷|同时喷|配伍|复配).{0,20}(?:农药|杀虫剂|杀菌剂|除草剂|药剂|波尔多液|石硫合剂)",
    r"(?:农药|杀虫剂|杀菌剂|除草剂|药剂).{0,20}(?:混用|混合|混在一起|兑|一起喷|配伍)",
    r"(?:mix|combine|tank\s*mix).{0,30}(?:pesticide|herbicide|fungicide|insecticide)",
)
_UNKNOWN_INGESTION_RE = (
    r"(?:不认识|不确定|未知|不知道).{0,12}(?:植物|叶子|果实|花|蘑菇).{0,20}(?:吃|食用|能不能吃|入口|吞)",
    r"(?:吃了|误食|吞了|咬了一口).{0,20}(?:植物|叶子|果实|花|不认识|未知)",
    r"(?:can\s+i\s+eat|ate|ingested).{0,25}(?:unknown|unidentified|plant|leaf|flower)",
)
_PET_RE = (
    r"(?:猫|狗|宠物|兔子|鸟|仓鼠).{0,24}(?:吃|咬|舔|误食|中毒|接触|碰到)",
    r"(?:吃|咬|舔|误食).{0,24}(?:猫|狗|宠物|兔子|鸟|仓鼠)",
    r"(?:cat|dog|pet).{0,24}(?:ate|chewed|licked|poison|exposure)",
)
_HUMAN_RE = (
    r"(?:人|孩子|小孩|宝宝|孕妇|我).{0,24}(?:误食|吃了|吸入|吸到|溅到|进眼|中毒|接触)",
    r"(?:误食|吸入|进眼|溅到|中毒).{0,24}(?:人|孩子|小孩|宝宝|我)",
    # Exposure descriptions often omit the subject entirely (for example
    # “眼睛沾到杀虫剂” or “皮肤接触农药”).  These are still urgent human
    # exposure requests and must be stopped before any lookup/search path.
    r"(?:眼睛|眼部|皮肤|手上|脸上|口鼻|鼻子|喉咙).{0,24}(?:沾到|溅到|进入|进了|接触|碰到|喷到).{0,24}(?:农药|杀虫剂|杀菌剂|除草剂|药液|化学品)",
    r"(?:农药|杀虫剂|杀菌剂|除草剂|药液|化学品).{0,24}(?:入眼|进眼|溅到眼|接触皮肤|沾到皮肤|吸入|吸到|喷到脸)",
    r"(?:吸入|吸到|闻到).{0,24}(?:农药|杀虫剂|杀菌剂|除草剂|药液|化学品)",
    r"(?:child|human|i).{0,24}(?:ingested|inhaled|eye|poison|exposure)",
)
_CHEMICAL_RE = (
    r"(?:没有标签|无标签|自配|高浓度|原液|剧毒|禁用|敌敌畏|百草枯|甲胺磷).{0,30}(?:喷|用|稀释|浇|施)",
    r"(?:喷|用|稀释|浇|施).{0,30}(?:没有标签|无标签|自配|高浓度|原液|剧毒|百草枯|甲胺磷)",
)
_DISPOSAL_RE = (
    r"(?:农药|药液|化学品).{0,20}(?:倒进|倒入|排入|冲进|排水沟|河里|下水道)",
    r"(?:倒进|倒入|排入|冲进).{0,20}(?:河|沟|下水道|水源)",
)

_EDUCATIONAL_RE = re.compile(
    r"(?:为什么|为何|为什么不能|风险|危害|原理|解释|科普|如何避免|注意事项).{0,8}$",
    re.IGNORECASE,
)


def _negated_educational(text: str, category: SafetyCategory) -> bool:
    """Allow a clearly educational question without an action request."""

    value = text.strip()
    if category is SafetyCategory.CHEMICAL_MIXING:
        return bool(
            re.search(
                r"(?:为什么|为何|能不能|可不可以).{0,8}(?:混用|混合|一起喷).{0,12}(?:风险|危害|不行|不能|注意|原理|原因)",
                value,
            )
        )
    if category is SafetyCategory.UNKNOWN_INGESTION:
        return bool(re.search(r"(?:为什么|为何|如何避免).{0,8}(?:误食|不能吃|有毒)", value))
    return False


def classify_flower_safety(message: str) -> SafetyNotice | None:
    """Return a notice when *message* must be blocked before retrieval."""

    value = _normalise(message)
    if not value:
        return None
    checks: tuple[SafetyCategory, tuple[str, ...]] = (
        (SafetyCategory.CHEMICAL_MIXING, _MIX_RE),
        # A pet-specific exposure is more actionable than the broader unknown-
        # ingestion category, so evaluate it first when both patterns match.
        (SafetyCategory.PET_EXPOSURE, _PET_RE),
        # An identified human/child exposure should not be collapsed into the
        # generic unknown-ingestion response.
        (SafetyCategory.HUMAN_EXPOSURE, _HUMAN_RE),
        (SafetyCategory.UNKNOWN_INGESTION, _UNKNOWN_INGESTION_RE),
        (SafetyCategory.HIGH_RISK_CHEMICAL, _CHEMICAL_RE),
        (SafetyCategory.UNSAFE_DISPOSAL, _DISPOSAL_RE),
    )
    for category, patterns in checks:
        if not _has_any(value, patterns):
            continue
        if _negated_educational(value, category):
            continue
        if category is SafetyCategory.CHEMICAL_MIXING:
            return SafetyNotice(
                category=category,
                message="不要把不同农药或药液自行混用。相容性、稀释倍数和安全间隔必须以每种产品标签及当地植保指导为准。",
                immediate_actions=[
                    "先停止配制和喷施，保留所有产品标签与包装",
                    "若已混合，避免徒手接触，不要继续使用或倒入土壤/水源",
                    "联系当地植保机构或产品标签上的专业咨询渠道",
                ],
                do_not_do=["不要凭网络帖子自行增加浓度、混配或重复喷施"],
                seek_help="若发生吸入、皮肤/眼睛接触或不适，立即按标签急救说明并联系急救、医疗或中毒咨询机构。",
            )
        if category is SafetyCategory.UNKNOWN_INGESTION:
            return SafetyNotice(
                category=category,
                message="不要食用无法确认身份的植物、叶片、果实或花。仅凭照片或俗名不能可靠判断可食性。",
                immediate_actions=[
                    "停止继续入口，保留植物样本/包装和拍摄记录",
                    "若已经吞食，不要自行催吐，记录时间、数量和症状",
                    "立即联系当地急救或中毒咨询机构获取个体化指导",
                ],
                do_not_do=["不要用牛奶、酒或偏方抵消毒性"],
                seek_help="出现呼吸困难、意识改变、持续呕吐或抽搐时立即呼叫急救。",
            )
        if category is SafetyCategory.PET_EXPOSURE:
            return SafetyNotice(
                category=category,
                message="宠物接触或误食植物/药液可能有风险，无法仅凭文字判断毒性和剂量。",
                immediate_actions=[
                    "先移开宠物和植物，保留植物标签、样本及可能摄入时间",
                    "不要自行催吐或喂药，尽快联系兽医/宠物急诊",
                    "观察呕吐、流涎、步态异常、呼吸困难等变化并记录",
                ],
                do_not_do=["不要等待症状加重，也不要把不明药液当作普通清水处理"],
                seek_help="出现抽搐、呼吸困难、昏沉等急症请立即前往宠物急诊。",
            )
        if category is SafetyCategory.HUMAN_EXPOSURE:
            return SafetyNotice(
                category=category,
                message="人体误食、吸入或接触植物/药液需要按实际物质和暴露途径处理，不能在线确诊。",
                immediate_actions=[
                    "立即离开污染源并按产品标签进行冲洗/通风",
                    "保留产品包装、植物样本和暴露时间，不要自行催吐",
                    "联系当地急救、医疗机构或中毒咨询机构",
                ],
                do_not_do=["不要混用清洁剂或用偏方中和化学品"],
                seek_help="呼吸困难、意识异常、抽搐或眼部严重疼痛时立即呼叫急救。",
            )
        if category is SafetyCategory.HIGH_RISK_CHEMICAL:
            return SafetyNotice(
                category=category,
                message="不明标签、原液或高风险化学品不应在家庭环境中自行施用。",
                immediate_actions=[
                    "停止使用并远离儿童、宠物和食物区域",
                    "核对完整标签、登记信息和个人防护要求",
                    "向当地植保部门或有资质人员确认替代方案",
                ],
                do_not_do=["不要凭经验估算稀释倍数、徒手操作或转装到无标签容器"],
                seek_help="若已发生暴露，按标签急救要求并联系医疗/中毒咨询机构。",
            )
        if category is SafetyCategory.UNSAFE_DISPOSAL:
            return SafetyNotice(
                category=category,
                message="不要把农药或化学药液倒入土壤、排水沟、下水道或天然水体。",
                immediate_actions=[
                    "停止倾倒，封存并保留原包装和标签",
                    "按当地危险废物/农药包装回收规定咨询处理",
                    "若已经泄漏，避免接触并联系当地环保或应急部门",
                ],
                do_not_do=["不要用水冲入下水道，也不要与生活垃圾随意混装"],
                seek_help="发生大面积泄漏或进入水体时，尽快联系当地应急与环保机构。",
            )
    return None


class FlowerSafetyGuard:
    """Small object wrapper convenient for dependency injection and tests."""

    def classify(self, message: str) -> SafetyNotice | None:
        return classify_flower_safety(message)

    def is_blocked(self, message: str) -> bool:
        return self.classify(message) is not None


__all__ = ["FlowerSafetyGuard", "classify_flower_safety"]
