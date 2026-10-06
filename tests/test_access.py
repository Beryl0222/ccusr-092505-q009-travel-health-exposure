from __future__ import annotations

import unittest

from helpers import at
from travel_health_exposure.access import (
    InvestigationClosedError,
    close_investigation,
    open_investigation,
    query_exposures,
)
from travel_health_exposure.domain import ExposureType, TrackerStore
from travel_health_exposure.exposure import confirm_itinerary, register_exposure, set_consent


class AccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TrackerStore()
        for traveler, itinerary_id, region in (
            ("zhang-001", "it-1", "云南-西双版纳"),
            ("li-002", "it-2", "广东-广州"),
        ):
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
                destination_region=region,
                depart_at=at(1),
                return_at=at(5),
                booking_ref=f"BK-{itinerary_id}",
                now=at(1),
            )
            register_exposure(
                self.store,
                itinerary_id=itinerary_id,
                exposure_type=ExposureType.MOSQUITO_BORNE,
                region=region,
                window_start=at(1),
                window_end=at(3),
                now=at(3),
            )

    def open(self, purpose: str = "case_investigation"):
        return open_investigation(
            self.store,
            grant_id="grant-1",
            officer_id="officer-yn",
            region="云南-西双版纳",
            purpose=purpose,
            now=at(8),
        )

    def test_query_returns_only_jurisdiction_and_necessary_fields(self) -> None:
        self.open()
        rows = query_exposures(self.store, "grant-1", at(8, 9))
        self.assertEqual(1, len(rows))
        self.assertEqual({"exposure_type", "region", "window_start", "window_end"}, set(rows[0]))
        self.assertNotIn("traveler_id", rows[0])

    def test_unknown_purpose_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.open(purpose="unknown")

    def test_close_blocks_access_but_keeps_audit(self) -> None:
        self.open()
        query_exposures(self.store, "grant-1", at(8, 9))
        close_investigation(self.store, "grant-1", at(9))
        with self.assertRaises(InvestigationClosedError):
            query_exposures(self.store, "grant-1", at(9, 9))
        actions = [entry.action for entry in self.store.audit]
        self.assertEqual(
            ["access_granted", "access_queried", "access_closed", "access_denied"], actions[-4:]
        )  # 调查结束后审计保留


if __name__ == "__main__":
    unittest.main()
