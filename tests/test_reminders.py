from __future__ import annotations

import unittest

from helpers import at
from travel_health_exposure.domain import (
    ConsentScopeError,
    ExposureType,
    HealthReminder,
    ReminderState,
    TrackerStore,
)
from travel_health_exposure.exposure import confirm_itinerary, register_exposure, set_consent
from travel_health_exposure.reminders import (
    acknowledge_reminder,
    dispatch_reminder,
    recover_pending,
    schedule_monitoring,
)


def ok_sender(reminder: HealthReminder) -> None:
    pass


def failing_sender(reminder: HealthReminder) -> None:
    raise RuntimeError("短信通道不可用")


def prepare_store(risk_categories=frozenset(ExposureType)) -> TrackerStore:
    store = TrackerStore()
    set_consent(
        store,
        traveler_id="zhang-001",
        risk_categories=risk_categories,
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
    return store


def add_exposure(store: TrackerStore, exposure_type: ExposureType, region: str = "云南-西双版纳"):
    record, _ = register_exposure(
        store,
        itinerary_id="it-1",
        exposure_type=exposure_type,
        region=region,
        window_start=at(1),
        window_end=at(3),
        now=at(3),
    )
    return record


class ScheduleTests(unittest.TestCase):
    def test_monitoring_duration_differs_by_exposure_type(self) -> None:
        store = prepare_store()
        expectations = {
            ExposureType.MOSQUITO_BORNE: 19,  # 14 天
            ExposureType.FOODBORNE_GATHERING: 12,  # 7 天
            ExposureType.POLLEN: 8,  # 3 天
        }
        for exposure_type, expected_day in expectations.items():
            reminder = schedule_monitoring(
                store, exposure_id=add_exposure(store, exposure_type).exposure_id, now=at(5, 18)
            )
            self.assertEqual(at(expected_day), reminder.monitor_until)

    def test_unconsented_category_is_rejected(self) -> None:
        store = prepare_store(risk_categories=frozenset({ExposureType.MOSQUITO_BORNE}))
        record = add_exposure(store, ExposureType.POLLEN, region="云南-昆明")
        with self.assertRaises(ConsentScopeError):
            schedule_monitoring(store, exposure_id=record.exposure_id, now=at(5, 18))

    def test_schedule_is_idempotent_per_exposure(self) -> None:
        store = prepare_store()
        exposure = add_exposure(store, ExposureType.MOSQUITO_BORNE)
        first = schedule_monitoring(store, exposure_id=exposure.exposure_id, now=at(5, 18))
        second = schedule_monitoring(store, exposure_id=exposure.exposure_id, now=at(5, 19))
        self.assertIs(first, second)
        self.assertEqual(1, len(store.reminders))


class DeliveryTests(unittest.TestCase):
    def test_send_failure_keeps_local_deadline(self) -> None:
        store = prepare_store()
        reminder = schedule_monitoring(
            store, exposure_id=add_exposure(store, ExposureType.MOSQUITO_BORNE).exposure_id, now=at(5, 18)
        )
        deadline = reminder.monitor_until
        self.assertFalse(dispatch_reminder(store, reminder.reminder_id, failing_sender, at(6)))
        self.assertEqual(ReminderState.SEND_FAILED, reminder.state)
        self.assertEqual(deadline, reminder.monitor_until)
        self.assertEqual("短信通道不可用", reminder.last_error)

    def test_recovery_resends_only_unacknowledged(self) -> None:
        store = prepare_store()
        failed = schedule_monitoring(
            store, exposure_id=add_exposure(store, ExposureType.MOSQUITO_BORNE).exposure_id, now=at(5, 18)
        )
        sent_unacked = schedule_monitoring(
            store, exposure_id=add_exposure(store, ExposureType.FOODBORNE_GATHERING).exposure_id, now=at(5, 18)
        )
        acked = schedule_monitoring(
            store, exposure_id=add_exposure(store, ExposureType.POLLEN).exposure_id, now=at(5, 18)
        )
        dispatch_reminder(store, failed.reminder_id, failing_sender, at(6))
        dispatch_reminder(store, sent_unacked.reminder_id, ok_sender, at(6))
        dispatch_reminder(store, acked.reminder_id, ok_sender, at(6))
        acknowledge_reminder(store, acked.reminder_id, at(6, 9))
        attempts_before = acked.attempts
        resent = recover_pending(store, ok_sender, at(6, 10))
        self.assertEqual({failed.reminder_id, sent_unacked.reminder_id}, set(resent))
        self.assertEqual(ReminderState.SENT, failed.state)
        self.assertEqual(attempts_before, acked.attempts)  # 已确认内容不再补发


if __name__ == "__main__":
    unittest.main()
