# 姑苏站明确分支 State 数据集 v2

本版本保留 v1 的 28 个规程派生 canonical cases，每个 case 生成 5 种受控自然语言 State 表达，共 140 条。每个分支的变体都进入 train、calibration、test，因此测试集不包含训练中完全未出现的标签。

这是用于验证 Decision Model 训练链路和 State 表达鲁棒性的合成数据，未经运行人员或领域专家审核，不能作为现场自动操作依据。原始合成事件、条款定位和 gold 来源保存在 `audit.jsonl`。
