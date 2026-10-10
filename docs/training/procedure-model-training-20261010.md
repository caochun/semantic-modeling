# 规程五 Typed Decisions 训练记录

日期：2026-10-10。

使用规程五驱动的 `gusu-procedure-state-v1` 数据集，在 192.168.30.134 的 RTX 4090 上按 Unsloth 官方 Decision Model 接口重新训练。训练入口使用官方 `FastDecisionModel`、`FastDecisionModel.build_dataset`、`DecisionTrainer`、`FastDecisionModel.calibrate` 和官方 adapter 保存/加载流程；仓库为包含 `noul`、`choice`、`score` 的 typed 数据增加了独立 `train_procedure.py` 入口，避免复用旧的 choice-only 校验器。

## 数据与配置

- 训练 / 校准 / 测试：8 / 4 / 4 条任务级 State。
- 每条 State 9 个决策，共 72 / 36 / 36 个 decisions。
- 官方 `build_dataset`：三组均 `skipped=0`、`truncated=0`，State 长度 1321–1339 tokens，未超过 4096。
- 基座：本地固定 revision 的 `Qwen3.5-0.8B`。
- 4-bit、LoRA r=16、LoRA 学习率 2e-4、决策头学习率 1e-4、2 epochs、batch 1、gradient accumulation 4。
- 数据状态：规程派生合成样本，来源已核对但没有运行人员/领域专家审核。

## 训练验证

- 4 个 optimizer steps，训练 loss 0.9842。
- 122 个决策头张量和 372 个 LoRA 张量均发生有限且非零更新。
- smoke test 已先完成 1 步更新和重载检查。
- 正式 adapter 在独立进程重载后，所有离散输出一致；228 个概率值最大绝对差 0.0011，低于 0.02 阈值。

## 测试结果

测试集只有 4 条 State，不能作为现场有效性结论。新鲜决策头基线 accuracy 为 0.25，训练后未校准为 0.50；校准集拟合后再次测试为 0.50。所有 `record_accuracy` 均为 0，说明没有一条 State 的全部问题同时正确。当前结果只证明训练、保存和重载链路完成，不证明模型已经可靠掌握规程判断。

## 产物

远程正式 adapter：

```text
/home/chun/semantic-decision/experiments/gusu_procedure_20261010/formal/adapter
```

本地保存了训练元数据和测试结果：[`experiments/decision_model/results/gusu-procedure-20261010`](../../experiments/decision_model/results/gusu-procedure-20261010)。当前 9120 服务仍使用原来的 `gusu_valve_v2` adapter；新模型需要经过与旧模型的同一组外部 State 评测后，再决定是否部署。

## 线上切换记录

根据部署指令，2026-10-10 已将 192.168.30.134:9120 的 `DECISION_ADAPTER` 切换为上面的规程模型，并重启用户级服务。健康检查确认 RTX 4090 已加载该 adapter，State 构建和 Decision 请求均返回 200；服务返回的模型标识为 `gusu-procedure-v1`。

同次部署替换了 `/static/` 下的前端资源。旧前端仍调用已移除的 `/v1/monitoring`，因此会收到 404；当前页面改为调用 `/v1/state/stream`，完成“原始日志 → State → Decision Model”流程。切换前的前端和 service unit 保存在远程 `backups/gusu-service-deploy-*` 目录中。

该上线只表示服务已切换到新 adapter，不改变训练数据未经专家审核、测试集只有 4 条 State、不能据此宣称现场有效性的结论。
