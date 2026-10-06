"""异常症状上报：本人或医生均可上报，规则提示与诊断严格分开。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .domain import (
    MONITORING_DURATIONS,
    ExposureType,
    ReporterRole,
    RuleAlert,
    SymptomReport,
    TrackerStore,
    emit_event,
    record_audit,
)


@dataclass(frozen=True)
class SymptomRule:
    """监测期内症状与暴露类型的匹配规则。"""

    rule_id: str
    exposure_type: ExposureType
    symptoms: frozenset[str]
    message: str


SYMPTOM_RULES: tuple[SymptomRule, ...] = (
    SymptomRule(
        "R-MOSQ-01",
        ExposureType.MOSQUITO_BORNE,
        frozenset({"发热", "皮疹"}),
        "蚊媒暴露监测期内出现发热或皮疹，建议按蚊媒疾病排查",
    ),
    SymptomRule(
        "R-FOOD-01",
        ExposureType.FOODBORNE_GATHERING,
        frozenset({"腹泻", "呕吐"}),
        "聚餐暴露监测期内出现胃肠道症状，注意食源性疾病风险",
    ),
    SymptomRule(
        "R-POLLEN-01",
        ExposureType.POLLEN,
        frozenset({"打喷嚏", "喘息"}),
        "花粉暴露监测期内出现呼吸道症状，考虑过敏因素",
    ),
)


def report_symptoms(
    store: TrackerStore,
    *,
    report_id: str,
    traveler_id: str,
    reporter: ReporterRole,
    symptoms: tuple[str, ...],
    now: datetime,
    diagnosis: str | None = None,
) -> SymptomReport:
    """本人或医生上报异常症状；系统只给规则提示，不在此下诊断。"""
    if diagnosis is not None:
        raise ValueError("上报入口不接受诊断结论，诊断须由医生另行登记")
    symptom_set = set(symptoms)
    matched: list[RuleAlert] = []
    for exposure in store.exposures.values():
        if exposure.traveler_id != traveler_id:
            continue
        monitor_until = exposure.window_end + MONITORING_DURATIONS[exposure.exposure_type]
        if now > monitor_until:
            continue  # 监测期外的暴露不再触发规则提示
        for rule in SYMPTOM_RULES:
            if rule.exposure_type is exposure.exposure_type and rule.symptoms & symptom_set:
                matched.append(
                    RuleAlert(rule_id=rule.rule_id, exposure_type=rule.exposure_type, message=rule.message)
                )
    report = SymptomReport(
        report_id=report_id,
        traveler_id=traveler_id,
        reporter=reporter,
        symptoms=tuple(symptoms),
        reported_at=now,
        rule_alerts=matched,
    )
    store.reports[report_id] = report
    emit_event(
        store,
        event_type="SYMPTOM_REPORTED",
        aggregate_type="symptom_report",
        aggregate_id=report_id,
        occurred_at=now,
        summary=f"异常症状上报（{reporter.value}），命中规则提示 {len(matched)} 条",
    )
    return report


def record_diagnosis(
    store: TrackerStore,
    report_id: str,
    *,
    clinician_id: str,
    diagnosis: str,
    now: datetime,
) -> SymptomReport:
    """医生登记诊断结论，与系统规则提示分开保存、互不改写。"""
    if not clinician_id.strip():
        raise ValueError("诊断必须登记医生标识")
    report = store.reports.get(report_id)
    if report is None:
        raise ValueError(f"症状上报不存在: {report_id}")
    report.diagnosis = diagnosis
    report.diagnosed_by = clinician_id
    record_audit(
        store,
        actor=clinician_id,
        action="diagnosis_recorded",
        target_id=report_id,
        occurred_at=now,
        detail="医生登记诊断，与规则提示分开保存",
    )
    return report
