from __future__ import annotations

import unittest

from helpers import at
from travel_health_exposure.domain import ConsentScopeError, ExposureType, TrackerStore
from travel_health_exposure.exposure import (
    confirm_itinerary,
    register_contact,
    register_exposure,
    register_group_scan,
    register_stay,
    set_consent,
)

ALL_TYPES = frozenset(ExposureType)


def full_consent(store: TrackerStore, traveler: str = "zhang-001", **overrides):
    defaults = dict(record_stays=True, record_contacts=True, record_protections=True)
    defaults.update(overrides)
    return set_consent(store, traveler_id=traveler, risk_categories=ALL_TYPES, now=at(1), **defaults)


def confirmed_itinerary(
    store: TrackerStore,
    itinerary_id: str = "it-1",
    traveler: str = "zhang-001",
    group_id: str | None = None,
    booking_ref: str = "BK-1",
    return_day: int = 5,
):
    return confirm_itinerary(
        store,
        itinerary_id=itinerary_id,
        traveler_id=traveler,
        destination_region="云南-西双版纳",
        depart_at=at(1),
        return_at=at(return_day),
        booking_ref=booking_ref,
        group_id=group_id,
        now=at(1),
    )


class ConsentTests(unittest.TestCase):
    def test_recording_outside_scope_is_rejected(self) -> None:
        store = TrackerStore()
        full_consent(store, record_contacts=False)
        confirmed_itinerary(store)
        with self.assertRaises(ConsentScopeError):
            register_contact(
                store, itinerary_id="it-1", kind="聚餐", occurred_at=at(2), detail="景区餐厅", now=at(2)
            )
        stay = register_stay(store, itinerary_id="it-1", start=at(1), end=at(3), region="云南-西双版纳", now=at(1))
        self.assertEqual("云南-西双版纳", stay.region)

    def test_recording_without_consent_is_rejected(self) -> None:
        store = TrackerStore()
        confirmed_itinerary(store)
        with self.assertRaises(ConsentScopeError):
            register_stay(store, itinerary_id="it-1", start=at(1), end=at(3), region="云南-西双版纳", now=at(1))


class RebookingTests(unittest.TestCase):
    def test_rebooking_keeps_single_itinerary_and_exposure(self) -> None:
        store = TrackerStore()
        full_consent(store)
        confirmed_itinerary(store)
        first, created = register_exposure(
            store,
            itinerary_id="it-1",
            exposure_type=ExposureType.MOSQUITO_BORNE,
            region="云南-西双版纳",
            window_start=at(1),
            window_end=at(3),
            now=at(3),
        )
        self.assertTrue(created)
        itinerary = confirmed_itinerary(store, booking_ref="BK-2", return_day=6)  # 改签
        self.assertEqual(2, itinerary.version)
        second, created = register_exposure(
            store,
            itinerary_id="it-1",
            exposure_type=ExposureType.MOSQUITO_BORNE,
            region="云南-西双版纳",
            window_start=at(3),
            window_end=at(4),
            now=at(4),
        )
        self.assertFalse(created)
        self.assertIs(first, second)
        self.assertEqual(1, len(store.itineraries))
        self.assertEqual(1, len(store.exposures))
        self.assertEqual(at(1), second.window_start)
        self.assertEqual(at(4), second.window_end)

    def test_rebooking_with_foreign_traveler_is_rejected(self) -> None:
        store = TrackerStore()
        full_consent(store)
        confirmed_itinerary(store)
        with self.assertRaises(ValueError):
            confirmed_itinerary(store, traveler="li-002")


class GroupScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TrackerStore()
        for traveler, itinerary_id in (("zhang-001", "it-1"), ("li-002", "it-2"), ("wang-003", "it-3")):
            full_consent(self.store, traveler=traveler)
            confirmed_itinerary(self.store, itinerary_id=itinerary_id, traveler=traveler, group_id="G-1")

    def scan(self, scan_id: str = "SCAN-1"):
        return register_group_scan(
            self.store,
            scan_id=scan_id,
            itinerary_ids=["it-1", "it-2", "it-3"],
            exposure_type=ExposureType.FOODBORNE_GATHERING,
            region="云南-西双版纳",
            window_start=at(2),
            window_end=at(2, 20),
            now=at(2, 21),
        )

    def test_group_scan_creates_one_exposure_per_member(self) -> None:
        records = self.scan()
        self.assertEqual(3, len(records))
        self.assertEqual(3, len(self.store.exposures))
        self.assertEqual({"zhang-001", "li-002", "wang-003"}, {r.traveler_id for r in records})
        self.assertEqual({"G-1"}, {r.group_id for r in records})

    def test_repeated_scan_is_idempotent(self) -> None:
        first = self.scan()
        second = self.scan()
        self.assertEqual([r.exposure_id for r in first], [r.exposure_id for r in second])
        self.assertEqual(3, len(self.store.exposures))

    def test_single_scan_dedup(self) -> None:
        record, created = register_exposure(
            self.store,
            itinerary_id="it-1",
            exposure_type=ExposureType.MOSQUITO_BORNE,
            region="云南-西双版纳",
            window_start=at(1),
            window_end=at(2),
            now=at(2),
            scan_id="S-1",
        )
        self.assertTrue(created)
        again, created = register_exposure(
            self.store,
            itinerary_id="it-1",
            exposure_type=ExposureType.MOSQUITO_BORNE,
            region="云南-西双版纳",
            window_start=at(1),
            window_end=at(2),
            now=at(2),
            scan_id="S-1",
        )
        self.assertFalse(created)
        self.assertIs(record, again)
        self.assertEqual(1, len(self.store.exposures))


if __name__ == "__main__":
    unittest.main()
