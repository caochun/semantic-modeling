# 阀冷日志现象识别原型

这份原型实现规程五场景下的前三步：把原始告警转换成可比较的信号，按时间窗口形成状态事实，再将事实映射到规程中的现象候选和证据。现在增加了第四步：根据已识别现象和缺失证据生成规程核查建议。它服务于后续的规则遍历或 Decision Model，不直接输出“设备已经损坏”、全系统正常或自动操作指令。

## 为什么先识别现象

规程五的主体是“现象 → 检查条件 → 处置分支”。日志通常只记录一个信号边沿，例如“报警出现/消失”，而内容可能描述另一个运行观测，例如“风机运行消失”或“泄漏保护短时被屏蔽”。两者含义不同：

```text
alarm_edge = 事件记录中的边沿（出现 / 消失）
observation = 内容表达的设备或保护状态（not_running / masked / ...）
```

因此，报警记录的“消失”不会被解释成物理设备已经恢复；“泄漏保护被屏蔽”也不会被解释成已经发生泄漏。只有先保留这种区别，后续才能安全地进入规程的检查和处置分支。

## 三步流程

### 1. 信号标准化

`phenomenon.py` 接收 TSV、JSON 或 JSONL。每一条输入都保留在 `events[*].raw`，并生成稳定的字段：

- 时间、站点、主机、A/B 通道、设备和原文；
- `alarm_edge`：从记录尾部解析出的“出现/消失”；
- `signal_id`：规范信号名称；
- `observation`：内容表达的状态；
- `component`：例如 `E4.M01`；
- `mapping_status`：`exact`、`procedure_phrase` 或 `unknown`。

当前基线识别的信号包括风机运行反馈、泄漏保护屏蔽、泄漏/液位、主循环泵和备用泵状态。无法识别的文本进入 `unknown_event_ids`，不会被强行归类。

### 2. 时间窗口和状态事实

按事件时间排序形成一个窗口，并保留窗口起止时间、原始事件数量、设备范围、通道范围和数据完整性说明。风机组按规程三的两组配置分别追踪：

```text
M01 / M03 / M05
M02 / M04 / M06
```

主机、通道、设备和风机组是状态键的一部分。A 通道的一条记录不会清除 B 通道的状态，也不会把同一主机下的不同设备合并。若某组所有成员都出现运行反馈丢失，状态为 `supported`；只看到部分成员时为 `partial`，并列出缺少的成员。

### 3. 规程现象识别和证据输出

事实会映射为现象候选。每个候选包括 `status`、支持它的原始事件 ID、缺失证据、规程引用和解释。当前映射覆盖：

| 现象候选 | 识别条件 | 当前含义 |
| --- | --- | --- |
| `fan_running_feedback_loss` | 内容明确写出风机运行消失 | 日志支持该运行观测，不等同于风机故障 |
| `leak_protection_masked` | 屏蔽条件出现/消失成对或单独出现 | 支持保护屏蔽状态；仍需独立泄漏或液位证据 |
| `inner_cooling_water_leak` | 泄漏报警、液位低或实际漏水文本 | 无这些证据时为 `insufficient_evidence`，有日志证据时仅为 `suspected` |
| `inner_cooling_main_pump_branch` | 主泵故障和备用泵状态文本 | 为规程五 10.4/10.5 提供分支输入 |
| `outer_cooling_fan_group_loss:*` | 同一设备/通道内一组成员全部或部分丢失 | 支持/部分支持阀冷风机组现象 |

每条现象都可以沿 `evidence_graph.edges` 回到原始事件。没有支持事件的候选仍会保留，并通过 `missing` 说明下一步需要补充什么；这避免把“日志里没有出现”误读为“现场没有发生”。

### 4. 规程核查推理

`procedure_reasoning.py` 使用版本化规则表 [procedure_rules_v1.json](../../experiments/decision_model/procedure_rules_v1.json)，覆盖规程五 10.3、10.4、10.5、11.3。每条规则有触发现象、适用条件、核查项、优先级、可接受的信号和原文引用。

核查项只有两种状态：

- `observed`：输入中已经出现了相应的离散证据；
- `missing`：当前窗口没有该证据，需要补充日志、模拟量趋势、控制命令或现场检查。

只有 `supported`、`partial` 或 `suspected` 的现象会触发规则。单独的 `insufficient_evidence` 候选不会触发处置分支，避免“没有看到泄漏证据”反过来激活泄漏处置。

运行整条链路：

```sh
python3 -m experiments.decision_model.procedure_reasoning \
  --input experiments/decision_model/cases/gusu-valve-cooling-20260911/raw.tsv \
  --output /tmp/gusu-procedure.json
```

输出在原有结果上增加 `procedure_reasoning`：

```json
{
  "summary": {
    "disposition": "not_ready_for_disposition",
    "missing_check_count": 7
  },
  "next_checks": [
    {
      "id": "fan_control_context",
      "status": "missing",
      "priority": "high",
      "title": "核对风机控制模式和启停/轮换命令"
    }
  ]
}
```

`not_ready_for_disposition` 表示证据仍不足以进入规程处置分支；它不是“系统正常”，也不是“系统故障”。

## 输入和输出

命令：

```sh
python3 -m experiments.decision_model.phenomenon \
  --input experiments/decision_model/cases/gusu-valve-cooling-20260911/raw.tsv \
  --output /tmp/gusu-phenomenon.json
```

顶层输出为 `phenomenon-recognition.v1`，主要字段如下：

```json
{
  "state": {
    "window": {"start": "...", "end": "...", "event_count": 20},
    "observed_signals": [],
    "fan_groups": [],
    "data_completeness": {}
  },
  "events": [],
  "phenomena": [
    {
      "id": "leak_protection_masked",
      "status": "supported",
      "evidence_event_ids": [],
      "missing": []
    }
  ],
  "evidence_graph": {"nodes": [], "edges": []},
  "physical_fault_confirmed": false
}
```

`physical_fault_confirmed` 在这个阶段固定为 `false`。这是边界声明：现象识别器可以确认“日志表达了什么”，不能仅凭告警文本确认现场物理故障。

## 用户 20 条日志的基线结果

对 `gusu-valve-cooling-20260911/raw.tsv` 的结果为：

- 20 条事件全部完成标准化；
- 4 个主机/通道/设备组合均识别出完整风机组运行反馈丢失；
- 识别出 4 组“泄漏保护短时被屏蔽”出现，并在窗口内记录对应消失；
- `inner_cooling_water_leak` 为 `insufficient_evidence`；缺少泄漏报警、液位/趋势和现场检查结果；
- 不确认物理故障，也不确认系统正常。
- 触发规程五 11.3 和 10.3 两组核查规则，生成 7 项待补充核查；优先核对风机控制命令、变频器/电源/冗余能力、泄漏报警/液位和水冷趋势。

适合呈现给值班人员的结论是：**观察到风机运行反馈退出和泄漏保护短时屏蔽；现有窗口不足以确认真实泄漏、风机故障或系统恢复，需要补充控制命令、完整风机状态、相关故障/冗余能力告警以及水冷参数趋势。**

## 与后续决策模型的边界

该模块的输出是可追溯的状态和证据输入。后续若要使用 Decision Model，应把“问题”和“候选动作/下一项检查”定义清楚，再将本模块生成的状态送入模型。例如：

```text
问题：当前窗口是否有足够证据确认阀冷异常？下一项应核查什么？
state：标准化信号 + 风机组状态 + 缺失证据 + 适用规程片段
候选：确认异常 / 已核实符合预期 / 信息不足 / 核查控制命令
```

不能把当前 `phenomena` 列表直接当成训练标签。历史调试日志需要结合操作、试验和现场记录审核；规程文本可以定义关系和缺口，不能单独制造“正常/异常”的事实标签。

## 任务级 state 构建器

`experiments/decision_model/state_builder.py` 将本模块的识别结果和规程核查结果进一步压缩成
自然语言任务级 `state`。它保留观察事实、证据缺口、不确定性和人工审核状态，不写入规程编号、
gold 标签或自动操作命令。完整事件和证据图继续放在 audit 中：

```sh
.venv/bin/python -m experiments.decision_model.state_builder \
  --input experiments/decision_model/cases/gusu-valve-cooling-20260911/raw.tsv \
  --snapshot-dir data/decision/gusu-procedure-state-candidates-v1
```

该命令已对用户提供的 20 条阀冷日志生成一条候选请求。候选请求可用于检查推理接口和问题
定义，但 `manifest.json` 标记 `training_ready: false`；完成运行人员复核前，不能把它加入
训练、校准或测试集合。
