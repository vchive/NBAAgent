"""Focused readability regressions for deterministic answer composition."""

from types import SimpleNamespace

from apps.api.src.application.template_composer import _key_replay_events


def _event(
    name: str,
    *,
    points: int | None = None,
    shot_type: str = "NONE",
    action: str = "换人",
):
    return SimpleNamespace(
        name=name,
        points=points,
        shot_type=SimpleNamespace(value=shot_type),
        action_text=action,
    )


def test_long_replay_window_keeps_scores_and_decisive_attempts_not_substitution_dump() -> None:
    events = [
        _event("opening-miss", shot_type="TWO_POINT", action="两分跳投不中"),
        _event("sub-1"),
        _event("turnover", action="投篮时钟违例失误"),
        _event("free-throw-1", points=1, shot_type="FREE_THROW", action="第一罚命中"),
        _event("sub-2"),
        _event("sub-3"),
        _event("free-throw-2", points=1, shot_type="FREE_THROW", action="第二罚命中"),
        _event("late-miss", shot_type="TWO_POINT", action="突破跳投不中"),
        _event("blocked-shot", shot_type="TWO_POINT", action="两分上篮被封盖"),
        _event("sub-4"),
        _event("last-three", shot_type="THREE_POINT", action="三分跳投不中"),
        _event("putback", points=2, shot_type="TWO_POINT", action="补篮得分"),
    ]

    selected = _key_replay_events(events)
    names = [event.name for event in selected]

    assert len(selected) == 8
    assert {"free-throw-1", "free-throw-2", "putback"}.issubset(names)
    assert {"late-miss", "blocked-shot", "last-three"}.issubset(names)
    assert "turnover" in names
    assert not {"sub-1", "sub-2", "sub-3", "sub-4"}.intersection(names)
