from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 8) -> datetime:
    """2026 年 10 月某日某时的东八区时间。"""
    return datetime(2026, 10, day, hour, tzinfo=TZ)
