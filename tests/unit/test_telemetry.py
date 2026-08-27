"""Privacy and short-circuit invariants for request telemetry."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.infrastructure.telemetry import (
    QueryTelemetry,
    TelemetrySink,
    hash_text,
    redact_text,
)
from apps.api.src.providers.fixture_provider import FixtureProvider


def test_hash_text_is_stable_one_way_and_bounded() -> None:
    message = "2025-26 总决赛 G4 谁得分最高？"

    digest = hash_text(message)

    assert digest == hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
    assert digest == hash_text(message)
    assert digest != hash_text(message + " ")
    assert len(digest) == 16
    assert message not in digest


def test_redact_text_replaces_controls_and_applies_limit() -> None:
    value = "  第一行\n第二行\x00\t第三行  "

    redacted = redact_text(value)

    assert redacted == "第一行 第二行  第三行"
    assert not any(ord(character) < 0x20 or ord(character) == 0x7F for character in redacted)
    assert redact_text("0123456789", limit=4) == "0123"
    assert redact_text("\x00\n\t", limit=20) == ""


def test_public_telemetry_contains_hashes_but_no_raw_message() -> None:
    message = "内部测试问题：请不要把这段原文写入日志"
    telemetry = QueryTelemetry(
        request_id=uuid4(),
        session_hash=hash_text("session-id"),
        message_hash=hash_text(message),
        deadline_at_utc=datetime.now(UTC) + timedelta(seconds=1),
    )
    telemetry.transition("SAFETY_CHECKED", safety="ALLOW")
    telemetry.finish(outcome="completed", total_latency_ms=12)

    public = telemetry.public_dict()
    serialized = json.dumps(public, ensure_ascii=False)

    assert public["message_hash"] == hash_text(message)
    assert public["session_hash"] == hash_text("session-id")
    assert public["request_id"] == str(telemetry.request_id)
    assert message not in serialized
    assert "raw_message" not in public
    # The projection is JSON-safe for exporters despite internal UUID/datetime
    # values on the dataclass.
    json.loads(serialized)


def test_telemetry_sink_keeps_only_the_newest_records() -> None:
    sink = TelemetrySink(max_records=2)
    records = [QueryTelemetry(request_id=uuid4(), session_hash=f"s-{index}") for index in range(3)]

    for record in records:
        sink.record(record)

    assert len(sink.records) == 2
    assert sink.latest() is records[-1]
    assert sink.records == records[1:]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_status"),
    [
        ("请给我比赛下注赔率", "blocked"),
        ("今天上海天气如何", "no_data"),
        ("未来总冠军是谁？", "no_data"),
    ],
)
async def test_short_circuit_branches_record_zero_downstream_calls(
    message: str, expected_status: str
) -> None:
    provider = FixtureProvider()
    telemetry = TelemetrySink()
    usecase = ChatUseCase(provider, telemetry=telemetry)

    result = await usecase.handle({"message": message})

    assert result.status == expected_status
    assert provider.calls == 0
    record = telemetry.latest()
    assert record is not None
    assert record.outcome == expected_status
    assert record.provider_call_count == 0
    assert record.cache_read_count == 0
    assert record.cache_write_count == 0
    assert record.cache_hit_count == 0
    assert record.message_hash == hash_text(message)
    assert message not in json.dumps(record.public_dict(), ensure_ascii=False)
