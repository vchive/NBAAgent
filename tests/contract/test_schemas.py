from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.api.src.api.schemas import (
    AnswerBlock,
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    HighlightAvailabilityDay,
    HighlightsAvailabilityResponse,
    HighlightsResponse,
    MessageDeltaPayload,
    RunStatusPayload,
)
from apps.api.src.api.sse import serialize_event


def test_chat_request_trims_message_and_validates_timezone() -> None:
    request = ChatRequest(message="  湖人今天赛程  ", client_timezone="Asia/Shanghai")
    assert request.message == "湖人今天赛程"
    with pytest.raises(ValidationError):
        ChatRequest(message="x", client_timezone="Not/AZone")


def test_chat_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="湖人", provider_url="https://example.invalid")


def test_answer_block_uses_lowercase_wire_type_and_ignores_unknown_fields() -> None:
    block = AnswerBlock.model_validate(
        {"type": "fact", "label": "得分", "value": 118, "source_ref": "internal"}
    )
    assert block.model_dump(mode="json")["type"] == "fact"
    assert "source_ref" not in block.model_dump()


def test_chat_response_has_contract_shape_and_lowercase_enums() -> None:
    response = ChatResponse(
        request_id=uuid4(),
        session_id=uuid4(),
        status="completed",
        answer_markdown="已核验。",
        evidence_state="verified",
        as_of_beijing="2026-08-26 21:30",
        latency_ms=12,
    )
    payload = response.model_dump(mode="json")
    assert payload["status"] == "completed"
    assert payload["evidence_state"] == "verified"
    assert payload["as_of_beijing"] == "2026-08-26 21:30"


def test_highlights_response_validates_freshness_timestamp() -> None:
    response = HighlightsResponse(
        date="2026-08-26",
        timezone="Asia/Shanghai",
        games=[],
        as_of_beijing="2026-08-26 21:30",
        evidence_state="none",
    )
    assert response.model_dump(mode="json")["as_of_beijing"] == "2026-08-26 21:30"

    for value in ("2026-8-26 21:30", "2026-02-30 21:30", "2026-08-26T21:30", "raw-upstream-value"):
        with pytest.raises(ValidationError):
            HighlightsResponse(
                date="2026-08-26",
                timezone="Asia/Shanghai",
                games=[],
                as_of_beijing=value,
                evidence_state="none",
            )


def test_technical_error_envelope_is_explicit() -> None:
    response = ErrorResponse(
        request_id=uuid4(),
        session_id=uuid4(),
        error={"code": "UPSTREAM_TIMEOUT", "retryable": True, "message": "请稍后重试"},
    )
    assert response.model_dump(mode="json")["status"] == "failed"
    with pytest.raises(ValidationError):
        ChatResponse(
            request_id=uuid4(),
            session_id=uuid4(),
            status="completed",
            answer_markdown="ok",
            evidence_state="none",
            as_of_beijing="not-a-timestamp",
            latency_ms=1,
        )


def test_highlights_availability_schema_uses_aliases_and_contiguous_days() -> None:
    response = HighlightsAvailabilityResponse(
        timezone="Asia/Shanghai",
        from_date="2026-06-06",
        to_date="2026-06-07",
        days=[
            HighlightAvailabilityDay(date="2026-06-06", status="available", game_count=1),
            HighlightAvailabilityDay(date="2026-06-07", status="empty", game_count=0),
        ],
        evidence_state="verified",
    )
    assert response.model_dump(mode="json", by_alias=True)["from"] == "2026-06-06"
    with pytest.raises(ValidationError):
        HighlightsAvailabilityResponse(
            timezone="Asia/Shanghai",
            from_date="2026-06-06",
            to_date="2026-06-07",
            days=[
                HighlightAvailabilityDay(date="2026-06-06", status="empty", game_count=0),
                HighlightAvailabilityDay(date="2026-06-08", status="empty", game_count=0),
            ],
            evidence_state="none",
        )


def test_sse_event_has_event_and_data_lines() -> None:
    request_id, session_id = uuid4(), uuid4()
    frame = serialize_event("run.started", {"request_id": request_id, "session_id": session_id})
    assert frame.startswith("event: run.started\ndata: ")
    assert frame.endswith("\n\n")


def test_answer_blocks_reject_nested_provider_values_and_nonfinite_scalars() -> None:
    with pytest.raises(ValidationError):
        AnswerBlock.model_validate(
            {"type": "fact", "label": "来源", "value": {"source_ref": "secret"}}
        )
    with pytest.raises(ValidationError):
        AnswerBlock.model_validate(
            {
                "type": "table",
                "columns": ["球队", "数据"],
                "rows": [["示例", {"url": "https://internal.invalid"}]],
            }
        )
    with pytest.raises(ValidationError):
        AnswerBlock.model_validate({"type": "fact", "label": "数据", "value": float("nan")})


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ChatResponse(
            request_id=uuid4(),
            session_id=uuid4(),
            status="completed",
            answer_markdown="详情见 https://provider.invalid/source_ref",
            evidence_state="none",
            latency_ms=1,
        ),
        lambda: AnswerBlock(type="text", content="provider_call_count=1"),
        lambda: ErrorResponse(
            request_id=uuid4(),
            session_id=uuid4(),
            error={
                "code": "SERVICE_BUSY",
                "retryable": True,
                "message": "traceback: https://internal.invalid",
            },
        ),
        lambda: MessageDeltaPayload(text="raw_json from ESPN"),
    ],
)
def test_public_text_rejects_urls_and_internal_provider_fields(factory) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_run_status_rejects_unverified_numbers_but_allows_safe_copy() -> None:
    assert RunStatusPayload(stage="verifying", text="正在核对比赛数据")
    assert RunStatusPayload(stage="custom", text="正在处理请求")
    with pytest.raises(ValidationError):
        RunStatusPayload(stage="custom", text="已找到 32 分")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ChatRequest(message="问题\x7f"),
        lambda: ChatResponse(
            request_id=uuid4(),
            session_id=uuid4(),
            status="completed",
            answer_markdown="答案\x7f",
            evidence_state="none",
            latency_ms=0,
        ),
        lambda: RunStatusPayload(stage="verifying", text="进度\x7f"),
        lambda: MessageDeltaPayload(text="增量\x7f"),
    ],
)
def test_wire_text_rejects_ascii_del(factory) -> None:
    with pytest.raises(ValidationError):
        factory()
