"""Privacy-aware request telemetry.

Telemetry is intentionally an in-memory sink for v1.  A production exporter can consume the
same records without changing the application contract.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger("nba_agent.telemetry")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def redact_text(value: str, limit: int = 160) -> str:
    cleaned = _CONTROL_RE.sub(" ", str(value)).strip()
    return cleaned[:limit]


@dataclass(slots=True)
class QueryTelemetry:
    request_id: UUID
    session_hash: str
    phase: str = "RECEIVED"
    outcome: str | None = None
    intent_category: str | None = None
    intent_name: str | None = None
    safety_category: str | None = None
    provider_call_count: int = 0
    cache_read_count: int = 0
    cache_write_count: int = 0
    cache_hit_count: int = 0
    evidence_state: str = "none"
    admission_result: str | None = None
    queue_wait_ms: int | None = None
    deadline_at_utc: datetime | None = None
    ttft_ms: int | None = None
    total_latency_ms: int | None = None
    error_code: str | None = None
    hermes_mode: str | None = None
    hermes_status: str | None = None
    fallback_reason: str | None = None
    # Publicly projected by the application as a small, provider-neutral
    # explanation of where the answer came from.  These fields remain
    # internal telemetry here so exporters can correlate model/fallback
    # behaviour without retaining prompts, keys, or raw provider payloads.
    composition_mode: str = "deterministic"
    composition_status: str = "not_requested"
    composition_latency_ms: int = 0
    message_hash: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, phase: str, **details: Any) -> None:
        self.phase = phase
        event = {"phase": phase, "at_utc": datetime.now(UTC).isoformat()}
        event.update({key: value for key, value in details.items() if value is not None})
        self.events.append(event)

    def finish(self, *, outcome: str, total_latency_ms: int | None = None) -> None:
        self.outcome = outcome
        self.phase = "FAILED" if outcome == "failed" else "COMPLETED"
        self.total_latency_ms = total_latency_ms
        self.events.append({"phase": self.phase, "outcome": outcome})

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        # UUID/datetime values are rendered only for internal exporters; no raw message is kept.
        result["request_id"] = str(self.request_id)
        if self.deadline_at_utc:
            result["deadline_at_utc"] = self.deadline_at_utc.isoformat()
        return result


class TelemetrySink:
    def __init__(self, *, max_records: int = 2_000) -> None:
        self.max_records = max_records
        self.records: list[QueryTelemetry] = []

    def record(self, telemetry: QueryTelemetry) -> None:
        self.records.append(telemetry)
        if len(self.records) > self.max_records:
            del self.records[: len(self.records) - self.max_records]
        logger.info(
            "query_complete request=%s outcome=%s intent=%s provider_calls=%d evidence=%s "
            "composition=%s composition_status=%s composition_latency_ms=%d fallback=%s",
            telemetry.request_id,
            telemetry.outcome,
            telemetry.intent_name,
            telemetry.provider_call_count,
            telemetry.evidence_state,
            telemetry.composition_mode,
            telemetry.composition_status,
            telemetry.composition_latency_ms,
            telemetry.fallback_reason or "none",
        )

    def latest(self) -> QueryTelemetry | None:
        return self.records[-1] if self.records else None
