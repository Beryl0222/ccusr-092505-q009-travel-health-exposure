"""返程后自我监测提醒。

不变量：

* 不同暴露类型按风险目录给出不同的监测期限，提醒截止日来自规则依据；
* 提醒的本地期限（``due_date``）在排程时即持久化，发送失败不改变期限；
* 只补发"未确认"内容，用户已确认的提醒不重发；
* 暴露区间改签时，仅重排尚未确认的提醒。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Protocol

from .exposures import Exposure
from .risks import RiskCatalog
from .timeutils import Clock, utcnow


class ReminderStatus(str, Enum):
    PENDING = "pending"      # 已排程，尚未成功送达
    SENT = "sent"            # 已送达，等待用户确认
    CONFIRMED = "confirmed"  # 用户已确认，不再补发
    FAILED = "failed"        # 最近一次发送失败，期限保留，等待补发


@dataclass
class Reminder:
    reminder_id: str
    user_id: str
    exposure_id: str
    advisory_code: str
    monitor_days: int
    due_date: date
    """监测截止日本地期限，一经排程即保留。"""
    message: str
    status: ReminderStatus = ReminderStatus.PENDING
    attempts: int = 0
    last_attempt_at: datetime | None = None
    last_error: str = ""
    confirmed_at: datetime | None = None
    basis_version: int = 1
    """排程所依据的暴露版本。"""


class DeliveryGateway(Protocol):
    def send(self, reminder: Reminder) -> None:
        """送达提醒；失败时抛出异常。"""
        ...


@dataclass(frozen=True)
class DispatchOutcome:
    reminder: Reminder
    delivered: bool
    error: str = ""


class MonitoringService:
    def __init__(self, catalog: RiskCatalog, gateway: DeliveryGateway, clock: Clock = utcnow) -> None:
        self._catalog = catalog
        self._gateway = gateway
        self._clock = clock
        self._reminders: dict[str, Reminder] = {}
        # (user_id, exposure_id) -> reminder_id，一条暴露一份监测提醒
        self._index: dict[tuple[str, str], str] = {}

    # -- 排程 ----------------------------------------------------------

    def schedule_for_exposure(self, exposure: Exposure, region: str | None = None) -> Reminder:
        advisory = self._catalog.resolve(region, exposure.exposure_type)
        due = advisory.window_end(exposure.window_end)
        reminder_id = self._index.get((exposure.user_id, exposure.exposure_id))
        if reminder_id is not None:
            reminder = self._reminders[reminder_id]
            self._reschedule_if_needed(reminder, exposure, advisory.code, advisory.monitor_days, due)
            return reminder
        message = (
            f"截至 {due.isoformat()} 请自我观察{advisory.monitor_days}天："
            f"{advisory.title}（规则提示，非诊断）"
        )
        reminder = Reminder(
            reminder_id=f"rmd-{len(self._reminders) + 1:04d}",
            user_id=exposure.user_id,
            exposure_id=exposure.exposure_id,
            advisory_code=advisory.code,
            monitor_days=advisory.monitor_days,
            due_date=due,
            message=message,
            basis_version=exposure.version,
        )
        self._reminders[reminder.reminder_id] = reminder
        self._index[(exposure.user_id, exposure.exposure_id)] = reminder.reminder_id
        return reminder

    def _reschedule_if_needed(
        self,
        reminder: Reminder,
        exposure: Exposure,
        advisory_code: str,
        monitor_days: int,
        due: date,
    ) -> None:
        # 已确认的提醒冻结；改签不打扰已完成的监测。
        if reminder.status is ReminderStatus.CONFIRMED:
            return
        if (
            reminder.due_date != due
            or reminder.advisory_code != advisory_code
            or reminder.basis_version != exposure.version
        ):
            reminder.due_date = due
            reminder.advisory_code = advisory_code
            reminder.monitor_days = monitor_days
            reminder.basis_version = exposure.version
            if reminder.status is ReminderStatus.SENT:
                # 新版提醒需要重新送达，但保持未确认语义。
                reminder.status = ReminderStatus.PENDING

    # -- 发送/补发 -----------------------------------------------------

    def due_reminders(self, today: date) -> tuple[Reminder, ...]:
        return tuple(
            r
            for r in self._reminders.values()
            if r.due_date <= today and r.status in (ReminderStatus.PENDING, ReminderStatus.FAILED)
        )

    def dispatch_due(self, today: date) -> tuple[DispatchOutcome, ...]:
        """尝试送达所有到期且未确认的提醒；失败保留本地期限。"""
        outcomes: list[DispatchOutcome] = []
        for reminder in self.due_reminders(today):
            outcomes.append(self._attempt(reminder))
        return tuple(outcomes)

    def _attempt(self, reminder: Reminder) -> DispatchOutcome:
        reminder.attempts += 1
        reminder.last_attempt_at = self._clock()
        try:
            self._gateway.send(reminder)
        except Exception as exc:  # 通道故障：领域状态不丢
            reminder.status = ReminderStatus.FAILED
            reminder.last_error = str(exc)
            return DispatchOutcome(reminder, False, str(exc))
        reminder.status = ReminderStatus.SENT
        reminder.last_error = ""
        return DispatchOutcome(reminder, True)

    def confirm(self, reminder_id: str) -> Reminder:
        reminder = self._reminders[reminder_id]
        reminder.status = ReminderStatus.CONFIRMED
        reminder.confirmed_at = self._clock()
        return reminder

    def recover(self, today: date) -> tuple[DispatchOutcome, ...]:
        """通道恢复后补发：等价于重新处理到期未确认队列。"""
        return self.dispatch_due(today)

    # -- 查询 ----------------------------------------------------------

    def get(self, reminder_id: str) -> Reminder:
        return self._reminders[reminder_id]

    def for_exposure(self, exposure_id: str) -> Reminder | None:
        for key, rid in self._index.items():
            if key[1] == exposure_id:
                return self._reminders[rid]
        return None

    def all_reminders(self) -> tuple[Reminder, ...]:
        return tuple(self._reminders.values())
