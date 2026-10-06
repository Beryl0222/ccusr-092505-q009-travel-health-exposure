# 旅居健康暴露追踪器

面向公共卫生场景的领域规则库：让用户在出发前选择目的地风险提示与自愿记录范围，
途中登记住宿区间、接触事件和防护措施，返程后按暴露类型生成不同期限的自我监测提醒，
就医时按授权向医生提供**经过确认**的旅居史，并支持异常症状上报、跨区域最小授权调查
和管理侧逐条追溯。

本仓库只包含领域规则与交换契约，不依赖任何第三方包（Python ≥ 3.11）。

## 目录

- `contracts/domain.schema.json`：领域事件信封与已登记的事件/聚合类型。
- `data/sample.json`：中文联调样例。
- `src/travel_health_exposure/`
  - `contracts.py`：交换层必填字段、类型、带时区时间与版本校验，不改写输入。
  - `risks.py`：风险提示目录（风险依据、观察症状、监测期限的单一事实来源）。
  - `exposures.py`：行程、住宿区间与暴露登记簿（幂等归并）。
  - `protections.py`：途中防护措施登记（幂等）。
  - `monitoring.py`：分期限监测提醒排程、发送失败补发、用户确认。
  - `consent.py`：自愿记录范围、就医共享授权、撤回、公共卫生脱敏留存。
  - `symptoms.py`：本人/医生症状上报、规则提示、医生诊断独立挂接。
  - `oversight.py`：跨区域卫生人员调查案件、辖区与字段最小授权、审计。
  - `trace.py`：管理追溯链（提醒 → 依据 → 确认 → 处置），身份掩码。
  - `events.py`：只增事件日志，事件信封符合 `domain.schema.json`。
  - `tracker.py`：装配门面 `TravelHealthTracker`，串起完整旅程。
- `tests/test_contracts.py`：契约边界检查。
- `tests/test_domain.py`：业务不变量检查。

## 核心不变量

| 场景 | 规则 |
| --- | --- |
| 行程改签 | 同一 `stay_id` 的区间改期只更新一条暴露并抬高版本，不产生新事实 |
| 退订 | 仅把行程标记为 `cancelled`，本地行程、已登记暴露与排程的提醒保留 |
| 重复扫码 | 同一场所码重复登记归并到同一条暴露（`merged_scan` 标记命中） |
| 多人同行 | 同行人挂在同一条暴露上，不为本人制造多条 |
| 重叠登记 | 同地点同类型且日期重叠的登记归并，窗口取并集 |
| 监测期限 | 蚊媒 14 天、聚餐 7 天、花粉 14 天，自暴露结束日起算（见 `risks.py`） |
| 发送失败 | `due_date` 排程时即固定；恢复后只补发未确认提醒，已确认不重发 |
| 改签重排 | 仅重排未确认提醒；已确认的提醒冻结 |
| 就医共享 | 医生只能看到用户逐条确认的旅居史，且只含授权字段 |
| 撤回共享 | 只关闭未来查看；历史查看审计保留，已用于通知的事实保留脱敏版本 |
| 脱敏留存 | SHA-256 假名 + 去除身份字段，撤回后不可还原旅客身份 |
| 症状上报 | 本人或医生均可上报；系统只给标注"非诊断"的规则提示 |
| 诊断 | 只能由医生单独签署挂接，不写回症状上报、不覆盖规则提示 |
| 跨区域查询 | 限于案件授权辖区与字段白名单；真实身份字段默认禁止授予 |
| 调查结束 | 案件关闭即终止访问，含被拒绝查询在内的审计只增保留 |
| 管理追溯 | 提醒→风险依据（版本/来源/期限）→用户确认→症状/诊断/处置/通知，全程假名 |

## 快速使用

```python
from datetime import date
from travel_health_exposure import TravelHealthTracker, RecordScope, Reporter

class Gateway:
    def send(self, reminder): ...  # 失败时抛异常即触发"期限保留、恢复补发"

tracker = TravelHealthTracker(Gateway())
tracker.choose_recording_scope("u-1", frozenset(RecordScope))

exposure = tracker.register_exposure(
    "u-1",
    exposure_type="mosquito_borne", location_id="loc-market",
    window_start=date(2026, 10, 1), window_end=date(2026, 10, 3),
    scan_code="scan-1",
)
reminder = tracker.schedule_monitoring(exposure.exposure_id, region="CN-SH")
# reminder.due_date == 2026-10-17（暴露结束日 + 14 天）
```

## 事件目录

`ITINERARY_CONFIRMED`、`ITINERARY_CANCELLED`、`EXPOSURE_RECORDED`、
`PROTECTION_RECORDED`、`REMINDER_SCHEDULED`、`REMINDER_DELIVERED`、
`REMINDER_DELIVERY_FAILED`、`REMINDER_CONFIRMED`、`SYMPTOM_REPORTED`、
`DIAGNOSIS_ATTACHED`、`SHARE_GRANTED`、`SHARE_REVOKED`、
`PUBLIC_HEALTH_RETAINED`、`INVESTIGATION_OPENED`、`INVESTIGATION_CLOSED`。

新增事件类型时同步更新 `contracts/domain.schema.json` 与 `events.py`。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 编译检查

```bash
python3 -m compileall -q src tests
```
