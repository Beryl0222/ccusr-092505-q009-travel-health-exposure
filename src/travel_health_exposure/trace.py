"""管理者溯源视图：从一条提醒追到风险依据、用户确认与后续处置。"""

from __future__ import annotations

from typing import Any

from .domain import MONITORING_DURATIONS, TrackerStore


def trace_reminder(store: TrackerStore, reminder_id: str) -> dict[str, Any]:
    """汇总一条提醒的完整链路；无关旅客仅以计数出现，不暴露身份。"""
    reminder = store.reminders.get(reminder_id)
    if reminder is None:
        raise ValueError(f"提醒不存在: {reminder_id}")
    exposure = store.exposures[reminder.exposure_id]
    itinerary = store.itineraries[exposure.itinerary_id]
    consent = store.consents.get(reminder.traveler_id)
    reports = [r for r in store.reports.values() if r.traveler_id == reminder.traveler_id]
    share_ids = {s.share_id for s in store.shares.values() if s.traveler_id == reminder.traveler_id}
    notices = [n for n in store.notices.values() if n.share_id in share_ids]
    companions = {
        e.traveler_id
        for e in store.exposures.values()
        if e.group_id is not None and e.group_id == exposure.group_id and e.traveler_id != reminder.traveler_id
    }
    return {
        "reminder": {
            "reminder_id": reminder.reminder_id,
            "traveler_id": reminder.traveler_id,
            "state": reminder.state.value,
            "monitor_until": reminder.monitor_until.isoformat(),
            "attempts": reminder.attempts,
            "acknowledged_at": reminder.acknowledged_at.isoformat() if reminder.acknowledged_at else None,
        },
        "risk_basis": {
            "exposure_id": exposure.exposure_id,
            "exposure_type": exposure.exposure_type.value,
            "region": exposure.region,
            "window_start": exposure.window_start.isoformat(),
            "window_end": exposure.window_end.isoformat(),
            "monitoring_days": MONITORING_DURATIONS[exposure.exposure_type].days,
        },
        "user_confirmation": {
            "itinerary_id": itinerary.itinerary_id,
            "itinerary_version": itinerary.version,
            "risk_categories": sorted(c.value for c in consent.risk_categories) if consent else [],
            "consent_granted_at": consent.granted_at.isoformat() if consent else None,
        },
        "disposition": {
            "symptom_reports": [
                {
                    "report_id": r.report_id,
                    "reporter": r.reporter.value,
                    "rule_alerts": [a.rule_id for a in r.rule_alerts],
                    "diagnosis": r.diagnosis,
                    "diagnosed_by": r.diagnosed_by,
                }
                for r in reports
            ],
            "public_health_notices": [
                {"notice_id": n.notice_id, "exposure_type": n.exposure_type.value, "region": n.region}
                for n in notices
            ],
        },
        "related_travelers": {
            "group_id": exposure.group_id,
            "companion_count": len(companions),
        },
    }
