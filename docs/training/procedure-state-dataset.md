# 规程驱动的语义状态 Typed Decisions 数据集

当前原型实现了从规程五到 Typed Decisions 数据行的第一段闭环，范围限定为 10.3、10.4、10.5 和 11.3 四个已经有核查规则的场景。

```text
规程条款
  → 场景因素
  → 合成事件流
  → 状态构建器
  → 自然语言 state
  → 固定 Typed Questions
  → 规程 oracle gold
```

生成入口：

```sh
python3 -m experiments.decision_model.procedure_state_dataset build \
  --output data/decision/gusu-procedure-state-v1

python3 -m experiments.decision_model.procedure_state_dataset verify \
  --output data/decision/gusu-procedure-state-v1
```

生成器校验通过后，还要用锁定的 Unsloth 源码检查 `noul`、`choice` 和
`score` 的问题规范化、gold 概率以及选项顺序。这个检查不会加载模型权重：

```sh
# 不依赖训练环境，检查数据契约和上游标签映射
python3 -m experiments.decision_model.typed_contract_check \
  --data data/decision/gusu-procedure-state-v1 --skip-tokenizer

# 安装 transformers 后，再增加真实 tokenizer 和 Clef 编码检查
python3 -m experiments.decision_model.typed_contract_check \
  --data data/decision/gusu-procedure-state-v1 \
  --report data/decision/gusu-procedure-state-v1/upstream_validation.json
```

如果当前环境没有 `transformers`，第二条命令仍会完成纯协议检查，并在报告中把
`tokenizer_contract` 标为 `skipped_missing_dependency`。这时还没有验证 token 长度、
上游 `build_dataset` 或训练可执行性；安装依赖后应重新运行该命令。

## 输入和输出边界

每条模型数据只包含：

```json
{
  "id": "10.3-mask-only",
  "state": "自然语言任务级状态",
  "questions": "固定问题定义的 JSON 字符串",
  "gold": "问题答案分布的 JSON 字符串"
}
```

原始事件、结构化状态、场景因素、规程条款、来源哈希和标签来源保存在 `audit.jsonl`，不进入模型输入。这样可以分别评估状态构建器和决策模型，也避免把条款编号或目标标签直接泄漏给模型。

当前状态构建器是一个确定性的原型。它把合成事件整理为：时间范围、观察事实、状态转换、候选场景、缺失证据和不确定性，然后用模板生成自然语言 `state`。以后可以在同一接口下替换为 CEP、规则引擎或受约束的大模型，但必须保留状态构建器版本和证据来源。

## Typed Questions

当前固定问题包括：

- 10.3、10.4、10.5、11.3 场景是否适用（分别为 `noul`）；
- 物理故障是否确认（`choice`）；
- 证据是否足以进入人工规程审核（`choice`）；
- 是否需要运行人员复核（`noul`）；
- 当前优先核查类别（`choice`）；
- 核查紧迫程度（`score`）。

场景使用独立的真假问题，因为一个日志窗口可能同时触发多个规程候选。例如主泵异常且备用泵未投入时，10.4 和 10.5 可以同时适用。

## Gold 的含义

当前种子集的 gold 是从场景因素和规程条件确定性推导的一位分布：

```json
{"label": "candidate", "probabilities": {
  "confirmed": 0.0,
  "candidate": 1.0,
  "insufficient": 0.0
}}
```

这不是专家标注，也不是教师模型多次采样得到的现实概率。真实日志接入后，需要由运行人员复核；多个独立复核结果才可以形成软分布。没有可靠复核时，应保留硬标签并明确标记来源。

## 当前数据范围

| 集合 | 案例数 | 决策数 |
| --- | ---: | ---: |
| train | 8 | 72 |
| calibration | 4 | 36 |
| test | 4 | 36 |
| 合计 | 16 | 144 |

这是验证数据契约和状态构建链路的种子集。它不是现场故障率样本，不能用于宣称模型已经具有换流站运行资质。当前测试集是不同因素组合的留出，尚未实现按未见规程场景泛化的评估。

## 后续扩展

1. 用真实日志窗口替换或补充合成事件，并由状态构建器统一处理；
2. 为第 10、11 章剩余场景建立场景因素和交叉引用解析；
3. 增加复归、缺失、乱序、矛盾信号和多场景重叠样本；
4. 组织多名运行人员复核真实案例，形成软 gold；
5. 使用事件日、设备、通道和案例族分组切分，避免同一事故窗口泄漏；
6. 分别评估“日志到 state”“state 到 decision”和端到端链路。

当前检查结果：纯协议检查和隔离环境中的真实 tokenizer、上游 `build_dataset` 检查均已通过。
项目 `.venv` 仍未安装 `transformers`；若在本地训练环境复核，应按上面的隔离命令安装对应依赖。

现在已经用一条真实姑苏阀冷日志窗口跑通同一条状态构建链路。候选快照位于
[`gusu-procedure-state-candidates-v1`](../../data/decision/gusu-procedure-state-candidates-v1/README.md)：
其中 `request.json` 可用于推理试探，`audit.json` 保存原始事件和证据图，但由于还没有运行
人员 gold，明确不能直接作为训练数据。

在没有运行人员的情况下，已按 Typed Decisions README 的三次教师采样方法补充了一个手工
教师 gold：当前助手对同一 state 做三次独立判断，按三次结果的频次取均值。结果保存在
候选目录的 `teacher_samples.json`、`teacher_gold.json` 和 `labeled_row.json`，来源标记为
`manual_self_teacher_consensus`。这能验证 soft gold 的格式和聚合链路，不能替代现场审核，
也不表示真实故障概率。
