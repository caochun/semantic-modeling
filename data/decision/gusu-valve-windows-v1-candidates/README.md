# 姑苏阀冷事件窗口候选集

**状态：待业务复核，当前不满足训练启动条件。** 目标是先核对时序、语义及标签，不是把规则候选冒充现场金标。

24个窗口，4855条去重后的范围内事件；覆盖7个事件日。窗口及前置上下文可重叠，不能视作完全独立案例。

仅覆盖姑苏极2低端VSC1的P221VCC1/P221VCC2，两个主机、A/B通道都保留。窗外其他设备未纳入；完整性只针对本地导出的所选范围，不证明采集无缺失。

## 逐窗复核

| 窗口 | 建议集合 | 窗内记录/前置上下文 | 窗内判断 | 末端判断 | 已见异常信号复归 |
| --- | --- | --- | --- | --- | --- |
| [gusu-valve-20220528-1400](review/gusu-valve-20220528-1400.md) | train | 171/10 | 有异常证据 | 信息不足 | 已见目标信号均复归 |
| [gusu-valve-20220528-1430](review/gusu-valve-20220528-1430.md) | train | 192/26 | 信息不足 | 信息不足 | 未观察到目标信号出现 |
| [gusu-valve-20220528-1700](review/gusu-valve-20220528-1700.md) | train | 98/1 | 信息不足 | 信息不足 | 未观察到目标信号出现 |
| [gusu-valve-20220603-1630](review/gusu-valve-20220603-1630.md) | calibration | 222/20 | 有异常证据 | 信息不足 | 已见目标信号均复归 |
| [gusu-valve-20220604-1030](review/gusu-valve-20220604-1030.md) | train | 164/46 | 有异常证据 | 有异常证据 | 部分未见复归 |
| [gusu-valve-20220604-1230](review/gusu-valve-20220604-1230.md) | train | 20/0 | 信息不足 | 信息不足 | 未观察到目标信号出现 |
| [gusu-valve-20220604-1430](review/gusu-valve-20220604-1430.md) | train | 85/50 | 有异常证据 | 有异常证据 | 部分未见复归 |
| [gusu-valve-20220604-2200](review/gusu-valve-20220604-2200.md) | train | 28/0 | 信息不足 | 信息不足 | 未观察到目标信号出现 |
| [gusu-valve-20220605-0300](review/gusu-valve-20220605-0300.md) | calibration | 16/0 | 信息不足 | 信息不足 | 未观察到目标信号出现 |
| [gusu-valve-20220605-0830](review/gusu-valve-20220605-0830.md) | calibration | 122/0 | 有异常证据 | 信息不足 | 已见目标信号均复归 |
| [gusu-valve-20220605-1625](review/gusu-valve-20220605-1625.md) | calibration | 176/29 | 有异常证据 | 有异常证据 | 部分未见复归 |
| [gusu-valve-20220606-0900](review/gusu-valve-20220606-0900.md) | train | 140/0 | 信息不足 | 信息不足 | 未观察到目标信号出现 |
| [gusu-valve-20220606-1000](review/gusu-valve-20220606-1000.md) | train | 190/30 | 信息不足 | 信息不足 | 未观察到目标信号出现 |
| [gusu-valve-20220606-1100](review/gusu-valve-20220606-1100.md) | train | 26/0 | 有异常证据 | 信息不足 | 已见目标信号均复归 |
| [gusu-valve-20220606-1525](review/gusu-valve-20220606-1525.md) | train | 246/0 | 信息不足 | 信息不足 | 未观察到目标信号出现 |
| [gusu-valve-20220606-1630](review/gusu-valve-20220606-1630.md) | train | 62/0 | 信息不足 | 信息不足 | 未观察到目标信号出现 |
| [gusu-valve-20220606-1745](review/gusu-valve-20220606-1745.md) | train | 84/32 | 信息不足 | 信息不足 | 未观察到目标信号出现 |
| [gusu-valve-20220608-0900](review/gusu-valve-20220608-0900.md) | test | 446/24 | 有异常证据 | 有异常证据 | 部分未见复归 |
| [gusu-valve-20220608-1100](review/gusu-valve-20220608-1100.md) | test | 118/107 | 有异常证据 | 有异常证据 | 均未见复归 |
| [gusu-valve-20220608-1400](review/gusu-valve-20220608-1400.md) | test | 149/0 | 有异常证据 | 有异常证据 | 部分未见复归 |
| [gusu-valve-20220608-1500](review/gusu-valve-20220608-1500.md) | test | 749/86 | 有异常证据 | 有异常证据 | 部分未见复归 |
| [gusu-valve-20220608-1915](review/gusu-valve-20220608-1915.md) | test | 64/0 | 有异常证据 | 有异常证据 | 均未见复归 |
| [gusu-valve-20220612-1500](review/gusu-valve-20220612-1500.md) | test | 704/90 | 有异常证据 | 有异常证据 | 部分未见复归 |
| [gusu-valve-20220612-1700](review/gusu-valve-20220612-1700.md) | test | 42/16 | 信息不足 | 信息不足 | 未观察到目标信号出现 |

## 候选标签分布

```json
{
  "during_window": {
    "abnormal_evidence": 13,
    "insufficient_evidence": 11
  },
  "at_window_end": {
    "insufficient_evidence": 15,
    "abnormal_evidence": 9
  },
  "observed_recovery": {
    "all_observed_cleared": 4,
    "not_observed": 11,
    "some_unresolved": 7,
    "all_unresolved": 2
  }
}
```

verified_expected目前没有可靠候选：缺少与低端事件直接对应的完整操作/运行确认。不得用“没有故障词”生成正常标签。

## 分组和复现

按实际事件日期分组，整日的两个主机、A/B通道、相邻窗口及选项变体均同组。train/calibration/test只是冻结的建议划分；规则跨集合重用，不宣称未见规则泛化。

用户2026-09-11案例留在原独立评测目录，本数据集不读取它的日志或标签。

`candidate_rows.jsonl`符合Unsloth字段格式，其中gold仅为候选；没有生成可直接接入现有训练入口的train/calibration/test文件。

模型输入采用可逆的字典编码：设备和原文消息只写一次，逐条事件保留相对时间、设备/消息索引和原始等级；原始记录仍在events.jsonl和逐窗Markdown中。编码逐字段反解核对，没有删除事件或生成摘要。

```sh
python -m experiments.decision_model.valve_windows build --output /新的空目录
python -m experiments.decision_model.valve_windows verify --check-sources
```
