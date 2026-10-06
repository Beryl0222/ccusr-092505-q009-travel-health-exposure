"""时间工具：领域内统一使用带时区的时间。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def require_aware(value: datetime, field: str = "时间") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field}必须带时区")
    return value
