# Unsloth Decision Model：数据与训练代码核对

核对日期：2026-10-09。原版数据构造、GPU训练、校准、评估及FP32保存重载已在RTX 4090和TITAN V完成实测。具体结果与局限见[GPU试点记录](decision-model-gpu-pilot-20261009.md)。

## 核对依据

- [官方训练说明](https://unsloth.ai/docs/basics/train-your-own-decision-model-with-unsloth)
- [Decision API及问题类型](https://unsloth.ai/docs/models/decision-laya)
- [typed-decisions数据说明](https://huggingface.co/datasets/LocalLLaMA/typed-decisions)
- 固定Unsloth commit：[`cc9128134156cc51f94eeb76d3d19f80bddbc8a7`](https://github.com/unslothai/unsloth/tree/cc9128134156cc51f94eeb76d3d19f80bddbc8a7)，具体文件及SHA256见 [锁文件](../../experiments/decision_model/upstream.lock.json)。

阅读范围覆盖 `decision.py` 的数据构造、collator、forward、loss、Trainer、LoRA、划分、评估、校准与保存相关实现，`_decision_common.py` 的标签和校准函数，`decision_from_lm.py` 的模型转换，以及Clef原版编码函数。并非声称审计了所有模型、硬件和MLX实现。

## 数据格式不是普通SFT messages

每条记录的核心是 `state`、`questions`、`gold`。`criteria`位于 **`questions[问题名]`内部**，不是第四个顶层字段。`questions`与`gold`既可为字典，也可为JSON字符串。每个问题必须在gold中有对应项，否则 `build_dataset` 会跳过该项。

| 类型 | criteria | gold支持形式 | 本项目用法 |
| --- | --- | --- | --- |
| `noul` | 可省略，或`true`/`false`描述 | 布尔/标签，或概率 | 不用于把信息不足硬塞成否或0.5 |
| `choice` | 选项键到描述的映射 | `{"label":"选项键"}`，或`{"probabilities":{...}}` | 首批闭锁三分类及下一步核查选择 |
| `score` | 有序等级描述列表 | 整数等级或等级分布 | 本批不用，缺少已核定的统一严重度标尺 |

`_target_for`将合法硬标签转成one-hot target，概率字典则归一化。首批没有可靠的人类频率/软标签，因此使用明确的候选硬标签，不编造0.8、0.9这样的概率。官方typed-decisions的软gold来自教师模型多次采样，也不等同于现实事件发生频率。

## 模型与loss

普通LLM经 `FastDecisionModel.from_pretrained` 加入新的Clef joint schema head。输入拼接state、各问题和选项；backbone产生hidden states，head按问题/选项的token span输出候选分数。所有问题可以在一次前向中处理，不生成解释文本或逐token答案。

`DecisionTrainer`使用决策选项上的交叉熵；所核对commit的 `CLEF_RECIPE` 为空，默认label smoothing、Brier和ordinal权重均为0。代码支持显式启用这些项，不能因为实现了Brier项就说默认训练已经用它。backbone LoRA与head分别有学习率，head默认1e-4；官方推荐backbone 2e-4、r=16是起点，不是姑苏数据已验证的最优参数。

从普通LLM开始时head是新初始化的；训练前准确率只能表示“该初始化决策系统”的表现，不能与原LLM自然语言问答准确率混称。训练后的head和LoRA必须一同保存，单独保存旧QA adapter不够。

保存精度也影响决策：官方`model.save_pretrained`会将head非标量参数转为BF16；一次GPU试验中，重载最大概率偏差仅0.0007，却让0.2602与0.2599两个近似并列的候选发生排序变化。训练入口因此改为官方`DecisionTrainer.save_model`的exact路径，保留FP32 head，继续要求全部离散预测一致。校准配置仍随head与adapter保存；这没有修改官方模型或loss。

## 必须适配的行为

1. **分组拆分。** 官方 `split_holdout` 按原始row成组，能保留同row的多个问题，但不知道跨row的规则族/同一事故。我们先按family拆分，再分别 `build_dataset`。
2. **选项排序。** Clef `question_options` 对choice的键排序；`noul`的Clef顺序是true/false，不能套用另一条实现的索引顺序。标签必须随编码后的option IDs校验。
3. **长度截断。** `encode_record`优先保留schema，超长时裁掉state尾部。这可能恰好裁掉恢复状态或关键前提。本试点比较完整与限定长度编码，超长直接失败，不允许静默丢证据。
4. **无效样本会被跳过。** 必须检查report中的`total`、`skipped`、`truncated`和有效row数，不能只看到训练启动就认为全部数据都用上了。
5. **校准不是标签概率。** Clef校准对硬正确标签拟合温度，有最少决策数门槛和内部交叉拟合。它需要与梯度训练分开的数据；最终测试也须另留。我们的7个校准场景仅够验证接口，不足以声称现场概率可靠。
6. **confidence和选项概率有区别。** 业务阈值应解释对应选项的`probabilities`，不直接搬用其他API的confidence阈值。模型概率也不是故障发生率。
7. **Mac不等于不支持。** 当前上游明确有MLX分支；本批只验证CPU数据处理，准备的训练入口针对CUDA，未验证MLX模型训练。

## 本次实际验证

固定基座/tokenizer：`unsloth/Qwen3.5-0.8B`，revision `23c69c53358a07516b5827588b3fdb12ae78fd65`。不下载模型权重，只加载真实tokenizer。

在隔离CPU检查中按哈希加载固定源码里的数据函数与数据类，实际执行原版 `FastDecisionModel.build_dataset`、`_clef_items`、`_target_for`、`encode_record`，没有重新仿写这些函数来冒充兼容性验证。为避免GPU初始化，只提取数据相关定义，不导入完整Unsloth运行时。

| 集合 | 记录数 | 决策数 | 跳过 | 截断 | 最长token |
| --- | ---: | ---: | ---: | ---: | ---: |
| train | 46 | 74 | 0 | 0 | 496 |
| calibration | 14 | 22 | 0 | 0 | 487 |
| test | 26 | 38 | 0 | 0 | 503 |

86条记录的选项排序与gold映射全部吻合。另有11项本地测试验证快照篡改、跨集合规则泄漏、错误标签映射、漏标签、元数据混入和重载预测比较等情况。详情见 [上游验证记录](../../data/decision/gusu-alarm-pilot-v1/upstream_validation.json)。

GPU更新检查在4090和TITAN V上均通过：head与LoRA梯度均有限且非零，两组参数均实际改变。默认BF16 head导出的重载检查曾失败，改用FP32保存后，两台的一步模型在新进程中均得到一致的离散输出和返回概率。

两台固定两轮试点均完成24次更新、独立校准、测试及正式模型重载。4090测试为15/19个决策正确，TITAN V为16/19；但极小校准集使两台的测试交叉熵都恶化，不能声称输出概率已可靠校准；这也不是现场业务有效性验证。

服务器环境和排队/实测进展单独记录在 [GPU试点记录](decision-model-gpu-pilot-20261009.md)。

## 与当前项目的关系

本试点学习的是“给定观测＋适用规则→有限候选决策”。它可以成为未来任务级语义模型的下游决策模块，或与可执行规则基线做对照；它本身不替代概念发现、证据检索、图更新及模型验证，也不证明完整语义建模过程已学会。

第一批刻意保留规则上下文，便于验证模型是否使用条件、识别缺失观测。后续若研究闭卷固化，必须另建问题与切分目标，不能用这一批的依证据成绩来宣称知识已写入权重。
