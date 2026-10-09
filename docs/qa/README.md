# 文档 QA 工作流与目录

QA 流程代码放在独立 `qa_pipeline/` 包，现有图建模应用仍由 `semantic_agent/` 提供。业务规划与最终 QA 放在 `data/qa/`，训练试验放在 `experiments/`，原始文档和运行产物留在 `rawdoc/` 与 `.semantic/`。

| 目录 | 内容 | Git |
|---|---|---|
| `qa_pipeline/` | 构建、模型辅助审阅、校验、覆盖、汇总和打印 | 提交 |
| `data/qa/batches/` | 最终批次的事实/问题规划、QA、引用上下文与来源复核 | 提交 |
| `data/qa/releases/` | 固定的累计数据、阅读/打印入口、统计及业务缺口 | 提交 |
| `data/qa/backups/` | 完整本地备份的摘要记录 | 提交记录 |
| `experiments/qa_finetuning/` | 训练、下载、远程后台作业与评测脚本 | 提交代码 |
| `experiments/qa_history/` | 历史生成实验和批次临时工具记录 | 提交代码 |
| `rawdoc/` | 用户提供的原始业务资料 | 本地 |
| `.semantic/` | 提取审计、API调用记录、临时版本、索引、运行与备份 | 本地 |

## 命令

在仓库根目录运行。QA 离线校验使用 Python 3.11+ 标准库；运行应用和测试可执行 `uv sync --dev`，重建旧 Word 提取时加 `--extra qa-extraction`。

```sh
python3 -m qa_pipeline --help
python3 -m qa_pipeline generate --help
python3 -m qa_pipeline review --help
python3 -m qa_pipeline verify-snapshot --dataset data/qa/releases/20261008-batch05
```

安装项目后，也可使用 `uv run qa-pipeline ...`。在其他工作目录执行时，可用 `QA_PROJECT_ROOT` 指定仓库根目录。`--help` 不调用 API，也不下载模型。

从最终规划重建新的候选快照：

```sh
python3 -m qa_pipeline generate \
  --seed data/qa/batches/20261008/batch05/seed.json \
  --source .semantic/qa_operations_20261008 \
  --output .semantic/qa/runs/batch05-rebuild
```

这一步需要本地来源提取与原始文档摘要，不是仅凭数据快照完整重建原始提取。生成结果重新进入待审核状态；不会复制旧审核通过状态。模型审阅使用 `.env` 配置的 GLM：

```sh
python3 -m qa_pipeline review --dataset .semantic/qa/runs/batch05-rebuild
```

审阅后还需逐题来源核对记录。`verify` 要求运行批次的完整提示、响应与绑定，不自动编写审核意见；`verify-snapshot` 面向已归档阅读快照，只检查身份与记录一致性。

汇总五个最终批次并打印到新的本地目录：

```sh
python3 -m qa_pipeline assemble \
  --dataset data/qa/batches/20261008/batch01 \
  --dataset data/qa/batches/20261008/batch02 \
  --dataset data/qa/batches/20261008/batch03 \
  --dataset data/qa/batches/20261008/batch04 \
  --dataset data/qa/batches/20261008/batch05 \
  --output .semantic/qa/runs/assembled
python3 -m qa_pipeline print \
  --dataset .semantic/qa/runs/assembled --output .semantic/qa/runs/printed
```

覆盖台账需要完整来源提取；读取版本化批次时沿用原批次 namespace，保证历史覆盖判定的事实键不因目录名改变而失效。

[数据入口](../../QA_DATASET.md) · [质量控制](quality-control.md) · [覆盖边界](coverage.md) · [批次记录](batches.md) · [研究综述](research.md) · [训练方案](../training/knowledge-injection.md)

历史构建记录绑定的是迁移前的构建器摘要，代码导入路径改变后不能用当前代码冒充当时的构建器。`verify-legacy --builder <原构建器副本>` 可核对原发布；模型运行目录同样保留原 runner 摘要，新代码应使用新的运行目录。
