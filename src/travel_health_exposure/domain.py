"""旅居健康暴露追踪器的领域对象、枚举与内存存储。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


class ConsentScopeError(ValueError):
    """登记内容超出用户出发前选择的自愿范围。"""


class ExposureType(str, Enum):
    """目的地风险提示覆盖的暴露类型。"""

    MOSQUITO_BORNE = "mosquito_borne"
    FOODBORNE_GATHERING = "foodborne_gathering"
    POLLEN = "pollen"


MONITORING_DURATIONS: dict[ExposureType, timedelta] = {
    ExposureType.MOSQUITO_BORNE: timedelta(days=14),
    ExposureType.FOODBORNE_GATHERING: timedelta(days=7),
    ExposureType.POLLEN: timedelta(days=3),
}
"""返程后不同暴露类型对应的自我监测期限。"""


class ReminderState(str, Enum):
    SCHEDULED = "scheduled"
    SENT = "sent"
    SEND_FAILED = "send_failed"
    ACKNOWLEDGED = "acknowledged"


class ReporterRole(str, Enum):
    SELF = "self"
    CLINICIAN = "clinician"


class ShareStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class ConsentScope:
    """出发前用户选择的风险提示类别与自愿记录范围。"""

    traveler_id: str
    risk_categories: frozenset[ExposureType]
    record_stays: bool
    record_contacts: bool
    record_protections: bool
    granted_at: datetime


@dataclass(frozen=True)
class StayInterval:
    """途中登记的一段住宿区间。"""

    start: datetime
    end: datetime
    region: str


@dataclass(frozen=True)
class ContactEvent:
    """途中登记的一次接触事件，如聚餐。"""

    kind: str
    occurred_at: datetime
    detail: str


@dataclass(frozen=True)
class ProtectionMeasure:
    """途中登记的一项防护措施。"""

    kind: str
    applied_at: datetime


@dataclass
class TravelItinerary:
    """已确认的行程；改签保持同一身份，仅更新班次与时间。"""

    itinerary_id: str
    traveler_id: str
    group_id: str | None
    destination_region: str
    depart_at: datetime
    return_at: datetime
    booking_ref: str
    version: int
    stays: list[StayInterval] = field(default_factory=list)
    contacts: list[ContactEvent] = field(default_factory=list)
    protections: list[ProtectionMeasure] = field(default_factory=list)


@dataclass
class ExposureRecord:
    """一条去重后的暴露事实。"""

    exposure_id: str
    itinerary_id: str
    traveler_id: str
    exposure_type: ExposureType
    region: str
    window_start: datetime
    window_end: datetime
    group_id: str | None
    scan_ids: set[str] = field(default_factory=set)


@dataclass
class HealthReminder:
    """返程后的自我监测提醒，monitor_until 为本地期限。"""

    reminder_id: str
    exposure_id: str
    traveler_id: str
    exposure_type: ExposureType
    monitor_until: datetime
    state: ReminderState = ReminderState.SCHEDULED
    attempts: int = 0
    acknowledged_at: datetime | None = None
    last_error: str | None = None


@dataclass
class ClinicalShare:
    """就医时按授权向医生开放的旅居史快照。"""

    share_id: str
    traveler_id: str
    clinician_id: str
    authorized_fields: frozenset[str]
    snapshot: dict[str, Any]
    status: ShareStatus
    created_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class DeidentifiedNotice:
    """已用于公共卫生通知的事实，保留不含身份的脱敏版本。"""

    notice_id: str
    share_id: str
    exposure_type: ExposureType
    region: str
    window_start: datetime
    window_end: datetime
    created_at: datetime


@dataclass(frozen=True)
class RuleAlert:
    """系统规则提示，kind 恒为 rule，永远不是诊断。"""

    rule_id: str
    exposure_type: ExposureType
    message: str
    kind: str = "rule"


@dataclass
class SymptomReport:
    """本人或医生上报的异常症状；诊断由医生另行登记。"""

    report_id: str
    traveler_id: str
    reporter: ReporterRole
    symptoms: tuple[str, ...]
    reported_at: datetime
    rule_alerts: list[RuleAlert] = field(default_factory=list)
    diagnosis: str | None = None
    diagnosed_by: str | None = None


@dataclass
class AccessGrant:
    """跨区域卫生人员的一次最小必要查询授权。"""

    grant_id: str
    officer_id: str
    region: str
    purpose: str
    allowed_fields: frozenset[str]
    opened_at: datetime
    closed_at: datetime | None = None


@dataclass(frozen=True)
class AuditEntry:
    actor: str
    action: str
    target_id: str
    occurred_at: datetime
    detail: str


@dataclass
class TrackerStore:
    """内存存储：业务实体、审计日志与符合交换契约的领域事件。"""

    consents: dict[str, ConsentScope] = field(default_factory=dict)
    itineraries: dict[str, TravelItinerary] = field(default_factory=dict)
    exposures: dict[str, ExposureRecord] = field(default_factory=dict)
    reminders: dict[str, HealthReminder] = field(default_factory=dict)
    shares: dict[str, ClinicalShare] = field(default_factory=dict)
    notices: dict[str, DeidentifiedNotice] = field(default_factory=dict)
    reports: dict[str, SymptomReport] = field(default_factory=dict)
    grants: dict[str, AccessGrant] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    scan_index: dict[str, str] = field(default_factory=dict)


def record_audit(
    store: TrackerStore,
    *,
    actor: str,
    action: str,
    target_id: str,
    occurred_at: datetime,
    detail: str = "",
) -> None:
    """追加一条审计记录；审计只增不改，调查结束后仍保留。"""
    store.audit.append(
        AuditEntry(actor=actor, action=action, target_id=target_id, occurred_at=occurred_at, detail=detail)
    )


def emit_event(
    store: TrackerStore,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    occurred_at: datetime,
    summary: str,
) -> dict[str, Any]:
    """按交换契约登记领域事件；发生时间必须带时区。"""
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("事件发生时间必须包含时区")
    version = sum(1 for event in store.events if event["aggregate_id"] == aggregate_id) + 1
    event = {
        "event_id": f"evt-{len(store.events) + 1:06d}",
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "occurred_at": occurred_at.isoformat(),
        "version": version,
        "summary": summary,
    }
    store.events.append(event)
    return event
