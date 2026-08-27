"""Official-style deterministic answer renderer."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from apps.api.src.domain.models import (
    AnswerBlock,
    AnswerBlockType,
    Correction,
    CorrectionStatus,
    DraftAnswer,
    EntityKind,
    EvidenceState,
    FactBundle,
    Game,
    GameBundle,
    PublicCorrection,
    QueryIntent,
    VerificationState,
)
from apps.api.src.domain.time_policy import format_beijing


def _name(value: Any) -> str:
    return getattr(value, "display_name", str(value))


def _num(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# News text is an untrusted external-content projection.  The normalizer
# already strips markup/control characters; this second, renderer-side check
# protects callers that construct FactAssertions directly (and prevents a
# headline containing a URL or prompt-injection phrase from crossing the
# public answer boundary).
_UNSAFE_NEWS_RE = re.compile(
    r"https?://|www\.|<\s*/?\w+\b|"
    r"(?:ignore\s+(?:all\s+)?previous\s+instructions|system\s+prompt|developer\s+message|tool\s*call|source_ref|evidence_id|canonical_id)",
    re.IGNORECASE,
)
_UNSAFE_NEWS_ZH_RE = re.compile(
    r"(?:忽略(?:之前|先前|所有)?指令|系统提示|开发者消息|工具调用|内部信息|"
    r"泄露(?:密钥|凭据)|访问令牌|api\s*密钥)",
    re.IGNORECASE,
)


def _news_parts(value: Any) -> tuple[str | None, str | None]:
    """Return a safe ``(title, summary)`` pair from a news fact value."""

    if isinstance(value, Mapping):
        title = value.get("title", value.get("headline"))
        summary = value.get("summary", value.get("description"))
    else:
        title, summary = value, None

    def clean(item: Any, *, limit: int) -> str | None:
        if item is None:
            return None
        text = str(item).strip()
        if not text or _UNSAFE_NEWS_RE.search(text) or _UNSAFE_NEWS_ZH_RE.search(text):
            return None
        # Keep the renderer defensive even when a manually-created fact did
        # not pass through ``Normalizer.news``.
        text = " ".join(text.split())
        return text[:limit] or None

    return clean(title, limit=500), clean(summary, limit=4000)


def _public_corrections(corrections: Iterable[Correction]) -> list[PublicCorrection]:
    def display(value: Any) -> str:
        """Render a verified correction without exposing raw dict/enum metadata."""

        if isinstance(value, Mapping):
            home = value.get("home")
            away = value.get("away")
            if home is not None and away is not None:
                return f"主队 {home}–客队 {away}"
            return (
                "、".join(f"{key} {item}" for key, item in value.items() if item is not None)
                or "暂无数据"
            )
        raw = getattr(value, "value", value)
        labels = {
            "TWO_POINT": "两分球",
            "THREE_POINT": "三分球",
            "FREE_THROW": "罚球",
            "UNKNOWN": "未标注",
            "NONE": "未标注",
        }
        return labels.get(str(raw), str(raw))

    output: list[PublicCorrection] = []
    for correction in corrections:
        if correction.status is CorrectionStatus.CORRECTED:
            expected = correction.verified_value
            output.append(
                PublicCorrection(
                    status=CorrectionStatus.CORRECTED,
                    message=f"核验结果显示，正确信息是：{display(expected)}。",
                )
            )
        else:
            output.append(
                PublicCorrection(
                    status=CorrectionStatus.UNVERIFIED,
                    message="目前没有足够公开数据核实这条前提，暂不下结论。",
                )
            )
    return output


class TemplateComposer:
    """Pure renderer; it never performs retrieval or arithmetic."""

    def compose(
        self,
        intent: QueryIntent,
        facts: FactBundle,
        *,
        game: Game | None = None,
        bundle: GameBundle | None = None,
        derived: Any | None = None,
        retrieved_at: datetime | None = None,
        corrections: Iterable[Correction] = (),
    ) -> DraftAnswer:
        public_corrections = _public_corrections(corrections)
        blocks: list[AnswerBlock] = []
        lines: list[str] = []
        evidence = facts.evidence_state
        # A correction is rendered before the normal answer, matching the LLD policy.
        if public_corrections:
            for item in public_corrections:
                blocks.append(AnswerBlock(type=AnswerBlockType.WARNING, content=item.message))
                lines.append(item.message)

        if game is None and bundle is not None:
            game = bundle.game
        if game is not None and intent.intent_name.value in {
            "DATA",
            "SCHEDULE_RESULT",
            "FACT_CHECK",
            "RECAP",
            "TACTICAL",
            "FOLLOW_UP",
        }:
            if game.home_score is not None and game.away_score is not None:
                winner = (
                    game.home
                    if game.home_score > game.away_score
                    else game.away
                    if game.away_score > game.home_score
                    else None
                )
                if winner:
                    winner_score = (
                        game.home_score
                        if winner.canonical_id == game.home.canonical_id
                        else game.away_score
                    )
                    loser_score = (
                        game.away_score
                        if winner.canonical_id == game.home.canonical_id
                        else game.home_score
                    )
                    lead = f"**{winner.display_name}** 以 **{winner_score}–{loser_score}** 取胜。"
                else:
                    lead = f"这场比赛以 **{game.home_score}–{game.away_score}** 结束。"
                blocks.append(AnswerBlock(type=AnswerBlockType.TEXT, content=lead))
                lines.append(lead)
            else:
                blocks.append(
                    AnswerBlock(type=AnswerBlockType.WARNING, content="比分尚未完成核验。")
                )
                lines.append("比分尚未完成核验。")

        if (
            intent.intent_name.value in {"DATA", "FACT_CHECK"}
            and bundle is not None
            and bundle.leaders
        ):
            # The summary contains several box-score metrics.  Select the
            # metric explicitly requested by the parser instead of always
            # answering with points; this keeps “篮板/助攻/三分” questions
            # from receiving a plausible but irrelevant points leader.
            metric_names = {
                "points",
                "rebounds",
                "assists",
                "three_pointers",
                "field_goal_percentage",
            }
            requested_metric = next(
                (
                    getattr(metric, "name", "")
                    for metric in getattr(intent, "metrics", [])
                    if getattr(metric, "name", "") in metric_names
                ),
                "points",
            )
            metric_rows = [
                (line.subject.display_name, line.metrics.get(requested_metric))
                for line in bundle.leaders
                if line.metrics.get(requested_metric) is not None
            ]
            if metric_rows:
                metric_rows.sort(key=lambda row: row[1], reverse=True)
                top_name, top_value = metric_rows[0]
                labels = {
                    "points": ("得分王", "得分最高", "分"),
                    "rebounds": ("篮板王", "篮板最多", "个"),
                    "assists": ("助攻王", "助攻最多", "次"),
                    "three_pointers": ("三分命中最多", "三分命中最多", "个"),
                    "field_goal_percentage": ("命中率最高", "命中率最高", "%"),
                }
                label, phrase, unit = labels[requested_metric]
                text = f"{phrase}的是 **{top_name}**，{_num(top_value)} {unit}。"
                blocks.append(
                    AnswerBlock(
                        type=AnswerBlockType.FACT,
                        label=label,
                        value=top_name,
                        unit=f"{_num(top_value)} {unit}",
                    )
                )
                blocks.append(
                    AnswerBlock(
                        type=AnswerBlockType.TABLE,
                        columns=["球员", "得分", "篮板", "助攻"],
                        rows=[
                            [
                                line.subject.display_name,
                                line.metrics.get("points"),
                                line.metrics.get("rebounds"),
                                line.metrics.get("assists"),
                            ]
                            for line in bundle.leaders
                        ],
                    )
                )
                lines.append(text)

        # Follow-up intents can still carry a PBP window (for example “那场最后
        # 5 秒”).  Render the event timeline whenever the deterministic derivation
        # supplied events, rather than relying solely on the top-level intent name.
        if intent.intent_name.value == "PLAY_BY_PLAY" or getattr(derived, "events", None):
            events = list(getattr(derived, "events", []) or [])
            if events:
                rows = []
                shot_labels = {
                    "TWO_POINT": "两分球",
                    "THREE_POINT": "三分球",
                    "FREE_THROW": "罚球",
                    "NONE": "—",
                    "UNKNOWN": "未标注",
                }
                for event in events:
                    actor = event.shooter.display_name if event.shooter else "未标注"
                    assister = event.assister.display_name if event.assister else "未标注"
                    points = "—" if event.points is None else f"{event.points} 分"
                    shot_type = shot_labels.get(
                        getattr(getattr(event, "shot_type", None), "value", "UNKNOWN"),
                        "未标注",
                    )
                    if event.home_score_after is None or event.away_score_after is None:
                        score_after = "未标注"
                    else:
                        score_after = f"{event.home_score_after}–{event.away_score_after}"
                    rows.append(
                        [
                            f"第{event.period}节",
                            f"{float(event.clock_seconds_remaining):g}秒",
                            actor,
                            assister,
                            shot_type,
                            points,
                            score_after,
                        ]
                    )
                blocks.append(
                    AnswerBlock(
                        type=AnswerBlockType.TABLE,
                        columns=[
                            "节次",
                            "剩余时间",
                            "出手者",
                            "助攻者",
                            "类型",
                            "结果",
                            "事件后比分",
                        ],
                        rows=rows,
                    )
                )
                window = getattr(intent, "clock_window", None)
                if getattr(window, "all_periods", False):
                    scope_text = "按每节结束前的时间窗口"
                elif (
                    getattr(window, "scope", None) is not None
                    and getattr(
                        getattr(window, "scope", None), "value", getattr(window, "scope", None)
                    )
                    == "PERIOD_END"
                    and intent.period is not None
                ):
                    scope_text = f"按第{intent.period}节结束前的时间窗口"
                else:
                    scope_text = "按全场结束前的时间窗口"
                text = f"{scope_text}，共找到 **{len(events)} 个回合**。"
                blocks.append(AnswerBlock(type=AnswerBlockType.TEXT, content=text))
                lines.append(text)
                event_lines = []
                for event in events:
                    actor = event.shooter.display_name if event.shooter else "未标注球员"
                    points = "得分值未标注" if event.points is None else f"{event.points} 分"
                    shot_type = shot_labels.get(
                        getattr(getattr(event, "shot_type", None), "value", "UNKNOWN"),
                        "未标注",
                    )
                    assister = event.assister.display_name if event.assister else "未标注助攻者"
                    if event.home_score_after is None or event.away_score_after is None:
                        score_after = "事件后比分未标注"
                    else:
                        score_after = (
                            f"事件后比分 {event.home_score_after}–{event.away_score_after}"
                        )
                    event_lines.append(
                        f"第{event.period}节 {float(event.clock_seconds_remaining):g}秒："
                        f"{actor}，{points}，类型 {shot_type}，助攻者 {assister}，{score_after}"
                    )
                if event_lines:
                    detail = "；".join(event_lines) + "。"
                    blocks.append(AnswerBlock(type=AnswerBlockType.TEXT, content=detail))
                    lines.append(detail)

                # Answer “最后一攻/最后一球是谁投的、事件后比分” directly.
                # A terminal whistle row may have no shooter; distinguish it
                # from the last identifiable scoring row instead of inventing a
                # player name or silently using an intermediate score.
                # Focus the direct answer on the final selected record, not
                # merely the latest row that happens to have a shooter.  A
                # provider may append a terminal whistle/administrative row
                # with a score but no participant; reporting the previous
                # free throw as the "last shot" would be a factual error.
                derived_facts = list(getattr(derived, "facts", []) or [])
                by_predicate = {
                    fact.predicate: fact
                    for fact in reversed(derived_facts)
                    if fact.verification in {VerificationState.VERIFIED, VerificationState.PARTIAL}
                }
                score_fact = by_predicate.get("last_score_after")
                shot_labels_by_value = {
                    "TWO_POINT": "两分球",
                    "THREE_POINT": "三分球",
                    "FREE_THROW": "罚球",
                }
                focus_parts: list[str] = []
                final_event = events[-1] if events else None
                if final_event is None:
                    focus_parts.append("没有可核验的最后一条回合记录。")
                else:
                    final_shooter = getattr(final_event, "shooter", None)
                    if final_shooter is None:
                        focus_parts.append("最后一条记录未标注出手者，最后一投暂无可核验结果。")
                    else:
                        focus_parts.append(
                            f"最后一条记录的出手者是 **{final_shooter.display_name}**"
                        )

                    raw_shot_type = getattr(getattr(final_event, "shot_type", None), "value", None)
                    if raw_shot_type in shot_labels_by_value:
                        focus_parts.append(f"投篮类型为{shot_labels_by_value[raw_shot_type]}")
                    else:
                        focus_parts.append("最后一条记录未标注投篮类型，暂无可核验结果。")

                    final_assister = getattr(final_event, "assister", None)
                    if final_assister is None:
                        focus_parts.append("最后一条记录未标注助攻者")
                    else:
                        focus_parts.append(f"助攻者是 **{final_assister.display_name}**")

                if score_fact is not None and isinstance(score_fact.value, Mapping):
                    home = score_fact.value.get("home")
                    away = score_fact.value.get("away")
                    if home is not None and away is not None:
                        focus_parts.append(f"最新记录后的比分为 **{home}–{away}**")
                    else:
                        focus_parts.append("最新记录后的比分暂无可核验结果。")
                else:
                    focus_parts.append("最新记录后的比分暂无可核验结果。")
                if focus_parts:
                    focus_text = "；".join(part.rstrip("。") for part in focus_parts) + "。"
                    blocks.append(AnswerBlock(type=AnswerBlockType.TEXT, content=focus_text))
                    lines.append(focus_text)
            else:
                lines.append("该时间窗口暂无可核验的逐回合记录。")
                blocks.append(AnswerBlock(type=AnswerBlockType.WARNING, content=lines[-1]))

        if intent.intent_name.value in {"TACTICAL", "RECAP"}:
            # Reasons are selected from already verified facts; no model arithmetic.
            reasons: list[str] = []
            for fact in facts.facts:
                if fact.verification in {
                    VerificationState.VERIFIED,
                    VerificationState.PARTIAL,
                } and fact.predicate in {"score", "points", "winner"}:
                    reasons.append(
                        f"{fact.subject.display_name}：{fact.predicate} {_num(fact.value)}"
                    )
                if len(reasons) >= 3:
                    break
            conclusion = "从已核验的比赛记录看，关键差异在于执行质量和末段回合控制。"
            blocks.append(AnswerBlock(type=AnswerBlockType.ANALYSIS, content=conclusion))
            lines.append(conclusion)
            if reasons:
                reason_text = "；".join(reasons)
                blocks.append(
                    AnswerBlock(type=AnswerBlockType.TEXT, content=f"**事实依据**：{reason_text}。")
                )
                lines.append(f"**事实依据**：{reason_text}。")

        if not game and not getattr(derived, "facts", None):
            generic = [
                fact
                for fact in facts.facts
                if fact.verification in {VerificationState.VERIFIED, VerificationState.PARTIAL}
            ]
            query_has_team_subject = any(
                getattr(entity, "kind", None) is EntityKind.TEAM
                for entity in getattr(intent, "entities", [])
            )
            # News facts carry a structured title/summary mapping in their
            # value.  Render those fields as public FACT/TEXT blocks rather
            # than passing the mapping to ``AnswerBlock.value`` (which is
            # intentionally scalar-only) or falling through to the opaque
            # generic ``subject：news {...}`` form.
            rendered_news_ids: set[str] = set()
            for fact in generic:
                if fact.predicate not in {"news", "background"}:
                    continue
                title, summary = _news_parts(fact.value)
                if title is None:
                    # A malformed/suspicious headline is omitted; the overall
                    # answer will retain its evidence state and can show the
                    # normal no-data warning if nothing safe remains.
                    continue
                blocks.append(AnswerBlock(type=AnswerBlockType.FACT, label="新闻标题", value=title))
                lines.append(f"**{title}**")
                if summary is not None:
                    blocks.append(
                        AnswerBlock(
                            type=AnswerBlockType.TEXT,
                            label="新闻摘要",
                            content=summary,
                        )
                    )
                    lines.append(summary)
                rendered_news_ids.add(fact.fact_id)

            for fact in generic[:20]:
                if fact.fact_id in rendered_news_ids or fact.predicate in {"news", "background"}:
                    continue
                # A championship record carries two distinct pieces of
                # information: ``value`` is the champion's display name while
                # ``season`` identifies when the title was won.  Preserve both
                # in natural Chinese instead of rendering the opaque generic
                # form (“马刺：冠军 马刺”), which would fail “队史上一次夺冠
                # 是哪一年？” questions.
                if intent.intent_name.value == "HISTORY" and fact.predicate == "championship":
                    record_season = getattr(fact, "season", None)
                    season_label = getattr(record_season, "label", None)
                    if season_label:
                        subject_kind = getattr(getattr(fact, "subject", None), "kind", None)
                        subject_name = getattr(getattr(fact, "subject", None), "display_name", "")
                        has_team_subject = (
                            getattr(subject_kind, "value", subject_kind) == EntityKind.TEAM.value
                        )
                        # A team-scoped, season-unspecified championship lookup
                        # is conventionally asking for that franchise's latest
                        # title year.  League/explicit-season lookups instead
                        # lead with the season and champion.
                        if has_team_subject and query_has_team_subject and intent.season is None:
                            label = "最近夺冠赛季"
                            value = season_label
                            unit = "赛季"
                            text = f"{subject_name} 队史最近一次夺冠是 **{season_label}**。"
                        else:
                            label = "总冠军"
                            value = fact.value
                            unit = season_label
                            text = f"{season_label} 赛季总冠军是 **{fact.value}**。"
                        blocks.append(
                            AnswerBlock(
                                type=AnswerBlockType.FACT, label=label, value=value, unit=unit
                            )
                        )
                        lines.append(text)
                        continue
                label = {
                    "rank": "排名",
                    "wins": "胜场",
                    "losses": "负场",
                    "points": "得分",
                    "championship": "冠军",
                    "franchise_record": "冠军次数",
                    "league_record": "联盟纪录",
                    "series_record": "系列赛纪录",
                }.get(fact.predicate, fact.predicate)
                blocks.append(
                    AnswerBlock(
                        type=AnswerBlockType.FACT, label=label, value=fact.value, unit=fact.unit
                    )
                )
                lines.append(f"{fact.subject.display_name}：{label} **{fact.value}**。")

        if derived is not None and getattr(derived, "facts", None):
            series_facts = [fact for fact in derived.facts if fact.predicate == "series_wins"]
            if (
                series_facts
                and intent.metrics
                and getattr(intent.metrics[0].scope, "value", intent.metrics[0].scope) == "SERIES"
            ):
                series_facts = sorted(series_facts, key=lambda fact: fact.value, reverse=True)
                if len(series_facts) >= 2:
                    series_text = (
                        f"系列赛大比分：**{series_facts[0].subject.display_name} "
                        f"{series_facts[0].value}–{series_facts[1].value} "
                        f"{series_facts[1].subject.display_name}**。"
                    )
                    blocks.append(AnswerBlock(type=AnswerBlockType.TEXT, content=series_text))
                    lines.append(series_text)
            for fact in derived.facts:
                if fact.predicate in {
                    "series_wins",
                    "games_counted",
                    "recent_record",
                    "margin",
                    "total_points",
                }:
                    value = fact.value
                    if isinstance(value, dict):
                        value = "，".join(f"{key}{val}" for key, val in value.items())
                    label = {
                        "series_wins": "系列赛胜场",
                        "games_counted": "已计入场次",
                        "recent_record": "近期战绩",
                        "margin": "分差",
                        "total_points": "总得分",
                    }.get(fact.predicate, fact.predicate)
                    blocks.append(
                        AnswerBlock(
                            type=AnswerBlockType.FACT, label=label, value=value, unit=fact.unit
                        )
                    )
                    lines.append(f"{fact.subject.display_name}：{label} **{value}**。")

        if not lines or (not facts.facts and game is None and not public_corrections):
            message = "暂无匹配的公开数据，您可以补充球队、球员或日期。"
            blocks.append(AnswerBlock(type=AnswerBlockType.WARNING, content=message))
            lines.append(message)
            evidence = EvidenceState.NONE

        if retrieved_at is not None and evidence is not EvidenceState.NONE:
            freshness = (
                f"数据截至北京时间 {format_beijing(retrieved_at)}，{_evidence_label(evidence)}。"
            )
            blocks.append(AnswerBlock(type=AnswerBlockType.TEXT, content=freshness))
            lines.append(freshness)
        markdown = "\n\n".join(lines)
        return DraftAnswer(
            markdown=markdown,
            blocks=blocks,
            evidence_state=evidence,
            corrections=public_corrections,
        )

    def no_data(self, *, follow_up: str | None = None, message: str | None = None) -> DraftAnswer:
        message = message or "暂无匹配的公开数据，您可以调整日期或缩小查询范围。"
        return DraftAnswer(
            markdown=message,
            blocks=[AnswerBlock(type=AnswerBlockType.WARNING, content=message)],
            evidence_state=EvidenceState.NONE,
            follow_up=follow_up,
        )


def _evidence_label(state: EvidenceState) -> str:
    return {
        EvidenceState.VERIFIED: "已核验",
        EvidenceState.PARTIAL: "部分核验",
        EvidenceState.NONE: "暂无数据",
    }[state]


compose_template = TemplateComposer().compose


__all__ = ["TemplateComposer", "compose_template"]
