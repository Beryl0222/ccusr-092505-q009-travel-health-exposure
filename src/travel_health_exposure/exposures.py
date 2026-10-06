"""行程与暴露登记。

关键不变量：

* 行程改签（区间改期、退订重建）只更新同一条住宿区间，不产生新暴露；
* 同一人重复扫同一个场所码、重复登记重叠区间，归并到同一条暴露；
* 多人同行只在暴露事实上挂接同行人，不为同一人制造多条暴露；
* 监测窗口自暴露结束日（``window_end``）起算，区间延长/改期会抬高版本号，
  提醒层据此重排尚未确认的提醒。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from .risks import EXPOSURE_TYPES
from .timeutils import Clock, require_aware, utcnow


class TripStatus(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"  # 退订：行程事实保留（可能已发生部分停留），状态标记取消


@dataclass
class StayInterval:
    """一段住宿/停留区间，是行程内稳定的归并锚点，改签不改标识。"""

    stay_id: str
    location_id: str
    check_in: date
    check_out: date

    def __post_init__(self) -> None:
        if self.check_out < self.check_in:
            raise ValueError("离开日不得早于入住日")

    def overlaps(self, other: "StayInterval") -> bool:
        return (
            self.location_id == other.location_id
            and self.check_in <= other.check_out
            and other.check_in <= self.check_out
        )


@dataclass
class Trip:
    trip_id: str
    user_id: str
    regions: tuple[str, ...]
    booking_refs: set[str] = field(default_factory=set)
    companions: frozenset[str] = frozenset()
    status: TripStatus = TripStatus.CONFIRMED
    stays: dict[str, StayInterval] = field(default_factory=dict)
    version: int = 1

    def add_booking_ref(self, ref: str) -> None:
        self.booking_refs.add(ref)

    def upsert_stay(self, stay: StayInterval) -> bool:
        """登记或按 ``stay_id`` 覆盖区间（改签）。返回是否为新建。"""
        created = stay.stay_id not in self.stays
        self.stays[stay.stay_id] = stay
        self.version += 1
        return created

    def cancel(self) -> None:
        self.status = TripStatus.CANCELLED
        self.version += 1


@dataclass
class Exposure:
    """一条归并后的暴露事实。"""

    exposure_id: str
    user_id: str
    exposure_type: str
    location_id: str
    window_start: date
    window_end: date
    trip_ids: set[str] = field(default_factory=set)
    stay_ids: set[str] = field(default_factory=set)
    scan_codes: set[str] = field(default_factory=set)
    companion_ids: frozenset[str] = frozenset()
    version: int = 1

    def _bump(self) -> None:
        self.version += 1

    def _merge_window(self, start: date, end: date) -> bool:
        new_start = min(self.window_start, start)
        new_end = max(self.window_end, end)
        changed = (new_start, new_end) != (self.window_start, self.window_end)
        self.window_start, self.window_end = new_start, new_end
        return changed


@dataclass(frozen=True)
class RegistrationResult:
    exposure: Exposure
    created: bool
    merged_scan: bool
    """是否命中了重复扫码归并。"""


class ExposureRegistry:
    """单用户的暴露登记簿，负责幂等归并。"""

    def __init__(self, user_id: str, clock: Clock = utcnow) -> None:
        self.user_id = user_id
        self._exposures: dict[str, Exposure] = {}
        self._scan_index: dict[str, str] = {}  # scan_code -> exposure_id
        self._clock = clock

    # -- 查询 ----------------------------------------------------------

    def list_exposures(self) -> tuple[Exposure, ...]:
        return tuple(self._exposures.values())

    def get(self, exposure_id: str) -> Exposure:
        return self._exposures[exposure_id]

    # -- 登记 ----------------------------------------------------------

    def register(
        self,
        *,
        exposure_type: str,
        location_id: str,
        window_start: date,
        window_end: date | None = None,
        trip_id: str | None = None,
        stay_id: str | None = None,
        scan_code: str | None = None,
        companion_ids: frozenset[str] = frozenset(),
        occurred_on: date | None = None,
    ) -> RegistrationResult:
        if exposure_type not in EXPOSURE_TYPES:
            raise ValueError(f"未登记的暴露类型: {exposure_type}")
        end = window_end if window_end is not None else (occurred_on or window_start)
        if end < window_start:
            raise ValueError("暴露结束日不得早于开始日")
        if self.user_id in companion_ids:
            companion_ids = frozenset(companion_ids - {self.user_id})

        merged_scan = False
        target: Exposure | None = None

        if scan_code is not None and scan_code in self._scan_index:
            target = self._exposures[self._scan_index[scan_code]]
            merged_scan = True

        if target is None:
            target = self._find_mergeable(exposure_type, location_id, window_start, end, stay_id)

        if target is None:
            require_aware(self._clock(), "登记时间")  # 时钟必须带时区，领域时间不留歧义
            exposure = Exposure(
                exposure_id=f"exp-{len(self._exposures) + 1:04d}",
                user_id=self.user_id,
                exposure_type=exposure_type,
                location_id=location_id,
                window_start=window_start,
                window_end=end,
                trip_ids={trip_id} if trip_id else set(),
                stay_ids={stay_id} if stay_id else set(),
                scan_codes={scan_code} if scan_code else set(),
                companion_ids=frozenset(companion_ids),
            )
            if scan_code is not None:
                self._scan_index[scan_code] = exposure.exposure_id
            self._exposures[exposure.exposure_id] = exposure
            return RegistrationResult(exposure, True, False)

        changed = False
        if scan_code is not None and scan_code not in target.scan_codes:
            target.scan_codes.add(scan_code)
            self._scan_index[scan_code] = target.exposure_id
            changed = True
        if target._merge_window(window_start, end):
            changed = True
        if trip_id and trip_id not in target.trip_ids:
            target.trip_ids.add(trip_id)
            changed = True
        if stay_id and stay_id not in target.stay_ids:
            target.stay_ids.add(stay_id)
            changed = True
        new_companions = target.companion_ids | frozenset(companion_ids)
        if new_companions != target.companion_ids:
            target.companion_ids = new_companions
            changed = True
        if changed:
            target._bump()
        return RegistrationResult(target, False, merged_scan)

    def _find_mergeable(
        self,
        exposure_type: str,
        location_id: str,
        start: date,
        end: date,
        stay_id: str | None,
    ) -> Exposure | None:
        # 同一住宿区间锚点直接归并（改签后再次登记）。
        if stay_id is not None:
            for exposure in self._exposures.values():
                if stay_id in exposure.stay_ids:
                    return exposure
        for exposure in self._exposures.values():
            if (
                exposure.exposure_type == exposure_type
                and exposure.location_id == location_id
                and start <= exposure.window_end
                and exposure.window_start <= end
            ):
                return exposure
        return None

    def reschedule(
        self,
        stay_id: str,
        *,
        check_in: date,
        check_out: date,
    ) -> Exposure:
        """改签：平移区间到新日期，保持同一条暴露，不做并集扩张。"""
        if check_out < check_in:
            raise ValueError("离开日不得早于入住日")
        for exposure in self._exposures.values():
            if stay_id in exposure.stay_ids:
                if (check_in, check_out) != (exposure.window_start, exposure.window_end):
                    exposure.window_start, exposure.window_end = check_in, check_out
                    exposure._bump()
                return exposure
        raise KeyError(f"未知住宿区间: {stay_id}")
