# 确定性日志试探结果（2026-10-10）

这些日志是依据规程五构造的合成输入，不是真实运行记录，也不是专家金标。

## State Builder 输出

### `all_recovered`

State：在2026-09-11T12:00:00至12:00:09窗口内，姑苏站P221VCC2主机A系统极2低端阀1许继阀冷A2设备的E4.M01、E4.M03、E4.M05三台风机运行信号（级别3）先后出现，约8秒后按原顺序依次消失，三组出现-消失边沿对完整。信号消失仅表明报警边沿消失，不代表设备恢复或风机实际状态，其短时出现后复归的原因不明。缺少阀冷系统其他设备、B通道及独立测量数据，无法进一步判定。该状态只描述日志证据，不等同于物理故障确认或操作指令。

当前线上三项问题输出：
- `during_window` → `insufficient_evidence`（0.6703）
- `at_window_end` → `insufficient_evidence`（0.6615）
- `observed_recovery` → `some_unresolved`（0.3220）

### `partially_recovered`

State：姑苏站P221VCC2主机A系统，极2低端阀1许继阀冷A2的E4.M01、M03、M05风机运行报警（级别3）于12:05:00-12:05:01集中出现；M01与M03报警约8秒后消失，仅表示报警边沿消失，不构成设备状态恢复或故障结论；M05报警至窗口结束未见消失记录。窗口前初始状态、连续趋势及通道冗余证据缺失，异常原因无法判定。该状态只描述日志证据，不等同于物理故障确认或操作指令。

当前线上三项问题输出：
- `during_window` → `insufficient_evidence`（0.6785）
- `at_window_end` → `insufficient_evidence`（0.6367）
- `observed_recovery` → `some_unresolved`（0.3286）

### `unresolved_pump_and_backup`

State：姑苏站P221VCC2主机A通道极2低端阀1许继阀冷A2于2026-09-11 12:10:00.000出现内水冷主循环泵故障报警（级别3），0.3秒后出现备用循环泵未投入报警（级别3）。窗口内仅有报警出现边沿，无复归边沿，报警信号在窗口结束时仍为出现状态，但报警存在不等于设备实际故障确认。缺少B通道信号、泵运行工况数据及初始全量状态，无法判断报警后续变化与设备真实状态。该状态只描述日志证据，不等同于物理故障确认或操作指令。

当前线上三项问题输出：
- `during_window` → `insufficient_evidence`（0.6878）
- `at_window_end` → `insufficient_evidence`（0.6396）
- `observed_recovery` → `some_unresolved`（0.3331）

### `confirmed_leak_evidence`

State：姑苏站P221VCC2主机，极2低端阀1许继阀冷A2（系统A）于12:15:00.000出现24小时泄漏报警，12:15:00.200出现膨胀罐液位低报警，均为级别3报警出现边沿；12:15:30.000记录现场检查发现阀厅实际漏水。三条记录仅含报警出现边沿，窗口内无任何报警消失边沿，信号复归情况未知；无系统B通道记录、无初始全量状态、无液位连续趋势，漏水与阀冷泄漏的物理关联尚不能确认。该状态只描述日志证据，不等同于物理故障确认或操作指令。

当前线上三项问题输出：
- `during_window` → `insufficient_evidence`（0.6748）
- `at_window_end` → `insufficient_evidence`（0.6354）
- `observed_recovery` → `some_unresolved`（0.3217）

## 关键发现

- 四组日志都被 State Builder 识别出了有意义的差异：全部复归、部分复归、主泵/备用泵异常、泄漏与现场漏水。
- 当前线上三个问题对四组输入都高度偏向 `insufficient_evidence`，`observed_recovery` 即使在三台风机均出现后均消失时仍输出 `some_unresolved`。
- 使用模型训练时的八个问题协议（去掉服务限制的第九个 `urgency`）再次测试，模型仍大量输出固定先验：场景问题几乎全部为 `false`，`evidence_sufficiency` 多为 `insufficient`，`next_check` 多为 `pump_backup`。
- 因此当前主要问题不是日志证据不够明确，而是 Decision Model 尚未学会根据 State 区分分支；同时线上 UI 问题协议与训练数据中的问题协议不一致。

## 建议

先统一线上问题协议与训练数据协议，再扩充平衡的训练/验证集并重新训练。上述日志应保留为回归测试，不应直接当作新的 gold。
