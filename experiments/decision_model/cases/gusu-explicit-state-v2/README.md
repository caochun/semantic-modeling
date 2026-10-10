# 规程五明确分支合成日志

这份文件包含 28 条与 `gusu-explicit-state-v2` canonical case 一一对应的合成日志样例。每行是一个 JSON 对象，包含：

- `case_id`：对应数据集 audit 的案例 ID；
- `task_id`：页面中应选择的规程场景；
- `raw_tsv`：可直接复制到页面日志输入框的 TSV；
- `gold_decision`：规程派生的预期分支；
- `events`：结构化事件，便于程序测试。

使用方法：打开 9120 页面，粘贴某条记录的 `raw_tsv`，点击“生成任务级 State”，核对 GLM State 中是否保留了现象、核查结果和不确定性；选择该记录的 `task_id`，再运行 Decision Model。`gold_decision` 只用于离线对照，不能当作现场操作授权。

日志完全由规程条款和合成场景生成，没有使用真实运行记录，也没有经过运行人员或领域专家审核。
