# 训练与远程运行实验

- `prepare_training.py`：旧候选的先导训练数据准备。
- `train_pilot.py`、`download_model.py`：1.7B 先导流水线与 hf-mirror.com 权重下载。
- `verify_training_resume.py`：GPU 上检验优化器、调度器和随机状态续跑。
- `launch_pilot.sh`：将已准备的远程独立运行目录交给 systemd 后台运行。
- `qwen/`：早期 Qwen 8B/4B 的准备、显存探测、训练与 adapter 评测。

这些脚本是历史实验入口，不能因为本地 180 条 QA 已被整理就自动导出训练。远程启动前仍需明确数据版本、模型 revision 和独立运行目录。依赖使用当次实验保存的环境记录；本地 QA 工具不导入 Torch、Transformers 或 PEFT。

远程 run 目录原有的 `scripts/train_pilot.py`、`scripts/download_model.py` 是部署时复制的运行载荷，保留该约定以兼容既有后台作业。移动本仓库代码不会改写远程载荷或重启训练。直接将新实验脚本复制到远程 run 的 `scripts/` 后，按运行记录启动 systemd。

[训练运行记录](../../docs/training/pilot-1.7b.md) · [知识注入方案](../../docs/training/knowledge-injection.md) · [早期微调方案](../../docs/training/plan.md)
