"""就医授权共享：撤回只影响未来查看，已通知事实保留脱敏版本。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .domain import (
    ClinicalShare,
    DeidentifiedNotice,
    ShareStatus,
    TrackerStore,
    emit_event,
    record_audit,
)


class ShareRevokedError(PermissionError):
    """共享已撤回，拒绝后续查看。"""


CONFIRMABLE_FIELDS = frozenset(
    {"destination_region", "depart_at", "return_at", "stays", "contacts", "protections", "exposures"}
)
"""可授权共享的旅居史字段。"""


def authorize_share(
    store: TrackerStore,
    *,
    share_id: str,
    traveler_id: str,
    clinician_id: str,
    fields: frozenset[str],
    now: datetime,
) -> ClinicalShare:
    """按授权字段向医生提供经过确认的旅居史快照。"""
    unknown = set(fields) - CONFIRMABLE_FIELDS
    if unknown:
        raise ValueError(f"存在不可共享的字段: {sorted(unknown)}")
    share = ClinicalShare(
        share_id=share_id,
        traveler_id=traveler_id,
        clinician_id=clinician_id,
        authorized_fields=frozenset(fields),
        snapshot=_build_snapshot(store, traveler_id, frozenset(fields)),
        status=ShareStatus.ACTIVE,
        created_at=now,
    )
    store.shares[share_id] = share
    record_audit(
        store,
        actor=clinician_id,
        action="share_granted",
        target_id=share_id,
        occurred_at=now,
        detail="就医授权共享已建立",
    )
    return share


def _build_snapshot(store: TrackerStore, traveler_id: str, fields: frozenset[str]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"itineraries": []}
    for itinerary in store.itineraries.values():
        if itinerary.traveler_id != traveler_id:
            continue
        entry: dict[str, Any] = {"itinerary_id": itinerary.itinerary_id, "version": itinerary.version}
        if "destination_region" in fields:
            entry["destination_region"] = itinerary.destination_region
        if "depart_at" in fields:
            entry["depart_at"] = itinerary.depart_at.isoformat()
        if "return_at" in fields:
            entry["return_at"] = itinerary.return_at.isoformat()
        if "stays" in fields:
            entry["stays"] = [
                {"start": s.start.isoformat(), "end": s.end.isoformat(), "region": s.region}
                for s in itinerary.stays
            ]
        if "contacts" in fields:
            entry["contacts"] = [
                {"kind": c.kind, "occurred_at": c.occurred_at.isoformat(), "detail": c.detail}
                for c in itinerary.contacts
            ]
        if "protections" in fields:
            entry["protections"] = [
                {"kind": p.kind, "applied_at": p.applied_at.isoformat()} for p in itinerary.protections
            ]
        snapshot["itineraries"].append(entry)
    if "exposures" in fields:
        snapshot["exposures"] = [
            {
                "exposure_type": e.exposure_type.value,
                "region": e.region,
                "window_start": e.window_start.isoformat(),
                "window_end": e.window_end.isoformat(),
            }
            for e in store.exposures.values()
            if e.traveler_id == traveler_id
        ]
    return snapshot


def _share(store: TrackerStore, share_id: str) -> ClinicalShare:
    share = store.shares.get(share_id)
    if share is None:
        raise ValueError(f"共享不存在: {share_id}")
    return share


def view_share(store: TrackerStore, share_id: str, *, viewer_id: str, now: datetime) -> dict[str, Any]:
    """查看共享快照；撤回后拒绝一切后续查看。"""
    share = _share(store, share_id)
    if share.status is ShareStatus.REVOKED:
        record_audit(
            store,
            actor=viewer_id,
            action="share_view_denied",
            target_id=share_id,
            occurred_at=now,
            detail="共享已撤回",
        )
        raise ShareRevokedError("共享已撤回，仅历史查看记录保留")
    record_audit(store, actor=viewer_id, action="share_viewed", target_id=share_id, occurred_at=now)
    return dict(share.snapshot)


def revoke_share(store: TrackerStore, share_id: str, now: datetime) -> ClinicalShare:
    """用户撤回共享：只影响未来查看，已通知的脱敏事实继续保留。"""
    share = _share(store, share_id)
    share.status = ShareStatus.REVOKED
    share.revoked_at = now
    emit_event(
        store,
        event_type="SHARE_REVOKED",
        aggregate_type="clinical_share",
        aggregate_id=share_id,
        occurred_at=now,
        summary="用户撤回就医共享",
    )
    return share


def notify_public_health(
    store: TrackerStore,
    *,
    notice_id: str,
    share_id: str,
    exposure_id: str,
    now: datetime,
) -> DeidentifiedNotice:
    """事实用于公共卫生通知时保留脱敏版本，不含旅客身份。"""
    share = _share(store, share_id)
    exposure = store.exposures.get(exposure_id)
    if exposure is None:
        raise ValueError(f"暴露记录不存在: {exposure_id}")
    if exposure.traveler_id != share.traveler_id:
        raise ValueError("暴露记录不属于该共享的旅客")
    notice = DeidentifiedNotice(
        notice_id=notice_id,
        share_id=share_id,
        exposure_type=exposure.exposure_type,
        region=exposure.region,
        window_start=exposure.window_start,
        window_end=exposure.window_end,
        created_at=now,
    )
    store.notices[notice_id] = notice
    record_audit(
        store,
        actor="public_health",
        action="notice_published",
        target_id=notice_id,
        occurred_at=now,
        detail="公共卫生通知保留脱敏事实",
    )
    return notice
