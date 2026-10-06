from __future__ import annotations

import json
import unittest

from helpers import ROOT, at
from travel_health_exposure.contracts import validate_event
from travel_health_exposure.domain import ExposureType, ReporterRole, TrackerStore
from travel_health_exposure.exposure import confirm_itinerary, register_exposure, set_consent
from travel_health_exposure.reminders import schedule_monitoring
from travel_health_exposure.sharing import authorize_share, revoke_share
from travel_health_exposure.symptoms import report_symptoms


class DomainEventTests(unittest.TestCase):
    def test_emitted_events_satisfy_exchange_contract(self) -> None:
        store = TrackerStore()
        set_consent(
            store,
            traveler_id="zhang-001",
            risk_categories=frozenset(ExposureType),
            record_stays=True,
            record_contacts=True,
            record_protections=True,
            now=at(1),
        )
        confirm_itinerary(
            store,
            itinerary_id="it-1",
            traveler_id="zhang-001",
            destination_region="云南-西双版纳",
            depart_at=at(1),
            return_at=at(5),
            booking_ref="BK-1",
            now=at(1),
        )
        confirm_itinerary(  # 改签：同一聚合版本递增
            store,
            itinerary_id="it-1",
            traveler_id="zhang-001",
            destination_region="云南-西双版纳",
            depart_at=at(1),
            return_at=at(6),
            booking_ref="BK-2",
            now=at(1, 10),
        )
        exposure, _ = register_exposure(
            store,
            itinerary_id="it-1",
            exposure_type=ExposureType.MOSQUITO_BORNE,
            region="云南-西双版纳",
            window_start=at(1),
            window_end=at(3),
            now=at(3),
        )
        schedule_monitoring(store, exposure_id=exposure.exposure_id, now=at(6, 18))
        report_symptoms(
            store,
            report_id="rep-1",
            traveler_id="zhang-001",
            reporter=ReporterRole.SELF,
            symptoms=("发热",),
            now=at(10),
        )
        authorize_share(
            store,
            share_id="share-1",
            traveler_id="zhang-001",
            clinician_id="doc-1",
            fields=frozenset({"exposures"}),
            now=at(10, 11),
        )
        revoke_share(store, "share-1", at(11))

        self.assertEqual(
            {"ITINERARY_CONFIRMED", "EXPOSURE_RECORDED", "REMINDER_SCHEDULED", "SYMPTOM_REPORTED", "SHARE_REVOKED"},
            {event["event_type"] for event in store.events},
        )
        schema = json.loads((ROOT / "contracts" / "domain.schema.json").read_text(encoding="utf-8"))
        for event in store.events:
            self.assertEqual([], validate_event(event, schema), event)
        versions = [event["version"] for event in store.events if event["aggregate_id"] == "it-1"]
        self.assertEqual([1, 2], versions)


if __name__ == "__main__":
    unittest.main()
