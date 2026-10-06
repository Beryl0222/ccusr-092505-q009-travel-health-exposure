"""跨区域卫生人员查询：最小授权、按辖区限定、调查结束即关闭。

* 每位调查员只在被授予的辖区集合和字段白名单内查询；
* 查询必须挂在一个有时限的调查案件上，案件关闭后访问立即失效；
* 所有查询写审计，案件关闭不删除审计；
* 返回结果默认掩码：只回必要字段，不暴露无关旅客身份。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .timeutils import Clock, utcnow


# 调查场景允许返回的字段白名单层级。
FIELD_REGION = "region"
FIELD_EXPOSURE_TYPE = "exposure_type"
FIELD_WINDOW = "window"              # 起止日期
FIELD_PSEUDONYM = "pseudonym"        # 假名，可跨记录关联但不可识别身份
FIELD_USER_ID = "user_id"            # 真实身份，默认不授予
FIELD_SYMPTOMS = "symptoms"
FIELD_ADVISORY = "advisory_code"

MINIMAL_FIELDS: frozenset[str] = frozenset(
    {FIELD_REGION, FIELD_EXPOSURE_TYPE, FIELD_WINDOW, FIELD_PSEUDONYM, FIELD_ADVISORY}
)


class CaseStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"  # 调查结束：访问关闭，审计保留


@dataclass
class InvestigationCase:
    case_id: str
    investigator_id: str
    jurisdictions: frozenset[str]
    allowed_fields: frozenset[str]
    reason: str
    status: CaseStatus = CaseStatus.OPEN
    opened_at: datetime = field(default_factory=utcnow)
    closed_at: datetime | None = None


@dataclass(frozen=True)
class QueryAudit:
    case_id: str
    investigator_id: str
    jurisdiction: str
    filters: tuple[tuple[str, str], ...]
    returned_fields: tuple[str, ...]
    queried_at: datetime
    denied: bool = False
    deny_reason: str = ""


@dataclass(frozen=True)
class ScopedRecord:
    """经过字段掩码的一条记录。"""

    fields: dict[str, object]


class OversightService:
    def __init__(self, salt: str, clock: Clock = utcnow) -> None:
        self._salt = salt
        self._clock = clock
        self._cases: dict[str, InvestigationCase] = {}
        self._audit: list[QueryAudit] = []

    # -- 案件 ----------------------------------------------------------

    def open_case(
        self,
        *,
        investigator_id: str,
        jurisdictions: frozenset[str],
        allowed_fields: frozenset[str] = MINIMAL_FIELDS,
        reason: str = "",
    ) -> InvestigationCase:
        if not jurisdictions:
            raise ValueError("调查案件至少覆盖一个辖区")
        if FIELD_USER_ID in allowed_fields:
            raise ValueError("真实身份字段需单独审批，不得随调查默认授予")
        case = InvestigationCase(
            case_id=f"case-{len(self._cases) + 1:04d}",
            investigator_id=investigator_id,
            jurisdictions=frozenset(jurisdictions),
            allowed_fields=frozenset(allowed_fields),
            reason=reason,
        )
        self._cases[case.case_id] = case
        return case

    def close_case(self, case_id: str) -> None:
        case = self._cases[case_id]
        case.status = CaseStatus.CLOSED
        case.closed_at = self._clock()

    def case(self, case_id: str) -> InvestigationCase:
        return self._cases[case_id]

    # -- 查询 ----------------------------------------------------------

    def query(
        self,
        *,
        case_id: str,
        jurisdiction: str,
        records: tuple[dict[str, object], ...],
        filters: dict[str, str] | None = None,
    ) -> tuple[ScopedRecord, ...]:
        """在案件授权范围内对一批原始记录做辖区过滤与字段掩码。

        ``records`` 由调用方从各业务库汇集；本服务只负责准入与投影。
        """
        case = self._cases[case_id]
        now = self._clock()
        filter_tuple = tuple(sorted((filters or {}).items()))

        if case.status is CaseStatus.CLOSED:
            self._deny(case, jurisdiction, filter_tuple, "案件已关闭，访问终止")
            raise PermissionError("案件已关闭，访问终止")
        if jurisdiction not in case.jurisdictions:
            self._deny(case, jurisdiction, filter_tuple, "辖区不在职责范围内")
            raise PermissionError("辖区不在职责范围内")

        projected: list[ScopedRecord] = []
        for raw in records:
            if raw.get("region") != jurisdiction:
                continue  # 越辖区数据即使混在输入里也不返回
            projected.append(self._project(case, raw))
        self._audit.append(
            QueryAudit(
                case_id=case.case_id,
                investigator_id=case.investigator_id,
                jurisdiction=jurisdiction,
                filters=filter_tuple,
                returned_fields=tuple(sorted(case.allowed_fields)),
                queried_at=now,
            )
        )
        return tuple(projected)

    def _project(self, case: InvestigationCase, raw: dict[str, object]) -> ScopedRecord:
        out: dict[str, object] = {}
        for name in case.allowed_fields:
            if name == FIELD_PSEUDONYM:
                from .consent import pseudonymize

                out[name] = pseudonymize(str(raw.get("user_id", "")), self._salt)
            elif name in raw:
                out[name] = raw[name]
        return ScopedRecord(out)

    def _deny(
        self,
        case: InvestigationCase,
        jurisdiction: str,
        filters: tuple[tuple[str, str], ...],
        reason: str,
    ) -> None:
        self._audit.append(
            QueryAudit(
                case_id=case.case_id,
                investigator_id=case.investigator_id,
                jurisdiction=jurisdiction,
                filters=filters,
                returned_fields=(),
                queried_at=self._clock(),
                denied=True,
                deny_reason=reason,
            )
        )

    def audit_log(self) -> tuple[QueryAudit, ...]:
        """审计只增；案件关闭后仍可逐条复核，包括被拒绝的查询。"""
        return tuple(self._audit)
