# 规程五明确分支 State 数据与训练记录

日期：2026-10-10。

## 这次先解决什么问题

规程五中不是所有条款都能直接产生确定决策。本次只选择同时满足以下条件的条款：有明确触发现象，有明确核查变量，核查结果能落入互斥分支，且关键证据缺失时可以显式输出待核查。首批选择了 14.1、14.2、13.4、13.5、15.12.6、15.13.1 和 10.5，共 28 个 canonical cases、24 个分支标签。

State 只包含任务场景、已观察现象、已完成核查、缺失证据和不确定性。原始合成事件、规程条款定位和手工指定 gold 保存在 `audit.jsonl`，不会进入模型输入。数据来自规程文字推导并由程序生成，未经过运行人员或领域专家审核。

## v1 结果与切分问题

`gusu-explicit-state-v1` 有 14/7/7 条 train/calibration/test。v1 按 canonical case 切分，导致若干 test 分支标签在 train 中完全没有出现。因此它只能作为数据契约和训练链路探针，不能作为泛化评测。

在 30.134 的 RTX 4090 上用固定的 `unsloth/Qwen3.5-0.8B` revision `23c69c53358a07516b5827588b3fdb12ae78fd65`，按官方 `FastDecisionModel`、`DecisionTrainer`、4-bit LoRA 和 2 epochs 训练：训练后 test accuracy 为 1/7；训练前新初始化决策头基线为 3/7。训练和重载链路均通过，但这个结果确认了 v1 切分不可用于判断模型能力。

## v2 的修正

`gusu-explicit-state-v2` 保留同一批 28 个 canonical cases，每个 case 生成 5 种受控自然语言表达，共 140 条：

- train 56 条，calibration 28 条，test 56 条；
- 每个分支标签在三个 split 中都出现；
- test 变体使用不同的自然语言 State 表达，避免把“训练中完全未见的标签”误当成泛化测试；
- 这仍然只测试 State 文字变体鲁棒性，不测试原始日志到 State 的识别能力。

v2 同样在 30.134 的 RTX 4090 上按 2 epochs 训练。训练前 fresh-head test accuracy 为 21/56（0.375），训练后未校准为 51/56（0.9107），校准后仍为 51/56（0.9107），ECE 为 0.0673。决策头和 LoRA 参数均有有限且非零更新，训练 loss 为 0.7875；在新进程中重载后 24 个概率值比较最大差为 0，所有离散输出一致。

错误集中在 14.1 的“复归后仍存在/再次出现”与“已复归”边界（4 条），以及 13.4 的“未发现明显漏点但仍需检修分析”与“现场检查尚未完成”边界（1 条）。这表明下一轮应增加这些相邻分支的 hard-negative State，并由业务人员确认规程措辞，而不是直接部署模型。

## 产物和部署状态

本地结果：

- [v1 数据集](../../data/decision/gusu-explicit-state-v1/README.md)
- [v2 数据集](../../data/decision/gusu-explicit-state-v2/README.md)
- [v1 训练结果](../../experiments/decision_model/results/gusu-explicit-state-20261010/formal/metrics.json)
- [v2 训练结果](../../experiments/decision_model/results/gusu-explicit-state-v2-20261010/formal/metrics.json)
- [v1/v2 对比](../../experiments/decision_model/results/gusu-explicit-state-20261010/comparison-v2.json)

远程 v2 adapter 位于：

```text
/home/chun/semantic-decision/runs/gusu_explicit_state_v2_20261010/formal/adapter
```

本轮已按实验服务部署到 192.168.30.134:9120。服务健康检查确认加载上述 adapter，页面协议也切换为“选择一个规程条款场景，再发送单个 `decision` 问题”。部署前的 service 文件和旧 adapter 已备份在远程 `/home/chun/semantic-decision/backups/gusu-v2-deploy-*` 与 `gusu-v2-ui-v2-*` 目录。当前结果仍只证明在受控合成 State 变体上训练链路和分支识别能力可运行，不能宣称现场故障判断准确率。

与案例对应的 28 条合成原始日志位于 [log_samples.jsonl](../../experiments/decision_model/cases/gusu-explicit-state-v2/log_samples.jsonl)，每条记录包含页面任务 ID、可直接粘贴的 `raw_tsv` 和离线对照用 `gold_decision`。
