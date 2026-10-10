# 姑苏阀冷 v2：证据识别与复归追踪

冻结的弱监督试验集。无业务专家金标；不用于宣称正常/异常现场准确率。

按同日历史携带未复归异常、屏蔽起点和最近风机/模式状态。原始窗口内全部事件恰好保留一次；A/B不相互抵消。跨日状态未知。

train/calibration/test 沿用 v1 事件日隔离；calibration 保留作验证，不拟合置信度。测试选项轮换不增加独立样本数。

events.jsonl 保留所有来源行；windows.jsonl 以 carry_ids/event_ids 指向证据。context_search.json 记录操作背景检索。

| 子窗口 | 分组 | 核心事件 | 历史记录 | 最大token | 窗口内 / 末端 / 复归候选 |
| --- | --- | --- | --- | --- | --- |
| gusu-valve-20220528-1400/P221VCC1/000 | train | 118 | 0 | 3691 | abnormal_evidence / insufficient_evidence / all_observed_cleared |
| gusu-valve-20220528-1400/P221VCC1/001 | train | 26 | 8 | 2055 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220528-1400/P221VCC2/000 | train | 27 | 0 | 1978 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220528-1430/P221VCC1/000 | train | 109 | 10 | 3839 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220528-1430/P221VCC1/001 | train | 83 | 10 | 3198 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220528-1700/P221VCC1/000 | train | 98 | 12 | 3755 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220603-1630/P221VCC1/000 | calibration | 94 | 8 | 3390 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220603-1630/P221VCC1/001 | calibration | 128 | 8 | 3798 | abnormal_evidence / insufficient_evidence / all_observed_cleared |
| gusu-valve-20220604-1030/P221VCC2/000 | train | 129 | 2 | 4043 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220604-1030/P221VCC2/001 | train | 35 | 17 | 2426 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220604-1230/P221VCC1/000 | train | 12 | 17 | 2014 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220604-1230/P221VCC2/000 | train | 8 | 8 | 1674 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220604-1430/P221VCC2/000 | train | 85 | 18 | 3479 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220604-2200/P221VCC1/000 | train | 16 | 25 | 2320 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220604-2200/P221VCC2/000 | train | 12 | 26 | 2215 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220605-0300/P221VCC2/000 | calibration | 16 | 0 | 1704 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220605-0830/P221VCC1/000 | calibration | 70 | 0 | 2737 | abnormal_evidence / insufficient_evidence / all_observed_cleared |
| gusu-valve-20220605-0830/P221VCC2/000 | calibration | 52 | 2 | 2517 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220605-1625/P221VCC1/000 | calibration | 24 | 12 | 2148 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220605-1625/P221VCC2/000 | calibration | 102 | 14 | 3675 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220605-1625/P221VCC2/001 | calibration | 50 | 32 | 2974 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220606-0900/P221VCC1/000 | train | 4 | 0 | 1436 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220606-0900/P221VCC2/000 | train | 122 | 0 | 3932 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220606-0900/P221VCC2/001 | train | 14 | 16 | 1974 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220606-1000/P221VCC1/000 | train | 94 | 0 | 3260 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220606-1000/P221VCC2/000 | train | 96 | 16 | 3527 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220606-1100/P221VCC2/000 | train | 26 | 16 | 2184 | abnormal_evidence / insufficient_evidence / all_observed_cleared |
| gusu-valve-20220606-1525/P221VCC1/000 | train | 92 | 14 | 3484 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220606-1525/P221VCC2/000 | train | 106 | 26 | 4071 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220606-1525/P221VCC2/001 | train | 48 | 26 | 2812 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220606-1630/P221VCC1/000 | train | 62 | 24 | 3166 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220606-1745/P221VCC1/000 | train | 62 | 24 | 3094 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220606-1745/P221VCC2/000 | train | 22 | 26 | 2378 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-0900/P221VCC1/000 | test | 50 | 4 | 2503 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-0900/P221VCC2/000 | test | 120 | 0 | 4076 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-0900/P221VCC2/001 | test | 54 | 57 | 3754 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-0900/P221VCC2/002 | test | 98 | 6 | 3720 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-0900/P221VCC2/003 | test | 55 | 66 | 4072 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-0900/P221VCC2/004 | test | 69 | 13 | 3150 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1100/P221VCC1/000 | test | 46 | 4 | 2422 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-1100/P221VCC2/000 | test | 72 | 13 | 3245 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-1400/P221VCC1/000 | test | 20 | 14 | 2114 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-1400/P221VCC2/000 | test | 74 | 27 | 4058 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1400/P221VCC2/001 | test | 55 | 51 | 3996 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC1/000 | test | 44 | 59 | 4057 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC1/001 | test | 44 | 58 | 4075 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-1500/P221VCC1/002 | test | 42 | 58 | 4071 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC1/003 | test | 75 | 27 | 4048 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC1/004 | test | 37 | 52 | 3572 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-1500/P221VCC1/005 | test | 36 | 63 | 4019 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-1500/P221VCC1/006 | test | 30 | 64 | 4000 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC1/007 | test | 50 | 45 | 3980 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC1/008 | test | 87 | 31 | 4068 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC1/009 | test | 42 | 35 | 3027 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-1500/P221VCC2/000 | test | 70 | 33 | 3917 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC2/001 | test | 55 | 49 | 3973 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC2/002 | test | 46 | 52 | 3963 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC2/003 | test | 68 | 39 | 4024 | abnormal_evidence / abnormal_evidence / some_unresolved |
| gusu-valve-20220608-1500/P221VCC2/004 | test | 23 | 40 | 2875 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-1915/P221VCC1/000 | test | 32 | 25 | 2643 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220608-1915/P221VCC2/000 | test | 32 | 30 | 2722 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220612-1500/P221VCC1/000 | test | 12 | 0 | 1601 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220612-1500/P221VCC2/000 | test | 116 | 0 | 4030 | abnormal_evidence / insufficient_evidence / all_observed_cleared |
| gusu-valve-20220612-1500/P221VCC2/001 | test | 133 | 0 | 4072 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220612-1500/P221VCC2/002 | test | 121 | 16 | 4058 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220612-1500/P221VCC2/003 | test | 108 | 16 | 4069 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220612-1500/P221VCC2/004 | test | 114 | 18 | 4057 | insufficient_evidence / insufficient_evidence / not_observed |
| gusu-valve-20220612-1500/P221VCC2/005 | test | 100 | 18 | 3889 | abnormal_evidence / abnormal_evidence / all_unresolved |
| gusu-valve-20220612-1700/P221VCC1/000 | test | 42 | 6 | 2367 | abnormal_evidence / abnormal_evidence / all_unresolved |
