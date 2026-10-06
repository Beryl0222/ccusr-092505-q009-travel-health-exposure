"""管理者追溯：从一条提醒追到风险依据、用户确认和后续处置。

追溯视图面向管理/质控角色：

* 链路完整：提醒 → 风险依据（目录版本/来源/期限）→ 暴露事实 → 用户确认
  → 症状报告与后续处置 → 公共卫生通知脱敏留存；
* 身份最小化：视图中只出现假名，不出现 ``user_id``，也不串出无关旅客；
* 视图只读，追溯行为本身写审计。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .consent import ConsentService, pseudonymize
from .exposures import Exposure
from .monitoring import MonitoringService, ReminderStatus
from .risks import RiskCatalog
from .symptoms import SymptomService
from .timeutils import Clock, utcnow


class ConfirmationKind(str, Enum):
    REMINDER_CONFIRMED = "reminder_confirmed"
    FACT_CONFIRMED = "travel_fact_confirmed"


@dataclass(frozen=True)
class RiskBasis:
    advisory_code: str
    advisory_version: str
    title: str
    source: str
    monitor_days: int


@dataclass(frozen=True)
class ConfirmationNode:
    kind: ConfirmationKind
    at: datetime
    detail: str = ""


@dataclass(frozen=True)
class FollowUpNode:
    report_id: str
    reporter: str
    symptoms: tuple[str, ...]
    diagnosis_code: str | None
    actions: tuple[str, ...]
    notification_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReminderTrace:
    reminder_id: str
    pseudonym: str
    reminder_status: ReminderStatus
    due_date: str
    basis: RiskBasis
    exposure_type: str
    region: str
    window: tuple[str, str]
    confirmations: tuple[ConfirmationNode, ...]
    follow_ups: tuple[FollowUpNode, ...]

    @property
    def user_identity_exposed(self) -> bool:
        return False


@dataclass(frozen=True)
class TraceAudit:
    reminder_id: str
    manager_id: str
    viewed_at: datetime


class TraceabilityService:
    def __init__(
        self,
        catalog: RiskCatalog,
        monitoring: MonitoringService,
        symptoms: SymptomService,
        consent: ConsentService,
        salt: str,
        clock: Clock = utcnow,
    ) -> None:
        self._catalog = catalog
        self._monitoring = monitoring
        self._symptoms = symptoms
        self._consent = consent
        self._salt = salt
        self._clock = clock
        self._audit: list[TraceAudit] = []

    def trace(
        self,
        *,
        reminder_id: str,
        manager_id: str,
        exposure: Exposure,
        region: str | None = None,
    ) -> ReminderTrace:
        reminder = self._monitoring.get(reminder_id)
        advisory = self._catalog.get(reminder.advisory_code)
        now = self._clock()
        self._audit.append(TraceAudit(reminder_id, manager_id, now))

        confirmations: list[ConfirmationNode] = []
        if reminder.status is ReminderStatus.CONFIRMED and reminder.confirmed_at is not None:
            confirmations.append(
                ConfirmationNode(ConfirmationKind.REMINDER_CONFIRMED, reminder.confirmed_at)
            )
        for fact in self._consent.confirmed_facts(reminder.user_id):
            if fact.region == (region or advisory.region or ""):
                confirmations.append(
                    ConfirmationNode(
                        ConfirmationKind.FACT_CONFIRMED,
                        fact.confirmed_at,
                        detail=fact.fact_type,
                    )
                )

        follow_ups: list[FollowUpNode] = []
        notification_ids: set[str] = set()
        for retained in self._consent.deidentified_facts():
            if retained.pseudonym == pseudonymize(reminder.user_id, self._salt):
                notification_ids.update(retained.notification_ids)
        for report in self._symptoms.list_for_user(reminder.user_id):
            follow_ups.append(
                FollowUpNode(
                    report_id=report.report_id,
                    reporter=report.reporter.value,
                    symptoms=tuple(sorted(report.symptoms)),
                    diagnosis_code=report.diagnosis.condition_code if report.diagnosis else None,
                    actions=tuple(report.follow_ups),
                    notification_ids=tuple(sorted(notification_ids)),
                )
            )

        return ReminderTrace(
            reminder_id=reminder.reminder_id,
            pseudonym=pseudonymize(reminder.user_id, self._salt),
            reminder_status=reminder.status,
            due_date=reminder.due_date.isoformat(),
            basis=RiskBasis(
                advisory_code=advisory.code,
                advisory_version=advisory.advisory_version,
                title=advisory.title,
                source=advisory.source,
                monitor_days=advisory.monitor_days,
            ),
            exposure_type=exposure.exposure_type,
            region=region or "",
            window=(exposure.window_start.isoformat(), exposure.window_end.isoformat()),
            confirmations=tuple(confirmations),
            follow_ups=tuple(follow_ups),
        )

    def audit_log(self) -> tuple[TraceAudit, ...]:
        return tuple(self._audit)
