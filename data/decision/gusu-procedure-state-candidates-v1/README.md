# 姑苏规程语义状态候选

这是一条真实日志窗口经过现象识别、规程核查和任务级状态构建后的候选快照：

```text
raw.tsv → phenomenon recognition → procedure checks → natural-language state
```

`request.json` 只有可送入 Decision Model 的 `id`、`state` 和 `questions`。
`audit.json` 保存原始日志、规范化事件、现象、证据图、规程核查结果和状态构建器输出，
用于复核。由于没有运行人员，`teacher_samples.json` 保存当前助手按 README 方法做的三次
独立判断，`teacher_gold.json` 是三次判断的频次均值，`labeled_row.json` 是带 soft gold
的 Typed Decisions 行，`teacher_gold_audit.json` 保存聚合过程和限制。

这份 gold 的来源是 `manual_self_teacher_consensus`，不是运行人员 gold，也不是现场故障概率。
`manifest.json` 仍标记 `training_ready: false`，因为目前只有一个真实窗口，且教师与被评估模型
共享同一语言模型判断来源；它适合验证格式和推理链路，不能单独形成正式训练/测试集合。

重新生成：

```sh
.venv/bin/python -m experiments.decision_model.state_builder \
  --input experiments/decision_model/cases/gusu-valve-cooling-20260911/raw.tsv \
  --snapshot-dir data/decision/gusu-procedure-state-candidates-v1

.venv/bin/python -m experiments.decision_model.teacher_gold \
  --snapshot data/decision/gusu-procedure-state-candidates-v1
```

原始告警本身不作为 state，现象识别器的候选也不作为自动金标；教师判断只对当前语义状态
和问题定义进行决策。

当前三次判断的聚合结果包括：

| 问题 | 聚合分布 |
| --- | --- |
| 10.3 候选 | `true: 1.0` |
| 10.4 / 10.5 候选 | `false: 1.0` |
| 11.3 候选 | `true: 1.0` |
| 物理故障 | `candidate: 0.667`, `insufficient: 0.333` |
| 证据是否足够 | `insufficient: 1.0` |
| 下一项核查 | `fan_power: 0.667`, `manual_review: 0.333` |
| 紧迫程度 | `2: 0.667`, `3: 0.333` |
