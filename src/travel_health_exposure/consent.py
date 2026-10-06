"""就医共享、撤回与公共卫生留存。

不变量：

* 用户出发前选择自愿记录范围（记录类别授权），就医时再对具体医生授予查看授权；
* 医生看到的是"经过确认的旅居史"：用户逐条确认后才进入共享视图；
* 撤回只关闭未来查看，不删除已经发生的查看审计；
* 已用于公共卫生通知的事实，撤回后保留脱敏版本（去标识、不可逆），
  可继续用于统计与通知溯源，但不能再回到具体旅客身份。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .timeutils import Clock, utcnow


class RecordScope(str, Enum):
    """自愿记录范围：用户出发前选择登记哪些类别。"""

    STAYS = "stays"            # 住宿区间
    EXPOSURES = "exposures"    # 接触事件
    PROTECTIONS = "protections"  # 防护措施
    SYMPTOMS = "symptoms"      # 异常症状


@dataclass(frozen=True)
class RecordingPreference:
    user_id: str
    scopes: frozenset[RecordScope]

    def allows(self, scope: RecordScope) -> bool:
        return scope in self.scopes


class ShareStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass
class ShareGrant:
    grant_id: str
    user_id: str
    clinician_id: str
    jurisdiction: str
    """授权就医所在辖区，供最小授权校验。"""
    fields: frozenset[str]
    """授权字段白名单，超出白名单的字段不得出现在共享视图。"""
    status: ShareStatus = ShareStatus.ACTIVE
    granted_at: datetime = field(default_factory=utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class AccessRecord:
    """医生查看审计：撤回不清除。"""

    grant_id: str
    clinician_id: str
    jurisdiction: str
    fields_viewed: tuple[str, ...]
    viewed_at: datetime
    purpose: str


@dataclass(frozen=True)
class ConfirmedTravelFact:
    """用户确认过的旅居史条目，进入医生共享视图的最小载体。"""

    fact_type: str  # stay / exposure / protection
    region: str
    started_on: str | None
    ended_on: str | None
    detail: str
    confirmed_at: datetime


def pseudonymize(user_id: str, salt: str) -> str:
    """不可逆假名：公共卫生留存版本无法回溯到旅客身份。"""
    digest = hashlib.sha256(f"{salt}:{user_id}".encode("utf-8")).hexdigest()
    return f"pid-{digest[:16]}"


@dataclass(frozen=True)
class DeidentifiedFact:
    """公共卫生通知使用后的脱敏留存事实。"""

    pseudonym: str
    fact_type: str
    region: str
    window_start: str
    window_end: str
    advisory_code: str
    notification_ids: tuple[str, ...]
    retained_at: datetime

    @classmethod
    def from_notification(
        cls,
        *,
        user_id: str,
        salt: str,
        fact_type: str,
        region: str,
        window_start: str,
        window_end: str,
        advisory_code: str,
        notification_id: str,
        clock: Clock = utcnow,
    ) -> "DeidentifiedFact":
        return cls(
            pseudonym=pseudonymize(user_id, salt),
            fact_type=fact_type,
            region=region,
            window_start=window_start,
            window_end=window_end,
            advisory_code=advisory_code,
            notification_ids=(notification_id,),
            retained_at=clock(),
        )


class ConsentService:
    def __init__(self, salt: str, clock: Clock = utcnow) -> None:
        self._salt = salt
        self._clock = clock
        self._preferences: dict[str, RecordingPreference] = {}
        self._grants: dict[str, ShareGrant] = {}
        self._audit: list[AccessRecord] = []
        self._confirmed: dict[str, list[ConfirmedTravelFact]] = {}
        self._deidentified: dict[tuple[str, str], DeidentifiedFact] = {}

    # -- 记录范围 ------------------------------------------------------

    def set_preference(self, user_id: str, scopes: frozenset[RecordScope]) -> RecordingPreference:
        preference = RecordingPreference(user_id, frozenset(scopes))
        self._preferences[user_id] = preference
        return preference

    def preference(self, user_id: str) -> RecordingPreference | None:
        return self._preferences.get(user_id)

    def recording_allowed(self, user_id: str, scope: RecordScope) -> bool:
        preference = self._preferences.get(user_id)
        return preference is not None and preference.allows(scope)

    # -- 旅居史确认 ----------------------------------------------------

    def confirm_fact(self, user_id: str, fact: ConfirmedTravelFact) -> None:
        self._confirmed.setdefault(user_id, []).append(fact)

    def confirmed_facts(self, user_id: str) -> tuple[ConfirmedTravelFact, ...]:
        return tuple(self._confirmed.get(user_id, ()))

    # -- 授权与查看 ----------------------------------------------------

    def grant_share(
        self,
        *,
        user_id: str,
        clinician_id: str,
        jurisdiction: str,
        fields: frozenset[str],
        expires_at: datetime | None = None,
    ) -> ShareGrant:
        if expires_at is not None:
            if expires_at.tzinfo is None:
                raise ValueError("过期时间必须带时区")
            if expires_at <= self._clock():
                raise ValueError("授权不得在过去过期")
        grant = ShareGrant(
            grant_id=f"grant-{len(self._grants) + 1:04d}",
            user_id=user_id,
            clinician_id=clinician_id,
            jurisdiction=jurisdiction,
            fields=frozenset(fields),
            expires_at=expires_at,
        )
        self._grants[grant.grant_id] = grant
        return grant

    def revoke(self, grant_id: str) -> None:
        grant = self._grants[grant_id]
        grant.status = ShareStatus.REVOKED
        grant.revoked_at = self._clock()

    def open_clinical_view(
        self,
        *,
        grant_id: str,
        purpose: str = "clinical_consultation",
    ) -> tuple[ConfirmedTravelFact, ...]:
        """医生查看；返回字段裁剪后的确认旅居史并写审计。

        撤回/过期后访问被拒绝，但历史审计记录保留。
        """
        grant = self._grants[grant_id]
        now = self._clock()
        if grant.status is ShareStatus.REVOKED:
            raise PermissionError("授权已撤回")
        if grant.expires_at is not None and grant.expires_at <= now:
            raise PermissionError("授权已过期")

        facts = self._confirmed.get(grant.user_id, ())
        viewed: list[ConfirmedTravelFact] = []
        viewed_fields: set[str] = set()
        for fact in facts:
            projected = self._project(fact, grant.fields)
            if projected is not None:
                viewed.append(projected)
                viewed_fields.update(grant.fields & self._fact_fields(fact))
        self._audit.append(
            AccessRecord(
                grant_id=grant.grant_id,
                clinician_id=grant.clinician_id,
                jurisdiction=grant.jurisdiction,
                fields_viewed=tuple(sorted(viewed_fields)),
                viewed_at=now,
                purpose=purpose,
            )
        )
        return tuple(viewed)

    @staticmethod
    def _fact_fields(fact: ConfirmedTravelFact) -> frozenset[str]:
        fields = {"fact_type", "region", "detail"}
        if fact.started_on is not None:
            fields.add("started_on")
        if fact.ended_on is not None:
            fields.add("ended_on")
        return frozenset(fields)

    @staticmethod
    def _project(fact: ConfirmedTravelFact, allowed: frozenset[str]) -> ConfirmedTravelFact | None:
        if "fact_type" not in allowed or "region" not in allowed:
            return None
        return ConfirmedTravelFact(
            fact_type=fact.fact_type,
            region=fact.region,
            started_on=fact.started_on if "started_on" in allowed else None,
            ended_on=fact.ended_on if "ended_on" in allowed else None,
            detail=fact.detail if "detail" in allowed else "",
            confirmed_at=fact.confirmed_at,
        )

    def audit_log(self) -> tuple[AccessRecord, ...]:
        """审计只增不改；撤回后仍可完整追溯。"""
        return tuple(self._audit)

    # -- 公共卫生脱敏留存 ----------------------------------------------

    def retain_for_public_health(self, fact: DeidentifiedFact) -> DeidentifiedFact:
        key = (fact.pseudonym, (fact.region, fact.window_start, fact.window_end, fact.advisory_code))
        existing = self._deidentified.get(key)
        if existing is not None:
            merged = DeidentifiedFact(
                pseudonym=existing.pseudonym,
                fact_type=existing.fact_type,
                region=existing.region,
                window_start=existing.window_start,
                window_end=existing.window_end,
                advisory_code=existing.advisory_code,
                notification_ids=tuple(sorted(set(existing.notification_ids) | set(fact.notification_ids))),
                retained_at=existing.retained_at,
            )
            self._deidentified[key] = merged
            return merged
        self._deidentified[key] = fact
        return fact

    def deidentified_facts(self) -> tuple[DeidentifiedFact, ...]:
        return tuple(self._deidentified.values())
