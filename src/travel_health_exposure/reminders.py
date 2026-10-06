"""返程后的自我监测提醒。

期限按暴露类型区分；发送失败保留本地期限，恢复后只补发未确认内容。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .domain import (
    MONITORING_DURATIONS,
    ConsentScopeError,
    HealthReminder,
    ReminderState,
    TrackerStore,
    emit_event,
    record_audit,
)

Sender = Callable[[HealthReminder], None]
"""发送通道；抛出异常表示本次发送失败。"""


def schedule_monitoring(store: TrackerStore, *, exposure_id: str, now: datetime) -> HealthReminder:
    """返程后按暴露类型生成不同期限的自我监测提醒；同一暴露只生成一条。"""
    exposure = store.exposures.get(exposure_id)
    if exposure is None:
        raise ValueError(f"暴露记录不存在: {exposure_id}")
    consent = store.consents.get(exposure.traveler_id)
    if consent is None or exposure.exposure_type not in consent.risk_categories:
        raise ConsentScopeError("用户未选择该目的地风险提示，不生成监测提醒")
    for existing in store.reminders.values():
        if existing.exposure_id == exposure_id:
            return existing
    itinerary = store.itineraries[exposure.itinerary_id]
    duration = MONITORING_DURATIONS[exposure.exposure_type]
    reminder = HealthReminder(
        reminder_id=f"rem-{len(store.reminders) + 1:05d}",
        exposure_id=exposure_id,
        traveler_id=exposure.traveler_id,
        exposure_type=exposure.exposure_type,
        monitor_until=itinerary.return_at + duration,
    )
    store.reminders[reminder.reminder_id] = reminder
    emit_event(
        store,
        event_type="REMINDER_SCHEDULED",
        aggregate_type="health_reminder",
        aggregate_id=reminder.reminder_id,
        occurred_at=now,
        summary=f"返程后自我监测提醒已排期，期限 {duration.days} 天",
    )
    return reminder


def dispatch_reminder(store: TrackerStore, reminder_id: str, sender: Sender, now: datetime) -> bool:
    """发送一条提醒；失败时保留本地期限，仅记录失败状态。"""
    reminder = store.reminders.get(reminder_id)
    if reminder is None:
        raise ValueError(f"提醒不存在: {reminder_id}")
    if reminder.state is ReminderState.ACKNOWLEDGED:
        return True
    reminder.attempts += 1
    try:
        sender(reminder)
    except Exception as exc:  # 发送失败不丢失本地期限
        reminder.state = ReminderState.SEND_FAILED
        reminder.last_error = str(exc)
        return False
    reminder.state = ReminderState.SENT
    reminder.last_error = None
    return True


def acknowledge_reminder(store: TrackerStore, reminder_id: str, now: datetime) -> HealthReminder:
    """用户确认收到提醒；已确认内容不再补发。"""
    reminder = store.reminders.get(reminder_id)
    if reminder is None:
        raise ValueError(f"提醒不存在: {reminder_id}")
    reminder.state = ReminderState.ACKNOWLEDGED
    reminder.acknowledged_at = now
    record_audit(
        store,
        actor=reminder.traveler_id,
        action="reminder_acknowledged",
        target_id=reminder_id,
        occurred_at=now,
    )
    return reminder


def recover_pending(store: TrackerStore, sender: Sender, now: datetime) -> list[str]:
    """通道恢复后只补发未确认内容，返回本次补发的提醒标识。"""
    resent = []
    for reminder in store.reminders.values():
        if reminder.acknowledged_at is not None:
            continue
        if dispatch_reminder(store, reminder.reminder_id, sender, now):
            resent.append(reminder.reminder_id)
    return resent
