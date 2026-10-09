# 版本化 QA 数据

本目录进入 Git。原始文档、完整来源提取、API 调用提示/响应、模型权重与检查点在本地，不随本目录提交。这里的“release”指可阅读的固定数据快照，不代表专家批准或训练发布。

```text
data/qa/
├── batches/20261008/batch01 ... batch05/  各批最终规划、QA、来源和复核记录
├── releases/20261008-batch05/            180题汇总、阅读与打印文件
├── backups/                             本地完整备份的位置与校验值
└── legacy/focused_qa.json                旧候选生成规格，保留历史用途
```

每个批次包含 `seed.json`（事实、问题、条件与顺序规划）、`candidates.jsonl`（原始候选）、`chains.json`（事实及完整引用上下文）、`source_reviews.json`（逐题 AI 来源核对），以及模型意见、复核说明和适用的整条覆盖判定。短引文关联与整条覆盖判定分开保存。

发布目录的 `qa.jsonl` 是五批汇总，带原批次、原题 ID 和候选摘要。`qa.md` 与 `print.html` 保留全部问题、答案和多轮历史；`index.html` 支持离线搜索。`pending_business_issues.json` 保留尚未闭合的业务问题。未认证来源现行性，未进行业务专家审核，`training_eligible` 为 false。

`manifest.json`、审核绑定及 QA 内容按原快照字节复制，未改写原来的 `.semantic/` 来源路径。`snapshot.json` 提供现在的批次位置映射、已收录文件摘要和未收录审核证据的原始摘要；它不是新的审核意见。原始复核说明中的旧路径属于当时的记录，当前位置以 snapshot 映射为准。

```sh
# 无 rawdoc、无 API 密钥也可以执行；不会调用模型
python3 -m qa_pipeline verify-snapshot --dataset data/qa/releases/20261008-batch05

# 有原本地运行目录时，另外逐文件核对未入库的提示/响应等证据
python3 -m qa_pipeline verify-snapshot --dataset data/qa/releases/20261008-batch05 --with-archive
```

第一条检查已收录文件身份、问题/答案/对话历史、事实计划、审核绑定和发布来源关系；外部证据摘要仅作登记，不等于验证外部文件。第二条要求原本地运行目录存在，并实际检查这些外部文件。两者都不评估业务正确率或全库覆盖率。

新批次先在 `.semantic/qa/runs/` 生成和审阅；完成逐题来源核对后，再以新版本目录收录，不覆盖旧 release，也不将模型 pass 自动升级为训练资格。
