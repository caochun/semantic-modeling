# 实验代码

`decision_model/` 是姑苏站告警Decision Model试点，包含可追溯候选数据构建、Unsloth数据协议核对及独立训练入口。[数据与验证状态](decision_model/README.md)。

旧版 choice-only 数据使用 `decision_model/contract_check.py`；规程五的
`noul/choice/score` 数据使用 `decision_model/typed_contract_check.py`，避免旧校验器
误判 typed gold 格式。

`qa_finetuning/` 保存模型下载、训练、远程后台运行与评测代码；`qa_history/` 保存旧候选构建和一次性批次工具。当前工作重点是 QA 质量，移动这些脚本不会启动新训练或修改服务器上的既有任务。

实际运行数据、权重、适配器、检查点和日志应写入 `.semantic/qa/experiments/` 或远程独立运行目录；本目录提交实验代码与说明。

[QA 数据与审核流程](../docs/qa/README.md) · [训练实验](qa_finetuning/README.md)
## 规程驱动的语义状态决策数据

当前新增的 [procedure_state_dataset.py](decision_model/procedure_state_dataset.py) 将规程五的场景条件转换为合成事件、任务级语义状态和 Typed Decisions 数据行。第一版覆盖 10.3、10.4、10.5、11.3 四个场景；模型输入只保留自然语言 `state`，原始事件、结构化状态和条款回链放在数据集的 `audit.jsonl`。

```sh
python3 -m experiments.decision_model.procedure_state_dataset build \
  --output data/decision/gusu-procedure-state-v1
```

这批数据是验证状态构建和数据协议的规则派生种子集，尚未经过运行人员审核，不能解释为现场故障样本或训练完成证明。方法边界见 [规程驱动的语义状态 Typed Decisions 数据集](../docs/training/procedure-state-dataset.md)。

真实日志候选可用 `decision_model/teacher_gold.py` 按 Typed Decisions README 的三次教师判断方法生成软 gold。当前快照使用本助手的三次手工独立判断，来源标记为 `manual_self_teacher_consensus`，仍不等同于运行人员审核或真实故障概率。

针对规程中具有明确互斥核查分支的条款，新增 [explicit_state_dataset.py](decision_model/explicit_state_dataset.py) 和 [明确分支 State 数据集](../data/decision/gusu-explicit-state-v1/README.md)。首批覆盖 14.1、14.2、13.4、13.5、15.12.6、15.13.1、10.5，共 28 条任务级 State；原始合成事件、结构化核查结果和条款回链只保存在 `audit.jsonl`。筛选方法见 [明确决策 State 筛选与数据构建](../docs/training/procedure-decision-state-selection.md)。

v1 按 canonical case 切分，发现部分 test 分支标签在 train 中完全未出现。随后新增 [explicit_state_dataset_v2.py](decision_model/explicit_state_dataset_v2.py) 和 [平衡分支 State 数据集 v2](../data/decision/gusu-explicit-state-v2/README.md)，为每个 case 生成 5 种受控自然语言 State 变体，并保证每个分支在 train、calibration、test 都出现。训练、误差分析和“不部署”结论见 [规程五明确分支 State 训练记录](../docs/training/procedure-explicit-state-training-20261010.md)。
