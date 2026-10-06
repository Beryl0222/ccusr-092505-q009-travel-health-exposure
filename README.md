# 旅居健康暴露追踪器

本项目提供旅居健康暴露追踪器所需的领域事件交换约定与基础校验库。接入方使用统一的聚合标识、事件版本和带时区的发生时间，保证业务事实在不同环节之间可以复核。

## 目录

- `contracts/domain.schema.json`：领域事件信封和已登记类型。
- `data/sample.json`：中文联调样例。
- `src/travel_health_exposure/contracts.py`：不依赖第三方包的基础校验器。
- `src/travel_health_exposure/domain.py`：领域对象、监测期限表、审计与契约事件登记。
- `src/travel_health_exposure/exposure.py`：出发前同意范围、行程确认与暴露登记。
- `src/travel_health_exposure/reminders.py`：返程后自我监测提醒的排期、发送与补发。
- `src/travel_health_exposure/sharing.py`：就医授权共享、撤回与脱敏保留。
- `src/travel_health_exposure/symptoms.py`：异常症状上报，规则提示与诊断分离。
- `src/travel_health_exposure/access.py`：跨区域卫生人员的最小必要查询。
- `src/travel_health_exposure/trace.py`：管理者溯源视图。
- `tests/`：契约边界检查与业务规则检查。

当前核心对象包括travel_itinerary、exposure_record、health_reminder、clinical_share、symptom_report，事件类型包括ITINERARY_CONFIRMED、EXPOSURE_RECORDED、REMINDER_SCHEDULED、SYMPTOM_REPORTED、SHARE_REVOKED。校验器负责交换层必填字段、类型、时间和版本检查，具体业务流程在此约定上扩展。

## 业务规则

业务服务基于内存存储 `TrackerStore`，状态变更同时登记符合交换契约的领域事件与审计记录：

- 出发前用户选择目的地风险提示类别与自愿记录范围，超出范围的途中登记被拒绝。
- 改签保持同一行程身份，多人同行各建一条暴露，重复扫码按扫码标识幂等，三者共同保证不制造重复暴露。
- 返程后按暴露类型生成不同期限的自我监测提醒（蚊媒 14 天、聚餐食源 7 天、花粉 3 天）；发送失败保留本地期限，恢复后只补发未确认内容。
- 就医时按授权字段向医生提供经过确认的旅居史快照；撤回只影响未来查看，已用于公共卫生通知的事实保留脱敏版本。
- 异常症状可由本人或医生上报；系统规则提示与医生诊断分开保存、互不改写。
- 跨区域卫生人员只能查询职责区域内的最小必要字段；调查结束后关闭访问，审计保留。
- 管理者可从一条提醒追到风险依据、用户确认与后续处置，无关旅客仅以计数出现。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 编译检查

```bash
python3 -m compileall -q src tests
```
