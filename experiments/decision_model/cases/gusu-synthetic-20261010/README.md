# 姑苏站确定性日志试探集（2026-10-10）

这些日志是依据规程五状态分支构造的实验输入，不是真实运行记录，也不是专家金标。目的在于测试当前链路能否从更完整的事件边沿中得到确定性较强的 State 和 Decision。

- `all_recovered.tsv`：三台风机反馈均出现，随后均消失。
- `partially_recovered.tsv`：三台风机反馈出现，仅两台消失。
- `unresolved_pump_and_backup.tsv`：主循环泵故障与备用泵未投入同时出现。
- `confirmed_leak_evidence.tsv`：泄漏报警、液位低和现场漏水证据同时出现。

`expected.json` 只记录试探目标，实际模型输出仍需单独记录。
