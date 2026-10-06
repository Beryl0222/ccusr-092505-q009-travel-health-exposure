"""异常症状上报：规则提示与诊断严格分离。

* 本人或接诊医生均可上报异常症状；
* 系统依据风险目录给出"规则提示"（建议排查方向、观察要点），
  规则提示永远是建议文本，不作为诊断写入；
* 诊断只能由医生单独记录并签名式挂接到报告，症状上报本身不含诊断字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .exposures import Exposure
from .risks import RISK_HINT, RiskCatalog, SYMPTOM_CODES
from .timeutils import Clock, require_aware, utcnow


class Reporter(str, Enum):
    SELF = "self"
    CLINICIAN = "clinician"


@dataclass(frozen=True)
class RuleHint:
    """系统生成的规则提示：建议性质，明确标注非诊断。"""

    advisory_code: str
    message: str
    suggested_symptoms: tuple[str, ...]

    @property
    def is_diagnosis(self) -> bool:
        return False


@dataclass
class Diagnosis:
    """医生做出的诊断，独立于症状上报存在。"""

    diagnosis_id: str
    report_id: str
    clinician_id: str
    condition_code: str
    note: str
    diagnosed_at: datetime


@dataclass
class SymptomReport:
    report_id: str
    user_id: str
    reporter: Reporter
    symptoms: frozenset[str]
    occurred_at: datetime
    note: str = ""
    clinician_id: str | None = None
    received_at: datetime = field(default_factory=utcnow)
    rule_hints: tuple[RuleHint, ...] = ()
    diagnosis: Diagnosis | None = None
    follow_ups: list[str] = field(default_factory=list)
    """后续处置记录（公卫联系、转诊等）的标识/摘要，仅追加。"""

    @property
    def has_diagnosis(self) -> bool:
        return self.diagnosis is not None


class SymptomService:
    def __init__(self, catalog: RiskCatalog, clock: Clock = utcnow) -> None:
        self._catalog = catalog
        self._clock = clock
        self._reports: dict[str, SymptomReport] = {}

    def _build_hints(
        self,
        symptoms: frozenset[str],
        exposures: tuple[Exposure, ...],
        region: str | None,
    ) -> tuple[RuleHint, ...]:
        hints: list[RuleHint] = []
        seen: set[str] = set()
        for exposure in exposures:
            advisory = self._catalog.resolve(region, exposure.exposure_type)
            if advisory.code in seen:
                continue
            overlap = symptoms & frozenset(advisory.symptoms)
            if not overlap:
                continue
            seen.add(advisory.code)
            hints.append(
                RuleHint(
                    advisory_code=advisory.code,
                    message=(
                        f"{RISK_HINT}：症状与「{advisory.title}」观察项 "
                        f"({'、'.join(sorted(overlap))}) 相关，建议结合旅居史评估；本提示非诊断。"
                    ),
                    suggested_symptoms=tuple(sorted(overlap)),
                )
            )
        return tuple(hints)

    def report(
        self,
        *,
        user_id: str,
        symptoms: frozenset[str],
        occurred_at: datetime,
        reporter: Reporter,
        exposures: tuple[Exposure, ...] = (),
        region: str | None = None,
        note: str = "",
        clinician_id: str | None = None,
    ) -> SymptomReport:
        require_aware(occurred_at, "症状发生时间")
        unknown = symptoms - SYMPTOM_CODES
        if unknown:
            raise ValueError(f"未登记的症状代码: {sorted(unknown)}")
        if reporter is Reporter.CLINICIAN and not clinician_id:
            raise ValueError("医生上报必须提供 clinician_id")
        for exposure in exposures:
            if exposure.user_id != user_id:
                raise ValueError("只能基于本人暴露生成规则提示")
        report = SymptomReport(
            report_id=f"sym-{len(self._reports) + 1:04d}",
            user_id=user_id,
            reporter=reporter,
            symptoms=frozenset(symptoms),
            occurred_at=occurred_at,
            note=note,
            clinician_id=clinician_id,
            received_at=self._clock(),
            rule_hints=self._build_hints(frozenset(symptoms), exposures, region),
        )
        self._reports[report.report_id] = report
        return report

    def attach_diagnosis(
        self,
        *,
        report_id: str,
        clinician_id: str,
        condition_code: str,
        note: str = "",
    ) -> Diagnosis:
        """只有医生可以挂接诊断；诊断不覆盖规则提示。"""
        report = self._reports[report_id]
        if not clinician_id.strip():
            raise ValueError("诊断必须由医生签署")
        diagnosis = Diagnosis(
            diagnosis_id=f"dx-{report_id}",
            report_id=report_id,
            clinician_id=clinician_id,
            condition_code=condition_code,
            note=note,
            diagnosed_at=self._clock(),
        )
        report.diagnosis = diagnosis
        return diagnosis

    def add_follow_up(self, report_id: str, action: str) -> None:
        self._reports[report_id].follow_ups.append(action)

    def get(self, report_id: str) -> SymptomReport:
        return self._reports[report_id]

    def list_for_user(self, user_id: str) -> tuple[SymptomReport, ...]:
        return tuple(r for r in self._reports.values() if r.user_id == user_id)
