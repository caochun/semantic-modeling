# 实验代码

`qa_finetuning/` 保存模型下载、训练、远程后台运行与评测代码；`qa_history/` 保存旧候选构建和一次性批次工具。当前工作重点是 QA 质量，移动这些脚本不会启动新训练或修改服务器上的既有任务。

实际运行数据、权重、适配器、检查点和日志应写入 `.semantic/qa/experiments/` 或远程独立运行目录；本目录提交实验代码与说明。

[QA 数据与审核流程](../docs/qa/README.md) · [训练实验](qa_finetuning/README.md)
