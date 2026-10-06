from __future__ import annotations

import unittest

from helpers import at
from travel_health_exposure.domain import ExposureType, ReporterRole, TrackerStore
from travel_health_exposure.exposure import confirm_itinerary, register_exposure, set_consent
from travel_health_exposure.symptoms import record_diagnosis, report_symptoms


class SymptomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TrackerStore()
        set_consent(
            self.store,
            traveler_id="zhang-001",
            risk_categories=frozenset(ExposureType),
            record_stays=True,
            record_contacts=True,
            record_protections=True,
            now=at(1),
        )
        confirm_itinerary(
            self.store,
            itinerary_id="it-1",
            traveler_id="zhang-001",
            destination_region="云南-西双版纳",
            depart_at=at(1),
            return_at=at(5),
            booking_ref="BK-1",
            now=at(1),
        )
        # 蚊媒暴露窗口 10-01 至 10-03，监测期至 10-17
        register_exposure(
            self.store,
            itinerary_id="it-1",
            exposure_type=ExposureType.MOSQUITO_BORNE,
            region="云南-西双版纳",
            window_start=at(1),
            window_end=at(3),
            now=at(3),
        )

    def report(self, symptoms=("发热",), day=10, reporter=ReporterRole.SELF, **kwargs):
        return report_symptoms(
            self.store,
            report_id="rep-1",
            traveler_id="zhang-001",
            reporter=reporter,
            symptoms=symptoms,
            now=at(day),
            **kwargs,
        )

    def test_self_report_triggers_rule_alert_not_diagnosis(self) -> None:
        report = self.report()
        self.assertEqual(["R-MOSQ-01"], [a.rule_id for a in report.rule_alerts])
        self.assertEqual({"rule"}, {a.kind for a in report.rule_alerts})
        self.assertIsNone(report.diagnosis)

    def test_clinician_can_also_report(self) -> None:
        report = self.report(symptoms=("皮疹",), reporter=ReporterRole.CLINICIAN)
        self.assertEqual(1, len(report.rule_alerts))

    def test_report_entry_rejects_diagnosis(self) -> None:
        with self.assertRaises(ValueError):
            self.report(diagnosis="登革热")

    def test_diagnosis_is_recorded_separately(self) -> None:
        report = self.report()
        record_diagnosis(self.store, "rep-1", clinician_id="doc-1", diagnosis="登革热", now=at(10, 10))
        self.assertEqual("登革热", report.diagnosis)
        self.assertEqual("doc-1", report.diagnosed_by)
        self.assertEqual(["R-MOSQ-01"], [a.rule_id for a in report.rule_alerts])  # 规则提示不被改写

    def test_report_after_monitoring_window_has_no_alerts(self) -> None:
        report = self.report(day=20)
        self.assertEqual([], report.rule_alerts)


if __name__ == "__main__":
    unittest.main()
