"""旅居健康暴露追踪器装配门面。

把风险目录、暴露登记、监测提醒、同意共享、症状上报、跨区域监管和追溯
装配成一条完整旅程：出发前选择 → 途中登记 → 返程监测 → 就医共享/上报 → 管理追溯。
状态服务与事件日志分离：服务判定不变量，日志只做只增留痕。
"""

from __future__ import annotations

from datetime import date

from .consent import (
    ConfirmedTravelFact,
    ConsentService,
    DeidentifiedFact,
    RecordScope,
)
from .events import (
    DIAGNOSIS_ATTACHED,
    EXPOSURE_RECORDED,
    EventLog,
    INVESTIGATION_CLOSED,
    INVESTIGATION_OPENED,
    ITINERARY_CANCELLED,
    ITINERARY_CONFIRMED,
    PROTECTION_RECORDED,
    PUBLIC_HEALTH_RETAINED,
    REMINDER_CONFIRMED,
    REMINDER_DELIVERED,
    REMINDER_DELIVERY_FAILED,
    REMINDER_SCHEDULED,
    SHARE_GRANTED,
    SHARE_REVOKED,
    SYMPTOM_REPORTED,
)
from .exposures import Exposure, ExposureRegistry, StayInterval, Trip, TripStatus
from .monitoring import DeliveryGateway, DispatchOutcome, MonitoringService
from .oversight import OversightService
from .protections import ProtectionLog, ProtectionMeasure
from .risks import RiskCatalog
from .symptoms import SymptomReport, SymptomService
from .timeutils import Clock, utcnow
from .trace import ReminderTrace, TraceabilityService

# 共享脱敏与追溯假名使用同一盐，保证跨模块假名一致、身份仍不可逆。
DEFAULT_SALT = "travel-health-exposure-2026"


class TravelHealthTracker:
    def __init__(
        self,
        gateway: DeliveryGateway,
        catalog: RiskCatalog | None = None,
        salt: str = DEFAULT_SALT,
        clock: Clock = utcnow,
    ) -> None:
        self._clock = clock
        self.salt = salt
        self.catalog = catalog or RiskCatalog()
        self.events = EventLog(clock)
        self.consent = ConsentService(salt, clock)
        self.symptoms = SymptomService(self.catalog, clock)
        self.monitoring = MonitoringService(self.catalog, gateway, clock)
        self.oversight = OversightService(salt, clock)
        self.traceability = TraceabilityService(
            self.catalog, self.monitoring, self.symptoms, self.consent, salt, clock
        )
        self._registries: dict[str, ExposureRegistry] = {}
        self._protection_logs: dict[str, ProtectionLog] = {}
        self._exposure_index: dict[str, Exposure] = {}
        self._trips: dict[str, Trip] = {}

    # -- 出发前：行程簿（退订后本地仍保留） ----------------------------

    def plan_trip(
        self,
        *,
        trip_id: str,
        user_id: str,
        regions: tuple[str, ...],
        booking_refs: frozenset[str] = frozenset(),
        companions: frozenset[str] = frozenset(),
    ) -> Trip:
        trip = Trip(
            trip_id=trip_id,
            user_id=user_id,
            regions=tuple(regions),
            booking_refs=set(booking_refs),
            companions=frozenset(companions),
        )
        self._trips[trip_id] = trip
        self.events.append(
            event_type=ITINERARY_CONFIRMED,
            aggregate_type="travel_itinerary",
            aggregate_id=trip_id,
            version=trip.version,
            summary="行程确认并在本地留档，平台退订不影响本地事实",
            payload={"user_id": user_id, "regions": list(regions)},
        )
        return trip

    def upsert_stay(self, trip_id: str, stay: StayInterval) -> Trip:
        """住宿区间登记/改签：区间标识稳定，不产生重复事实。"""
        trip = self._trips[trip_id]
        trip.upsert_stay(stay)
        return trip

    def cancel_trip(self, trip_id: str) -> None:
        """退订只改状态，本地行程与已登记暴露保留。"""
        trip = self._trips[trip_id]
        trip.cancel()
        self.events.append(
            event_type=ITINERARY_CANCELLED,
            aggregate_type="travel_itinerary",
            aggregate_id=trip_id,
            version=trip.version,
            summary="行程退订：状态标记取消，本地事实保留",
            payload={"status": TripStatus.CANCELLED.value},
        )

    def trip(self, trip_id: str) -> Trip:
        return self._trips[trip_id]

    # -- 出发前 --------------------------------------------------------

    def choose_recording_scope(self, user_id: str, scopes: frozenset[RecordScope]) -> None:
        self.consent.set_preference(user_id, scopes)

    def _registry(self, user_id: str) -> ExposureRegistry:
        registry = self._registries.get(user_id)
        if registry is None:
            registry = ExposureRegistry(user_id, self._clock)
            self._registries[user_id] = registry
        return registry

    def _protection_log(self, user_id: str) -> ProtectionLog:
        log = self._protection_logs.get(user_id)
        if log is None:
            log = ProtectionLog(user_id)
            self._protection_logs[user_id] = log
        return log

    # -- 途中登记 ------------------------------------------------------

    def register_exposure(self, user_id: str, **kwargs: object) -> Exposure:
        if not self.consent.recording_allowed(user_id, RecordScope.EXPOSURES):
            raise PermissionError("用户未授权登记接触事件")
        result = self._registry(user_id).register(**kwargs)
        self._exposure_index[result.exposure.exposure_id] = result.exposure
        if result.created:
            self.events.append(
                event_type=EXPOSURE_RECORDED,
                aggregate_type="exposure_record",
                aggregate_id=result.exposure.exposure_id,
                version=result.exposure.version,
                summary="登记接触事件（归并后一条事实）",
                payload={
                    "user_id": user_id,
                    "exposure_type": result.exposure.exposure_type,
                    "location_id": result.exposure.location_id,
                    "window_start": result.exposure.window_start.isoformat(),
                    "window_end": result.exposure.window_end.isoformat(),
                    "merged_scan": result.merged_scan,
                },
            )
        return result.exposure

    def record_protection(self, user_id: str, **kwargs: object) -> ProtectionMeasure:
        if not self.consent.recording_allowed(user_id, RecordScope.PROTECTIONS):
            raise PermissionError("用户未授权登记防护措施")
        log = self._protection_log(user_id)
        before = {m.measure_id for m in log.list_measures()}
        measure = log.record(**kwargs)
        if measure.measure_id not in before:
            self.events.append(
                event_type=PROTECTION_RECORDED,
                aggregate_type="exposure_record",
                aggregate_id=measure.measure_id,
                version=1,
                summary="登记防护措施",
                payload={"user_id": user_id, "kind": measure.kind, "date": measure.date.isoformat()},
            )
        return measure

    # -- 返程监测 ------------------------------------------------------

    def schedule_monitoring(self, exposure_id: str, region: str | None = None):
        exposure = self._exposure_index[exposure_id]
        reminder = self.monitoring.schedule_for_exposure(exposure, region)
        self.events.append(
            event_type=REMINDER_SCHEDULED,
            aggregate_type="health_reminder",
            aggregate_id=reminder.reminder_id,
            version=reminder.basis_version,
            summary=f"按 {reminder.advisory_code} 排程自我监测至 {reminder.due_date.isoformat()}",
            payload={
                "user_id": exposure.user_id,
                "exposure_id": exposure_id,
                "advisory_code": reminder.advisory_code,
                "due_date": reminder.due_date.isoformat(),
            },
        )
        return reminder

    def dispatch_due(self, today: date) -> tuple[DispatchOutcome, ...]:
        outcomes = self.monitoring.dispatch_due(today)
        for outcome in outcomes:
            event_type = REMINDER_DELIVERED if outcome.delivered else REMINDER_DELIVERY_FAILED
            self.events.append(
                event_type=event_type,
                aggregate_type="health_reminder",
                aggregate_id=outcome.reminder.reminder_id,
                version=outcome.reminder.attempts,
                summary="提醒送达" if outcome.delivered else "提醒发送失败，本地期限保留",
                payload={"error": outcome.error},
            )
        return outcomes

    def confirm_reminder(self, reminder_id: str):
        reminder = self.monitoring.confirm(reminder_id)
        self.events.append(
            event_type=REMINDER_CONFIRMED,
            aggregate_type="health_reminder",
            aggregate_id=reminder_id,
            version=1,
            summary="用户已确认自我监测提醒",
        )
        return reminder

    # -- 症状与诊断 ----------------------------------------------------

    def report_symptoms(self, user_id: str, **kwargs: object) -> SymptomReport:
        if not self.consent.recording_allowed(user_id, RecordScope.SYMPTOMS):
            raise PermissionError("用户未授权登记异常症状")
        report = self.symptoms.report(user_id=user_id, **kwargs)
        self.events.append(
            event_type=SYMPTOM_REPORTED,
            aggregate_type="symptom_report",
            aggregate_id=report.report_id,
            version=1,
            summary=f"{report.reporter.value} 上报异常症状",
            payload={"rule_hint_codes": [h.advisory_code for h in report.rule_hints]},
        )
        return report

    def attach_diagnosis(self, report_id: str, clinician_id: str, condition_code: str, note: str = ""):
        diagnosis = self.symptoms.attach_diagnosis(
            report_id=report_id,
            clinician_id=clinician_id,
            condition_code=condition_code,
            note=note,
        )
        self.events.append(
            event_type=DIAGNOSIS_ATTACHED,
            aggregate_type="symptom_report",
            aggregate_id=report_id,
            version=1,
            summary="医生挂接诊断，与规则提示分离",
            payload={"clinician_id": clinician_id, "condition_code": condition_code},
        )
        return diagnosis

    # -- 就医共享 ------------------------------------------------------

    def confirm_travel_fact(self, user_id: str, fact: ConfirmedTravelFact) -> None:
        self.consent.confirm_fact(user_id, fact)

    def grant_clinical_share(self, **kwargs: object):
        grant = self.consent.grant_share(**kwargs)
        self.events.append(
            event_type=SHARE_GRANTED,
            aggregate_type="clinical_share",
            aggregate_id=grant.grant_id,
            version=1,
            summary="用户授权医生查看确认旅居史",
            payload={"jurisdiction": grant.jurisdiction, "fields": sorted(grant.fields)},
        )
        return grant

    def revoke_clinical_share(self, grant_id: str) -> None:
        self.consent.revoke(grant_id)
        self.events.append(
            event_type=SHARE_REVOKED,
            aggregate_type="clinical_share",
            aggregate_id=grant_id,
            version=2,
            summary="用户撤回共享，仅影响未来查看",
        )

    # -- 公共卫生通知留存 ----------------------------------------------

    def retain_notification_fact(self, fact: DeidentifiedFact) -> None:
        self.consent.retain_for_public_health(fact)
        self.events.append(
            event_type=PUBLIC_HEALTH_RETAINED,
            aggregate_type="public_health_fact",
            aggregate_id=fact.pseudonym,
            version=1,
            summary="通知事实脱敏留存，撤回不可还原身份",
            payload={
                "region": fact.region,
                "advisory_code": fact.advisory_code,
                "notification_ids": list(fact.notification_ids),
            },
        )

    # -- 管理追溯 ------------------------------------------------------

    def trace_reminder(self, reminder_id: str, manager_id: str, region: str | None = None) -> ReminderTrace:
        exposure = self._exposure_index[self.monitoring.get(reminder_id).exposure_id]
        return self.traceability.trace(
            reminder_id=reminder_id,
            manager_id=manager_id,
            exposure=exposure,
            region=region,
        )

    def exposure(self, exposure_id: str) -> Exposure:
        return self._exposure_index[exposure_id]

    # -- 跨区域调查 ----------------------------------------------------

    def open_investigation(self, **kwargs: object):
        case = self.oversight.open_case(**kwargs)
        self.events.append(
            event_type=INVESTIGATION_OPENED,
            aggregate_type="investigation_case",
            aggregate_id=case.case_id,
            version=1,
            summary="开启跨区域调查，最小字段授权",
            payload={"jurisdictions": sorted(case.jurisdictions)},
        )
        return case

    def close_investigation(self, case_id: str) -> None:
        self.oversight.close_case(case_id)
        self.events.append(
            event_type=INVESTIGATION_CLOSED,
            aggregate_type="investigation_case",
            aggregate_id=case_id,
            version=2,
            summary="调查结束，访问关闭，审计保留",
        )
