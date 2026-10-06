from __future__ import annotations

import dataclasses
import unittest

from helpers import at
from travel_health_exposure.domain import ExposureType, TrackerStore
from travel_health_exposure.exposure import (
    confirm_itinerary,
    register_contact,
    register_exposure,
    set_consent,
)
from travel_health_exposure.sharing import (
    ShareRevokedError,
    authorize_share,
    notify_public_health,
    revoke_share,
    view_share,
)


class SharingTests(unittest.TestCase):
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
        register_contact(
            self.store, itinerary_id="it-1", kind="聚餐", occurred_at=at(2), detail="景区餐厅聚餐", now=at(2)
        )
        self.exposure, _ = register_exposure(
            self.store,
            itinerary_id="it-1",
            exposure_type=ExposureType.FOODBORNE_GATHERING,
            region="云南-西双版纳",
            window_start=at(2),
            window_end=at(2, 20),
            now=at(2, 21),
        )

    def authorize(self, fields=("destination_region", "exposures")):
        return authorize_share(
            self.store,
            share_id="share-1",
            traveler_id="zhang-001",
            clinician_id="doc-1",
            fields=frozenset(fields),
            now=at(6),
        )

    def test_snapshot_contains_only_authorized_fields(self) -> None:
        self.authorize()
        snapshot = view_share(self.store, "share-1", viewer_id="doc-1", now=at(6, 9))
        entry = snapshot["itineraries"][0]
        self.assertEqual("云南-西双版纳", entry["destination_region"])
        self.assertNotIn("contacts", entry)
        self.assertNotIn("return_at", entry)
        self.assertEqual(1, len(snapshot["exposures"]))

    def test_unknown_field_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.authorize(fields=("destination_region", "id_number"))

    def test_revoke_blocks_future_views_but_keeps_deidentified_notice(self) -> None:
        self.authorize()
        view_share(self.store, "share-1", viewer_id="doc-1", now=at(6, 9))
        notice = notify_public_health(
            self.store, notice_id="notice-1", share_id="share-1", exposure_id=self.exposure.exposure_id, now=at(6, 10)
        )
        revoke_share(self.store, "share-1", at(7))
        with self.assertRaises(ShareRevokedError):
            view_share(self.store, "share-1", viewer_id="doc-1", now=at(7, 9))
        # 已用于公共卫生通知的事实保留脱敏版本
        self.assertIs(self.store.notices["notice-1"], notice)
        self.assertNotIn("traveler_id", {field.name for field in dataclasses.fields(notice)})
        self.assertNotIn("zhang-001", str(dataclasses.asdict(notice)))
        actions = [entry.action for entry in self.store.audit]
        self.assertIn("share_viewed", actions)
        self.assertIn("share_view_denied", actions)

    def test_notice_rejects_foreign_exposure(self) -> None:
        self.authorize()
        set_consent(
            self.store,
            traveler_id="li-002",
            risk_categories=frozenset(ExposureType),
            record_stays=True,
            record_contacts=True,
            record_protections=True,
            now=at(1),
        )
        confirm_itinerary(
            self.store,
            itinerary_id="it-2",
            traveler_id="li-002",
            destination_region="广东-广州",
            depart_at=at(1),
            return_at=at(5),
            booking_ref="BK-9",
            now=at(1),
        )
        foreign, _ = register_exposure(
            self.store,
            itinerary_id="it-2",
            exposure_type=ExposureType.MOSQUITO_BORNE,
            region="广东-广州",
            window_start=at(2),
            window_end=at(3),
            now=at(3),
        )
        with self.assertRaises(ValueError):
            notify_public_health(
                self.store, notice_id="notice-2", share_id="share-1", exposure_id=foreign.exposure_id, now=at(6, 10)
            )


if __name__ == "__main__":
    unittest.main()
