"""风险提示目录：风险依据与监测期限的单一事实来源。

规则只回答"某类暴露建议观察什么、观察多久"，不产生诊断结论。
期限为暴露结束日（``window_starts``）起的自然日上限，为便于跨地区比较，
目录统一按自然日登记；具体辖区可在自己的目录版本中调整数值。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RiskAdvisory:
    """一条风险提示依据，可被提醒、自我监测建议和追溯链引用。"""

    code: str
    title: str
    exposure_type: str
    """暴露类型，取值见 :data:`EXPOSURE_TYPES`。"""
    monitor_days: int
    """建议自我监测的自然日数（自暴露结束日起）。"""
    region: str | None = None
    """适用地区代码；``None`` 表示通用基线。"""
    advisory_version: str = "1"
    symptoms: tuple[str, ...] = ()
    """建议留意的症状代码，仅用于规则提示。"""
    source: str = ""
    """规则来源说明（指南名称/发布机构），供追溯。"""

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("风险提示代码不能为空")
        if self.exposure_type not in EXPOSURE_TYPES:
            raise ValueError(f"未登记的暴露类型: {self.exposure_type}")
        if self.monitor_days < 1:
            raise ValueError("监测期限必须至少 1 天")
        if self.region is not None and not self.region.strip():
            raise ValueError("地区代码不能为空字符串")

    def window_end(self, window_start: date) -> date:
        """暴露结束日 + 监测期限，返回监测截止日。"""
        from datetime import timedelta

        return window_start + timedelta(days=self.monitor_days)


# 登记过的暴露类型。登记新类型时同步补充默认目录。
EXPOSURE_TYPES: frozenset[str] = frozenset(
    {
        "mosquito_borne",  # 蚊媒（登革热、疟疾、寨卡等流行区停留/叮咬）
        "foodborne",  # 聚餐/共餐暴露
        "pollen",  # 花粉高峰停留
        "waterborne",  # 疫水/饮用水暴露
        "respiratory",  # 密闭聚集呼吸道暴露
        "animal_contact",  # 动物接触
    }
)

# 内置症状代码（规则提示用语料，不是诊断）。
RISK_HINT = "规则提示（非诊断）"

SYMPTOM_CODES: frozenset[str] = frozenset(
    {
        "fever",
        "rash",
        "joint_pain",
        "diarrhea",
        "vomiting",
        "abdominal_pain",
        "cough",
        "sore_throat",
        "breathlessness",
        "jaundice",
    }
)

_BASELINE: tuple[RiskAdvisory, ...] = (
    RiskAdvisory(
        code="RISK-MOSQUITO-GENERAL",
        title="蚊媒疾病流行区停留",
        exposure_type="mosquito_borne",
        monitor_days=14,
        advisory_version="1",
        symptoms=("fever", "rash", "joint_pain"),
        source="通用基线：蚊媒传染病潜伏期观察建议",
    ),
    RiskAdvisory(
        code="RISK-FOOD-GATHERING",
        title="聚餐/共餐暴露",
        exposure_type="foodborne",
        monitor_days=7,
        advisory_version="1",
        symptoms=("diarrhea", "vomiting", "abdominal_pain", "fever"),
        source="通用基线：食源性疾病观察建议",
    ),
    RiskAdvisory(
        code="RISK-POLLEN-PEAK",
        title="花粉高峰地区停留",
        exposure_type="pollen",
        monitor_days=14,
        advisory_version="1",
        symptoms=("cough", "sore_throat", "breathlessness"),
        source="通用基线：花粉季过敏/呼吸道症状自我观察",
    ),
)


class RiskCatalog:
    """按 (地区, 暴露类型) 解析风险提示；缺省回退到通用基线。"""

    def __init__(self, advisories: tuple[RiskAdvisory, ...] = _BASELINE) -> None:
        self._index: dict[tuple[str, str], RiskAdvisory] = {}
        for advisory in advisories:
            key = (advisory.region or "", advisory.exposure_type)
            if key in self._index:
                raise ValueError(f"目录中存在重复键: {key}")
            self._index[key] = advisory

    def resolve(self, region: str | None, exposure_type: str) -> RiskAdvisory:
        if exposure_type not in EXPOSURE_TYPES:
            raise KeyError(f"未登记的暴露类型: {exposure_type}")
        regional = self._index.get((region or "", exposure_type))
        if regional is not None:
            return regional
        baseline = self._index.get(("", exposure_type))
        if baseline is None:
            raise KeyError(f"暴露类型缺少默认风险提示: {exposure_type}")
        return baseline

    def get(self, code: str) -> RiskAdvisory:
        for advisory in self._index.values():
            if advisory.code == code:
                return advisory
        raise KeyError(f"未登记的风险提示: {code}")

    def all_codes(self) -> tuple[str, ...]:
        return tuple(advisory.code for advisory in self._index.values())
