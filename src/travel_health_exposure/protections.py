"""途中防护措施登记（驱蚊、饮食防护、口罩等）。

防护记录同样受用户出发前选择的自愿记录范围约束，
并在用户确认后可作为旅居史的一部分共享给医生。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


PROTECTION_KINDS: frozenset[str] = frozenset(
    {
        "insect_repellent",  # 驱蚊剂
        "bed_net",           # 蚊帐
        "safe_food_water",   # 安全饮食饮水
        "mask",              # 口罩
        "prophylaxis",       # 预防服药
    }
)


@dataclass(frozen=True)
class ProtectionMeasure:
    measure_id: str
    user_id: str
    kind: str
    date: date
    location_id: str | None = None
    note: str = ""


class ProtectionLog:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._measures: dict[str, ProtectionMeasure] = {}

    def record(
        self,
        *,
        kind: str,
        date: date,
        location_id: str | None = None,
        note: str = "",
    ) -> ProtectionMeasure:
        if kind not in PROTECTION_KINDS:
            raise ValueError(f"未登记的防护类型: {kind}")
        measure = ProtectionMeasure(
            measure_id=f"prot-{len(self._measures) + 1:04d}",
            user_id=self.user_id,
            kind=kind,
            date=date,
            location_id=location_id,
            note=note,
        )
        # 同日同地同类防护幂等，重复登记不产生多条。
        key = (kind, date, location_id)
        for existing in self._measures.values():
            if (existing.kind, existing.date, existing.location_id) == key:
                return existing
        self._measures[measure.measure_id] = measure
        return measure

    def list_measures(self) -> tuple[ProtectionMeasure, ...]:
        return tuple(self._measures.values())
