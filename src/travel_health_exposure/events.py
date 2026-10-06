"""领域事件记录：状态变化按信封契约落为只增事件。

事件类型在 ``contracts/domain.schema.json`` 中登记；本模块负责把领域服务的
动作翻译成事件负载，不承载业务判定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .timeutils import Clock, utcnow


# 已登记事件类型（与 domain.schema.json 保持一致）。
ITINERARY_CONFIRMED = "ITINERARY_CONFIRMED"
ITINERARY_CANCELLED = "ITINERARY_CANCELLED"
EXPOSURE_RECORDED = "EXPOSURE_RECORDED"
PROTECTION_RECORDED = "PROTECTION_RECORDED"
REMINDER_SCHEDULED = "REMINDER_SCHEDULED"
REMINDER_DELIVERED = "REMINDER_DELIVERED"
REMINDER_DELIVERY_FAILED = "REMINDER_DELIVERY_FAILED"
REMINDER_CONFIRMED = "REMINDER_CONFIRMED"
SYMPTOM_REPORTED = "SYMPTOM_REPORTED"
DIAGNOSIS_ATTACHED = "DIAGNOSIS_ATTACHED"
SHARE_GRANTED = "SHARE_GRANTED"
SHARE_REVOKED = "SHARE_REVOKED"
PUBLIC_HEALTH_RETAINED = "PUBLIC_HEALTH_RETAINED"
INVESTIGATION_OPENED = "INVESTIGATION_OPENED"
INVESTIGATION_CLOSED = "INVESTIGATION_CLOSED"


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    occurred_at: datetime
    version: int
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_envelope(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "occurred_at": self.occurred_at.isoformat(),
            "version": self.version,
            "summary": self.summary,
            **self.payload,
        }


class EventLog:
    """只增事件日志；序号即 event_id 的一部分，保证可复核顺序。"""

    def __init__(self, clock: Clock = utcnow) -> None:
        self._clock = clock
        self._events: list[DomainEvent] = []

    def append(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        version: int,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            event_id=f"evt-{len(self._events) + 1:06d}",
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            occurred_at=self._clock(),
            version=version,
            summary=summary,
            payload=dict(payload or {}),
        )
        self._events.append(event)
        return event

    def events(self) -> tuple[DomainEvent, ...]:
        return tuple(self._events)
