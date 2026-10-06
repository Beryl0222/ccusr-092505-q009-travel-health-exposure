from __future__ import annotations

import json
import unittest

from helpers import at
from travel_health_exposure.domain import ExposureType, HealthReminder, ReporterRole, TrackerStore
from travel_health_exposure.exposure import (
    confirm_itinerary,
    register_group_scan,
    set_consent,
)
from travel_health_exposure.reminders import acknowledge_reminder, dispatch_reminder, schedule_monitoring
from travel_health_exposure.sharing import authorize_share, notify_public_health
from travel_health_exposure.symptoms import record_diagnosis, report_symptoms
from travel_health_exposure.trace import trace_reminder


def ok_sender(reminder: HealthReminder) -> None:
    pass


class TraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TrackerStore()
        for traveler, itinerary_id in (("zhang-001", "it-1"), ("li-002", "it-2")):
            set_consent(
                self.store,
                traveler_id=traveler,
                risk_categories=frozenset(ExposureType),
                record_stays=True,
                record_contacts=True,
                record_protections=True,
                now=at(1),
            )
            confirm_itinerary(
                self.store,
                itinerary_id=itinerary_id,
                traveler_id=traveler,
                destination_region="云南-西双版纳",
                depart_at=at(1),
                return_at=at(5),
                booking_ref=f"BK-{itinerary_id}",
                group_id="G-1",
                now=at(1),
            )
        records = register_group_scan(
            self.store,
            scan_id="SCAN-1",
            itinerary_ids=["it-1", "it-2"],
            exposure_type=ExposureType.MOSQUITO_BORNE,
            region="云南-西双版纳",
            window_start=at(1),
            window_end=at(3),
            now=at(3),
        )
        self.subject = records[0]
        self.reminder = schedule_monitoring(self.store, exposure_id=self.subject.exposure_id, now=at(5, 18))
        dispatch_reminder(self.store, self.reminder.reminder_id, ok_sender, at(6))
        acknowledge_reminder(self.store, self.reminder.reminder_id, at(6, 9))
        report_symptoms(
            self.store,
            report_id="rep-1",
            traveler_id="zhang-001",
            reporter=ReporterRole.SELF,
            symptoms=("发热",),
            now=at(10),
        )
        record_diagnosis(self.store, "rep-1", clinician_id="doc-1", diagnosis="登革热", now=at(10, 10))
        authorize_share(
            self.store,
            share_id="share-1",
            traveler_id="zhang-001",
            clinician_id="doc-1",
            fields=frozenset({"exposures"}),
            now=at(10, 11),
        )
        notify_public_health(
            self.store, notice_id="notice-1", share_id="share-1", exposure_id=self.subject.exposure_id, now=at(10, 12)
        )

    def test_trace_links_risk_basis_confirmation_and_disposition(self) -> None:
        view = trace_reminder(self.store, self.reminder.reminder_id)
        self.assertEqual("mosquito_borne", view["risk_basis"]["exposure_type"])
        self.assertEqual("云南-西双版纳", view["risk_basis"]["region"])
        self.assertEqual(14, view["risk_basis"]["monitoring_days"])
        self.assertEqual(1, view["user_confirmation"]["itinerary_version"])
        self.assertIn("mosquito_borne", view["user_confirmation"]["risk_categories"])
        self.assertEqual("acknowledged", view["reminder"]["state"])
        report = view["disposition"]["symptom_reports"][0]
        self.assertEqual(["R-MOSQ-01"], report["rule_alerts"])
        self.assertEqual("登革热", report["diagnosis"])
        self.assertEqual("notice-1", view["disposition"]["public_health_notices"][0]["notice_id"])

    def test_trace_masks_unrelated_travelers(self) -> None:
        view = trace_reminder(self.store, self.reminder.reminder_id)
        self.assertEqual(1, view["related_travelers"]["companion_count"])
        self.assertNotIn("li-002", json.dumps(view, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
