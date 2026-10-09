# 文档 QA 数据入口

当前累计 **180 条 QA、27 个业务单元**，保留列示来源的 AI 文档忠实性复核记录。原文现行性、业务正确性和业务专家审核未认证，样本尚未取得训练资格。

- [QA 全文](data/qa/releases/20261008-batch05/qa.md)：保留原问题、答案及合成对话历史。
- [离线搜索](data/qa/releases/20261008-batch05/index.html) · [打印 / 保存 PDF](data/qa/releases/20261008-batch05/print.html)。
- [数据目录与字段说明](data/qa/README.md) · [目录分工与命令](docs/qa/README.md)。
- [质量控制](docs/qa/quality-control.md) · [覆盖边界](docs/qa/coverage.md) · [批次记录](docs/qa/batches.md)。
- [QA 生成研究综述](docs/qa/research.md) · [研究来源](docs/qa/research-sources.json)。

五批最终数据和规划在 `data/qa/batches/20261008/batch01` 至 `batch05`：18、18、41、50、53 题。第五批补直流闭锁、功率回降、滤波器控制、线路再启动与保护跳闸；[未闭合业务问题](data/qa/releases/20261008-batch05/pending_business_issues.json)保留降压方式冲突、OLT/检修后复役流程以及前批规则衔接缺口。

[覆盖统计](data/qa/releases/20261008-batch05/coverage_summary.json)登记 174 份去重业务文档，独立盘点了两份规程；235 个完整提取来源单元另行核对了整条文本覆盖。274 个事实规划条目未经知识去重，业务事实分母未建立，`global_coverage_rate` 仍为 `null`。

离线验证当前快照：

```sh
python3 -m qa_pipeline verify-snapshot --dataset data/qa/releases/20261008-batch05
```

原始文档在本地 `rawdoc/`，模型提示、响应、历史草稿、训练产物和完整来源台账留在被忽略的 `.semantic/`。版本化快照保留原始文件摘要及来源目录映射，不将目录迁移记为新的审核。[备份记录](data/qa/backups/20261008-batch05.json)对应完整审核备份包；备份 ZIP 本体不放进 Git。

旧版 1,997 条候选及 415 条先导训练保留为历史实验，不能自动继承当前复核状态。[历史候选构建说明](docs/qa/construction.md)、[训练运行记录](docs/training/pilot-1.7b.md)、[训练实验目录](experiments/qa_finetuning/README.md)可回查。
