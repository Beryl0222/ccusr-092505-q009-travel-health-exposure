"""出发前同意、行程确认与暴露登记。

改签保持同一行程身份，多人同行各建一条暴露，重复扫码幂等，
三条规则共同保证不制造重复暴露。
"""

from __future__ import annotations

from datetime import datetime

from .domain import (
    ConsentScope,
    ConsentScopeError,
    ContactEvent,
    ExposureRecord,
    ExposureType,
    ProtectionMeasure,
    StayInterval,
    TrackerStore,
    TravelItinerary,
    emit_event,
    record_audit,
)


def set_consent(
    store: TrackerStore,
    *,
    traveler_id: str,
    risk_categories: frozenset[ExposureType],
    record_stays: bool,
    record_contacts: bool,
    record_protections: bool,
    now: datetime,
) -> ConsentScope:
    """出发前登记用户选择的目的地风险提示与自愿记录范围。"""
    consent = ConsentScope(
        traveler_id=traveler_id,
        risk_categories=frozenset(risk_categories),
        record_stays=record_stays,
        record_contacts=record_contacts,
        record_protections=record_protections,
        granted_at=now,
    )
    store.consents[traveler_id] = consent
    record_audit(
        store,
        actor=traveler_id,
        action="consent_granted",
        target_id=traveler_id,
        occurred_at=now,
        detail="出发前选择风险提示与自愿记录范围",
    )
    return consent


def confirm_itinerary(
    store: TrackerStore,
    *,
    itinerary_id: str,
    traveler_id: str,
    destination_region: str,
    depart_at: datetime,
    return_at: datetime,
    booking_ref: str,
    now: datetime,
    group_id: str | None = None,
) -> TravelItinerary:
    """确认行程；改签使用同一行程标识，只更新班次与时间，不新建行程。"""
    existing = store.itineraries.get(itinerary_id)
    if existing is None:
        existing = TravelItinerary(
            itinerary_id=itinerary_id,
            traveler_id=traveler_id,
            group_id=group_id,
            destination_region=destination_region,
            depart_at=depart_at,
            return_at=return_at,
            booking_ref=booking_ref,
            version=1,
        )
        store.itineraries[itinerary_id] = existing
        summary = "行程首次确认"
    else:
        if existing.traveler_id != traveler_id:
            raise ValueError("行程归属旅客不一致，不能借用他人行程标识改签")
        existing.group_id = group_id
        existing.destination_region = destination_region
        existing.depart_at = depart_at
        existing.return_at = return_at
        existing.booking_ref = booking_ref
        existing.version += 1
        summary = "行程改签确认"
    emit_event(
        store,
        event_type="ITINERARY_CONFIRMED",
        aggregate_type="travel_itinerary",
        aggregate_id=itinerary_id,
        occurred_at=now,
        summary=summary,
    )
    return existing


def _itinerary(store: TrackerStore, itinerary_id: str) -> TravelItinerary:
    itinerary = store.itineraries.get(itinerary_id)
    if itinerary is None:
        raise ValueError(f"行程未确认: {itinerary_id}")
    return itinerary


def _consent_for(store: TrackerStore, traveler_id: str) -> ConsentScope:
    consent = store.consents.get(traveler_id)
    if consent is None:
        raise ConsentScopeError("未登记自愿记录范围，无法写入途中信息")
    return consent


def register_stay(
    store: TrackerStore,
    *,
    itinerary_id: str,
    start: datetime,
    end: datetime,
    region: str,
    now: datetime,
) -> StayInterval:
    """途中登记住宿区间，须在用户自愿记录范围内。"""
    itinerary = _itinerary(store, itinerary_id)
    if not _consent_for(store, itinerary.traveler_id).record_stays:
        raise ConsentScopeError("用户未授权记录住宿区间")
    stay = StayInterval(start=start, end=end, region=region)
    itinerary.stays.append(stay)
    return stay


def register_contact(
    store: TrackerStore,
    *,
    itinerary_id: str,
    kind: str,
    occurred_at: datetime,
    detail: str,
    now: datetime,
) -> ContactEvent:
    """途中登记接触事件（如聚餐），须在用户自愿记录范围内。"""
    itinerary = _itinerary(store, itinerary_id)
    if not _consent_for(store, itinerary.traveler_id).record_contacts:
        raise ConsentScopeError("用户未授权记录接触事件")
    contact = ContactEvent(kind=kind, occurred_at=occurred_at, detail=detail)
    itinerary.contacts.append(contact)
    return contact


def register_protection(
    store: TrackerStore,
    *,
    itinerary_id: str,
    kind: str,
    applied_at: datetime,
    now: datetime,
) -> ProtectionMeasure:
    """途中登记防护措施，须在用户自愿记录范围内。"""
    itinerary = _itinerary(store, itinerary_id)
    if not _consent_for(store, itinerary.traveler_id).record_protections:
        raise ConsentScopeError("用户未授权记录防护措施")
    measure = ProtectionMeasure(kind=kind, applied_at=applied_at)
    itinerary.protections.append(measure)
    return measure


def register_exposure(
    store: TrackerStore,
    *,
    itinerary_id: str,
    exposure_type: ExposureType,
    region: str,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
    scan_id: str | None = None,
) -> tuple[ExposureRecord, bool]:
    """登记一条暴露；返回（记录，是否新建）。

    同一行程、同一暴露类型、同一区域只保留一条记录：改签或补登
    仅合并时间窗；重复扫码按 scan_id 幂等返回已有记录。
    """
    itinerary = _itinerary(store, itinerary_id)
    if scan_id is not None and scan_id in store.scan_index:
        return store.exposures[store.scan_index[scan_id]], False
    for existing in store.exposures.values():
        if (
            existing.itinerary_id == itinerary_id
            and existing.exposure_type is exposure_type
            and existing.region == region
        ):
            existing.window_start = min(existing.window_start, window_start)
            existing.window_end = max(existing.window_end, window_end)
            if scan_id is not None:
                existing.scan_ids.add(scan_id)
                store.scan_index[scan_id] = existing.exposure_id
            return existing, False
    record = ExposureRecord(
        exposure_id=f"exp-{len(store.exposures) + 1:05d}",
        itinerary_id=itinerary_id,
        traveler_id=itinerary.traveler_id,
        exposure_type=exposure_type,
        region=region,
        window_start=window_start,
        window_end=window_end,
        group_id=itinerary.group_id,
        scan_ids={scan_id} if scan_id is not None else set(),
    )
    store.exposures[record.exposure_id] = record
    if scan_id is not None:
        store.scan_index[scan_id] = record.exposure_id
    emit_event(
        store,
        event_type="EXPOSURE_RECORDED",
        aggregate_type="exposure_record",
        aggregate_id=record.exposure_id,
        occurred_at=now,
        summary=f"登记{exposure_type.value}暴露",
    )
    return record, True


def register_group_scan(
    store: TrackerStore,
    *,
    scan_id: str,
    itinerary_ids: list[str],
    exposure_type: ExposureType,
    region: str,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
) -> list[ExposureRecord]:
    """多人同行扫码：每位成员各对应一条暴露，整单重复扫码幂等。"""
    records = []
    for itinerary_id in itinerary_ids:
        record, _ = register_exposure(
            store,
            itinerary_id=itinerary_id,
            exposure_type=exposure_type,
            region=region,
            window_start=window_start,
            window_end=window_end,
            now=now,
            scan_id=f"{scan_id}#{itinerary_id}",
        )
        records.append(record)
    return records
