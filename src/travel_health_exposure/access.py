"""跨区域卫生人员的最小必要查询：调查结束关闭访问，审计保留。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from .domain import AccessGrant, TrackerStore, record_audit


class InvestigationClosedError(PermissionError):
    """调查已结束，访问已关闭。"""


PURPOSE_FIELDS: dict[str, frozenset[str]] = {
    "case_investigation": frozenset({"exposure_type", "region", "window_start", "window_end"}),
    "supervision": frozenset({"exposure_type", "region"}),
}
"""按调查目的登记的最小必要字段，均不含旅客身份。"""


def open_investigation(
    store: TrackerStore,
    *,
    grant_id: str,
    officer_id: str,
    region: str,
    purpose: str,
    now: datetime,
) -> AccessGrant:
    """为跨区域卫生人员开放限定区域与字段的查询授权。"""
    allowed = PURPOSE_FIELDS.get(purpose)
    if allowed is None:
        raise ValueError(f"未登记的调查目的: {purpose}")
    grant = AccessGrant(
        grant_id=grant_id,
        officer_id=officer_id,
        region=region,
        purpose=purpose,
        allowed_fields=allowed,
        opened_at=now,
    )
    store.grants[grant_id] = grant
    record_audit(
        store,
        actor=officer_id,
        action="access_granted",
        target_id=grant_id,
        occurred_at=now,
        detail=f"开放 {purpose} 查询，区域 {region}",
    )
    return grant


def _present(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def query_exposures(store: TrackerStore, grant_id: str, now: datetime) -> list[dict[str, Any]]:
    """按授权查询：只返回职责区域内、最小必要字段的暴露信息。"""
    grant = store.grants.get(grant_id)
    if grant is None:
        raise ValueError(f"查询授权不存在: {grant_id}")
    if grant.closed_at is not None:
        record_audit(
            store,
            actor=grant.officer_id,
            action="access_denied",
            target_id=grant_id,
            occurred_at=now,
            detail="调查已结束",
        )
        raise InvestigationClosedError("调查已结束，访问已关闭")
    rows = []
    for exposure in store.exposures.values():
        if exposure.region != grant.region:
            continue  # 仅限职责区域
        rows.append({name: _present(getattr(exposure, name)) for name in sorted(grant.allowed_fields)})
    record_audit(
        store,
        actor=grant.officer_id,
        action="access_queried",
        target_id=grant_id,
        occurred_at=now,
        detail=f"返回 {len(rows)} 条最小必要字段",
    )
    return rows


def close_investigation(store: TrackerStore, grant_id: str, now: datetime) -> AccessGrant:
    """调查结束：关闭访问，审计记录保留。"""
    grant = store.grants.get(grant_id)
    if grant is None:
        raise ValueError(f"查询授权不存在: {grant_id}")
    grant.closed_at = now
    record_audit(
        store,
        actor=grant.officer_id,
        action="access_closed",
        target_id=grant_id,
        occurred_at=now,
        detail="调查结束，关闭访问并保留审计",
    )
    return grant
