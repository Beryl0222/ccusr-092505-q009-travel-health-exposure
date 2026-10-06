from __future__ import annotations

import json
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from travel_health_exposure import (
    ConfirmedTravelFact,
    DeidentifiedFact,
    ExposureRegistry,
    MonitoringService,
    OversightService,
    RecordScope,
    Reporter,
    RiskCatalog,
    StayInterval,
    SymptomService,
    TravelHealthTracker,
    TripStatus,
    validate_event,
)
from travel_health_exposure.consent import ConsentService
from travel_health_exposure.monitoring import ReminderStatus
from travel_health_exposure.oversight import (
    FIELD_PSEUDONYM,
    FIELD_REGION,
    FIELD_USER_ID,
    MINIMAL_FIELDS,
)

CN_SH = "CN-SH"
CN_GD = "CN-GD"
TZ = timezone(timedelta(hours=8))


class FlakyGateway:
    """可配置失败次数的发送通道。"""

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.sent: list[str] = []

    def send(self, reminder) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("通道不可用")
        self.sent.append(reminder.reminder_id)


def aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=TZ)


class ExposureDedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ExposureRegistry(user_id="u-1")

    def test_repeat_scan_of_same_place_is_one_exposure(self) -> None:
        r1 = self.registry.register(
            exposure_type="mosquito_borne",
            location_id="loc-night-market",
            window_start=date(2026, 10, 1),
            scan_code="scan-777",
        )
        r2 = self.registry.register(
            exposure_type="mosquito_borne",
            location_id="loc-night-market",
            window_start=date(2026, 10, 1),
            scan_code="scan-777",
        )
        self.assertEqual(1, len(self.registry.list_exposures()))
        self.assertTrue(r1.created)
        self.assertFalse(r2.created)
        self.assertTrue(r2.merged_scan)
        self.assertIs(r1.exposure, r2.exposure)

    def test_overlapping_registrations_at_same_location_merge(self) -> None:
        self.registry.register(
            exposure_type="foodborne",
            location_id="loc-banquet",
            window_start=date(2026, 10, 2),
            window_end=date(2026, 10, 2),
        )
        r2 = self.registry.register(
            exposure_type="foodborne",
            location_id="loc-banquet",
            window_start=date(2026, 10, 2),
            window_end=date(2026, 10, 3),
        )
        self.assertFalse(r2.created)
        self.assertEqual(1, len(self.registry.list_exposures()))
        exposure = r2.exposure
        self.assertEqual((date(2026, 10, 2), date(2026, 10, 3)),
                         (exposure.window_start, exposure.window_end))
        self.assertGreaterEqual(exposure.version, 2)

    def test_companions_attach_to_single_exposure(self) -> None:
        r1 = self.registry.register(
            exposure_type="foodborne",
            location_id="loc-banquet",
            window_start=date(2026, 10, 2),
            companion_ids=frozenset({"u-2"}),
        )
        r2 = self.registry.register(
            exposure_type="foodborne",
            location_id="loc-banquet",
            window_start=date(2026, 10, 2),
            companion_ids=frozenset({"u-3"}),
        )
        self.assertIs(r1.exposure, r2.exposure)
        self.assertEqual({"u-2", "u-3"}, set(r2.exposure.companion_ids))

    def test_itinerary_change_keeps_one_exposure_and_moves_window(self) -> None:
        self.registry.register(
            exposure_type="mosquito_borne",
            location_id="loc-hotel",
            window_start=date(2026, 10, 1),
            window_end=date(2026, 10, 3),
            stay_id="stay-1",
        )
        before = self.registry.reschedule("stay-1", check_in=date(2026, 10, 5), check_out=date(2026, 10, 7))
        self.assertEqual(1, len(self.registry.list_exposures()))
        self.assertEqual((date(2026, 10, 5), date(2026, 10, 7)),
                         (before.window_start, before.window_end))
        self.assertGreaterEqual(before.version, 2)

    def test_distinct_exposure_types_or_regions_remain_separate(self) -> None:
        self.registry.register(
            exposure_type="mosquito_borne", location_id="loc-a",
            window_start=date(2026, 10, 1),
        )
        self.registry.register(
            exposure_type="foodborne", location_id="loc-a",
            window_start=date(2026, 10, 1),
        )
        self.assertEqual(2, len(self.registry.list_exposures()))


class MonitoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = RiskCatalog()

    def _exposure(self, registry: ExposureRegistry, exposure_type: str, end: date, **kw):
        return registry.register(
            exposure_type=exposure_type,
            location_id="loc-x",
            window_start=end - timedelta(days=1),
            window_end=end,
            **kw,
        ).exposure

    def test_different_exposure_types_get_different_windows(self) -> None:
        gateway = FlakyGateway()
        service = MonitoringService(self.catalog, gateway)
        mosquito = self._exposure(ExposureRegistry("u-1"), "mosquito_borne", date(2026, 10, 1))
        food = self._exposure(ExposureRegistry("u-2"), "foodborne", date(2026, 10, 1))
        r1 = service.schedule_for_exposure(mosquito)
        r2 = service.schedule_for_exposure(food)
        self.assertEqual(date(2026, 10, 15), r1.due_date)  # 14 天
        self.assertEqual(date(2026, 10, 8), r2.due_date)   # 7 天
        self.assertIn("非诊断", r1.message)

    def test_failed_send_keeps_local_deadline_and_replays_only_unconfirmed(self) -> None:
        gateway = FlakyGateway(fail_times=1)
        service = MonitoringService(self.catalog, gateway)
        exposure = self._exposure(ExposureRegistry("u-1"), "foodborne", date(2026, 10, 1))
        reminder = service.schedule_for_exposure(exposure)
        due = reminder.due_date

        outcomes = service.dispatch_due(due)
        self.assertFalse(outcomes[0].delivered)
        self.assertEqual(ReminderStatus.FAILED, reminder.status)
        self.assertEqual(due, reminder.due_date)  # 期限未丢
        self.assertEqual("", gateway.sent and gateway.sent[0] or "")

        recovered = service.recover(due)
        self.assertTrue(recovered[0].delivered)
        self.assertEqual(ReminderStatus.SENT, reminder.status)

        service.confirm(reminder.reminder_id)
        again = service.recover(due + timedelta(days=2))
        self.assertEqual((), again)  # 已确认不补发
        self.assertEqual(1, gateway.sent.count(reminder.reminder_id))

    def test_reschedule_only_touches_unconfirmed_reminder(self) -> None:
        gateway = FlakyGateway()
        service = MonitoringService(self.catalog, gateway)
        registry = ExposureRegistry("u-1")
        exposure = self._exposure(registry, "foodborne", date(2026, 10, 1), stay_id="stay-1")
        reminder = service.schedule_for_exposure(exposure)
        first_due = reminder.due_date

        registry.reschedule("stay-1", check_in=date(2026, 10, 9), check_out=date(2026, 10, 10))
        service.schedule_for_exposure(registry.get(exposure.exposure_id))
        self.assertEqual(date(2026, 10, 17), reminder.due_date)
        self.assertNotEqual(first_due, reminder.due_date)

        # 确认后再次改签：提醒冻结。
        service.confirm(reminder.reminder_id)
        registry.reschedule("stay-1", check_in=date(2026, 11, 1), check_out=date(2026, 11, 2))
        service.schedule_for_exposure(registry.get(exposure.exposure_id))
        self.assertEqual(ReminderStatus.CONFIRMED, reminder.status)
        self.assertEqual(date(2026, 10, 17), reminder.due_date)


class ConsentAndShareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.consent = ConsentService(salt="s")
        self.fact = ConfirmedTravelFact(
            fact_type="exposure",
            region=CN_SH,
            started_on="2026-10-01",
            ended_on="2026-10-03",
            detail="蚊媒流行区夜间停留",
            confirmed_at=aware(datetime(2026, 10, 5, 9, 0)),
        )
        self.consent.set_preference("u-1", frozenset(RecordScope))
        self.consent.confirm_fact("u-1", self.fact)

    def test_recording_scope_gates_registration(self) -> None:
        self.assertTrue(self.consent.recording_allowed("u-1", RecordScope.EXPOSURES))
        self.consent.set_preference("u-1", frozenset({RecordScope.STAYS}))
        self.assertFalse(self.consent.recording_allowed("u-1", RecordScope.EXPOSURES))

    def test_clinician_sees_only_confirmed_facts_within_field_grant(self) -> None:
        grant = self.consent.grant_share(
            user_id="u-1",
            clinician_id="dr-1",
            jurisdiction=CN_SH,
            fields=frozenset({"fact_type", "region", "started_on", "ended_on"}),
        )
        view = self.consent.open_clinical_view(grant_id=grant.grant_id)
        self.assertEqual(1, len(view))
        self.assertEqual("", view[0].detail)  # 未授权字段被裁掉

    def test_revocation_blocks_future_views_but_keeps_audit(self) -> None:
        grant = self.consent.grant_share(
            user_id="u-1", clinician_id="dr-1", jurisdiction=CN_SH,
            fields=frozenset({"fact_type", "region"}),
        )
        self.consent.open_clinical_view(grant_id=grant.grant_id)
        self.consent.revoke(grant.grant_id)
        with self.assertRaises(PermissionError):
            self.consent.open_clinical_view(grant_id=grant.grant_id)
        self.assertEqual(1, len(self.consent.audit_log()))  # 历史审计保留

    def test_public_health_fact_retained_deidentified_after_revocation(self) -> None:
        grant = self.consent.grant_share(
            user_id="u-1", clinician_id="dr-1", jurisdiction=CN_SH,
            fields=frozenset({"fact_type", "region"}),
        )
        self.consent.revoke(grant.grant_id)

        fact = DeidentifiedFact.from_notification(
            user_id="u-1", salt="s", fact_type="exposure", region=CN_SH,
            window_start="2026-10-01", window_end="2026-10-03",
            advisory_code="RISK-MOSQUITO-GENERAL", notification_id="ntf-1",
        )
        retained = self.consent.retain_for_public_health(fact)
        self.assertNotIn("u-1", retained.pseudonym)
        again = self.consent.retain_for_public_health(
            DeidentifiedFact.from_notification(
                user_id="u-1", salt="s", fact_type="exposure", region=CN_SH,
                window_start="2026-10-01", window_end="2026-10-03",
                advisory_code="RISK-MOSQUITO-GENERAL", notification_id="ntf-2",
            )
        )
        self.assertEqual(("ntf-1", "ntf-2"), again.notification_ids)
        self.assertEqual(1, len(self.consent.deidentified_facts()))


class SymptomSeparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ExposureRegistry("u-1")
        self.exposure = self.registry.register(
            exposure_type="mosquito_borne", location_id="loc-x",
            window_start=date(2026, 10, 1), window_end=date(2026, 10, 3),
        ).exposure
        self.service = SymptomService(RiskCatalog())

    def test_self_and_clinician_can_report(self) -> None:
        own = self.service.report(
            user_id="u-1", symptoms=frozenset({"fever"}),
            occurred_at=aware(datetime(2026, 10, 6, 8, 0)), reporter=Reporter.SELF,
            exposures=(self.exposure,),
        )
        clinician = self.service.report(
            user_id="u-1", symptoms=frozenset({"rash"}),
            occurred_at=aware(datetime(2026, 10, 6, 10, 0)),
            reporter=Reporter.CLINICIAN, clinician_id="dr-9",
        )
        self.assertEqual(Reporter.SELF, own.reporter)
        self.assertEqual("dr-9", clinician.clinician_id)

    def test_rule_hints_are_never_diagnosis(self) -> None:
        report = self.service.report(
            user_id="u-1", symptoms=frozenset({"fever", "rash"}),
            occurred_at=aware(datetime(2026, 10, 6, 8, 0)), reporter=Reporter.SELF,
            exposures=(self.exposure,),
        )
        self.assertFalse(report.has_diagnosis)
        self.assertEqual(1, len(report.rule_hints))
        hint = report.rule_hints[0]
        self.assertFalse(hint.is_diagnosis)
        self.assertIn("非诊断", hint.message)
        self.assertEqual("RISK-MOSQUITO-GENERAL", hint.advisory_code)

    def test_diagnosis_is_separate_signed_step(self) -> None:
        report = self.service.report(
            user_id="u-1", symptoms=frozenset({"fever"}),
            occurred_at=aware(datetime(2026, 10, 6, 8, 0)), reporter=Reporter.SELF,
        )
        with self.assertRaises(ValueError):
            self.service.attach_diagnosis(
                report_id=report.report_id, clinician_id=" ", condition_code="DENGUE-A090"
            )
        diagnosis = self.service.attach_diagnosis(
            report_id=report.report_id, clinician_id="dr-9", condition_code="DENGUE-A090"
        )
        self.assertTrue(report.has_diagnosis)
        self.assertEqual("dr-9", diagnosis.clinician_id)
        self.assertEqual((), report.rule_hints)  # 诊断不产生也不改动规则提示

    def test_unknown_symptom_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.report(
                user_id="u-1", symptoms=frozenset({"exploding_head"}),
                occurred_at=aware(datetime(2026, 10, 6, 8, 0)), reporter=Reporter.SELF,
            )


class OversightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.oversight = OversightService(salt="s")
        self.records = (
            {"user_id": "u-1", "region": CN_SH, "exposure_type": "mosquito_borne",
             "window_start": "2026-10-01", "window_end": "2026-10-03",
             "advisory_code": "RISK-MOSQUITO-GENERAL"},
            {"user_id": "u-2", "region": CN_GD, "exposure_type": "foodborne",
             "window_start": "2026-10-02", "window_end": "2026-10-02",
             "advisory_code": "RISK-FOOD-GATHERING"},
        )

    def test_query_is_scoped_to_jurisdiction_and_minimal_fields(self) -> None:
        case = self.oversight.open_case(
            investigator_id="inv-1", jurisdictions=frozenset({CN_SH}),
            allowed_fields=MINIMAL_FIELDS, reason="聚集性发热排查",
        )
        rows = self.oversight.query(
            case_id=case.case_id, jurisdiction=CN_SH, records=self.records,
        )
        self.assertEqual(1, len(rows))
        fields = rows[0].fields
        self.assertNotIn(FIELD_USER_ID, fields)
        self.assertIn(FIELD_PSEUDONYM, fields)
        self.assertIn(FIELD_REGION, fields)
        self.assertEqual(CN_SH, fields[FIELD_REGION])
        self.assertNotIn("u-1", repr(fields))

    def test_out_of_jurisdiction_query_denied_and_audited(self) -> None:
        case = self.oversight.open_case(
            investigator_id="inv-1", jurisdictions=frozenset({CN_SH}),
        )
        with self.assertRaises(PermissionError):
            self.oversight.query(
                case_id=case.case_id, jurisdiction=CN_GD, records=self.records,
            )
        denied = [a for a in self.oversight.audit_log() if a.denied]
        self.assertEqual(1, len(denied))

    def test_closed_case_terminates_access_but_keeps_audit(self) -> None:
        case = self.oversight.open_case(
            investigator_id="inv-1", jurisdictions=frozenset({CN_SH}),
        )
        self.oversight.query(case_id=case.case_id, jurisdiction=CN_SH, records=self.records)
        self.oversight.close_case(case.case_id)
        with self.assertRaises(PermissionError):
            self.oversight.query(case_id=case.case_id, jurisdiction=CN_SH, records=self.records)
        self.assertEqual(2, len(self.oversight.audit_log()))
        self.assertTrue(all(a.case_id == case.case_id for a in self.oversight.audit_log()))

    def test_real_identity_field_cannot_be_default_granted(self) -> None:
        with self.assertRaises(ValueError):
            self.oversight.open_case(
                investigator_id="inv-1", jurisdictions=frozenset({CN_SH}),
                allowed_fields=MINIMAL_FIELDS | frozenset({FIELD_USER_ID}),
            )


class ItineraryResilienceTests(unittest.TestCase):
    def test_platform_cancellation_keeps_local_trip_stays_and_exposures(self) -> None:
        tracker = TravelHealthTracker(gateway=FlakyGateway())
        tracker.choose_recording_scope("u-1", frozenset(RecordScope))
        tracker.plan_trip(
            trip_id="trip-1", user_id="u-1", regions=(CN_SH,),
            booking_refs=frozenset({"booking-xyz"}),
        )
        tracker.upsert_stay(
            "trip-1",
            StayInterval("stay-1", "loc-hotel", date(2026, 10, 1), date(2026, 10, 3)),
        )
        exposure = tracker.register_exposure(
            "u-1",
            exposure_type="mosquito_borne", location_id="loc-hotel",
            window_start=date(2026, 10, 1), window_end=date(2026, 10, 3),
            stay_id="stay-1", trip_id="trip-1",
        )

        # 平台侧退订导致行程消失：本地仅改状态，事实仍在、提醒照常。
        tracker.cancel_trip("trip-1")
        trip = tracker.trip("trip-1")
        self.assertEqual(TripStatus.CANCELLED, trip.status)
        self.assertIn("stay-1", trip.stays)
        reminder = tracker.schedule_monitoring(exposure.exposure_id, region=CN_SH)
        self.assertEqual(date(2026, 10, 17), reminder.due_date)

        types = [e.event_type for e in tracker.events.events()]
        self.assertIn("ITINERARY_CANCELLED", types)


class TrackerJourneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads((ROOT / "contracts" / "domain.schema.json").read_text(encoding="utf-8"))

    def _tracker(self, fail_times: int = 0) -> TravelHealthTracker:
        return TravelHealthTracker(gateway=FlakyGateway(fail_times))

    def test_full_journey_and_event_envelopes(self) -> None:
        tracker = self._tracker()
        # 出发前：选择全部自愿记录范围。
        tracker.choose_recording_scope("u-1", frozenset(RecordScope))

        # 途中：重复扫码归并为一条暴露。
        kw = dict(
            exposure_type="mosquito_borne", location_id="loc-market",
            window_start=date(2026, 10, 1), window_end=date(2026, 10, 3),
            scan_code="scan-1", companion_ids=frozenset({"u-2"}),
            trip_id="trip-1", stay_id="stay-1",
        )
        exposure = tracker.register_exposure("u-1", **kw)
        tracker.register_exposure("u-1", **kw)
        self.assertEqual(
            1,
            [e.event_type for e in tracker.events.events()].count("EXPOSURE_RECORDED"),
        )

        tracker.record_protection(
            "u-1", kind="insect_repellent", date=date(2026, 10, 2),
            location_id="loc-market",
        )

        # 返程：分期限监测提醒。
        reminder = tracker.schedule_monitoring(exposure.exposure_id, region=CN_SH)
        self.assertEqual(date(2026, 10, 17), reminder.due_date)

        # 就医：确认旅居史 → 授权 → 医生查看。
        tracker.confirm_travel_fact(
            "u-1",
            ConfirmedTravelFact(
                fact_type="exposure", region=CN_SH,
                started_on="2026-10-01", ended_on="2026-10-03",
                detail="夜间市场停留并使用驱蚊剂",
                confirmed_at=aware(datetime(2026, 10, 5, 9, 0)),
            ),
        )
        grant = tracker.grant_clinical_share(
            user_id="u-1", clinician_id="dr-1", jurisdiction=CN_SH,
            fields=frozenset({"fact_type", "region", "started_on", "ended_on", "detail"}),
        )
        view = tracker.consent.open_clinical_view(grant_id=grant.grant_id)
        self.assertEqual(1, len(view))

        # 症状上报（本人）→ 医生诊断分离挂接。
        report = tracker.report_symptoms(
            user_id="u-1", symptoms=frozenset({"fever"}),
            occurred_at=aware(datetime(2026, 10, 6, 8, 0)),
            reporter=Reporter.SELF, exposures=(exposure,), region=CN_SH,
        )
        self.assertFalse(report.has_diagnosis)
        tracker.attach_diagnosis(report.report_id, "dr-1", "DENGUE-A090")

        # 提醒送达、失败补发。
        tracker.dispatch_due(reminder.due_date)
        tracker.confirm_reminder(reminder.reminder_id)

        # 通知事实脱敏留存，撤回不影响留存。
        tracker.retain_notification_fact(
            DeidentifiedFact.from_notification(
                user_id="u-1", salt=tracker.salt, fact_type="exposure",
                region=CN_SH, window_start="2026-10-01", window_end="2026-10-03",
                advisory_code="RISK-MOSQUITO-GENERAL", notification_id="ntf-9",
            )
        )
        tracker.revoke_clinical_share(grant.grant_id)
        with self.assertRaises(PermissionError):
            tracker.consent.open_clinical_view(grant_id=grant.grant_id)
        self.assertEqual(1, len(tracker.consent.deidentified_facts()))

        # 跨区域调查：最小字段、关闭后不可查、审计保留。
        case = tracker.open_investigation(
            investigator_id="inv-1", jurisdictions=frozenset({CN_SH}),
            reason="蚊媒聚集排查",
        )
        raw = ({"user_id": "u-1", "region": CN_SH, "exposure_type": "mosquito_borne",
                "window_start": "2026-10-01", "window_end": "2026-10-03",
                "advisory_code": "RISK-MOSQUITO-GENERAL"},)
        rows = tracker.oversight.query(case_id=case.case_id, jurisdiction=CN_SH, records=raw)
        self.assertNotIn("u-1", repr(rows[0].fields))
        tracker.close_investigation(case.case_id)
        with self.assertRaises(PermissionError):
            tracker.oversight.query(case_id=case.case_id, jurisdiction=CN_SH, records=raw)

        # 管理追溯：依据 → 确认 → 处置，身份掩码。
        trace = tracker.trace_reminder(reminder.reminder_id, manager_id="mgr-1", region=CN_SH)
        self.assertEqual("RISK-MOSQUITO-GENERAL", trace.basis.advisory_code)
        self.assertGreaterEqual(len(trace.confirmations), 2)
        self.assertEqual(1, len(trace.follow_ups))
        self.assertEqual("DENGUE-A090", trace.follow_ups[0].diagnosis_code)
        self.assertIn("ntf-9", trace.follow_ups[0].notification_ids)
        self.assertFalse(trace.user_identity_exposed)
        self.assertNotIn("u-1", repr(trace))
        self.assertEqual(1, len(tracker.traceability.audit_log()))

        # 所有事件信封符合契约。
        for event in tracker.events.events():
            issues = validate_event(event.to_envelope(), self.schema)
            self.assertEqual([], issues, msg=event.event_type)

    def test_dispatch_failure_then_recovery_emits_both_events(self) -> None:
        tracker = self._tracker(fail_times=1)
        tracker.choose_recording_scope("u-9", frozenset(RecordScope))
        exposure = tracker.register_exposure(
            user_id="u-9", exposure_type="foodborne", location_id="loc-b",
            window_start=date(2026, 10, 1), window_end=date(2026, 10, 1),
        )
        reminder = tracker.schedule_monitoring(exposure.exposure_id)
        first = tracker.dispatch_due(reminder.due_date)
        self.assertFalse(first[0].delivered)
        recovered = tracker.dispatch_due(reminder.due_date)
        self.assertTrue(recovered[0].delivered)
        types = [e.event_type for e in tracker.events.events()]
        self.assertIn("REMINDER_DELIVERY_FAILED", types)
        self.assertEqual(types.count("REMINDER_DELIVERED"), 1)


if __name__ == "__main__":
    unittest.main()
