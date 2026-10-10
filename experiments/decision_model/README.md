# 姑苏站告警 Decision Model 试点

从PCS-974FG-D、PCS-9586A和规程五中选择规则，验证两个任务：**本条自检信号是否闭锁装置、当前下一项核查建议是什么**。输入明确带有适用规则和当前观测，目标是应用规则，不是闭卷记忆全部规程，也不训练自动建图。

[数据入口](../../data/decision/gusu-alarm-pilot-v1/README.md) · [逐场景核读](../../data/decision/gusu-alarm-pilot-v1/review.md) · [Unsloth代码核对说明](../../docs/training/decision-model-unsloth.md) · [阀冷现象识别原型](../../docs/training/valve-phenomenon-recognizer.md)

规程五的阀冷日志先经过独立的[现象识别器](../../docs/training/valve-phenomenon-recognizer.md)，完成信号标准化、时间窗口状态和证据图构建，再决定是否进入后续决策模型。这样不会把尚未确认的“泄漏保护屏蔽”直接当成真实泄漏或训练标签。

## 当前完成情况

- 3份姑苏目录文档、18个规则族、43个模拟场景、86条输入记录、134个决策标签。
- 原文文件SHA256和PDF物理页绑定；表格关键行已目视核对，自动检查来源定位锚点。
- 按规则族固定划分train/calibration/test，同一场景的两种表达不跨集合。
- 固定上游源码与模型tokenizer revision；原版数据构造、标签转换和编码函数验证通过，无跳过、无截断，最长503 token。
- 11项本地测试通过。RTX 4090和TITAN V均完成真实参数更新、两轮训练、校准、评估和新进程重载。具体指标及小校准集过拟合问题见 [GPU试点记录](../../docs/training/decision-model-gpu-pilot-20261009.md) 与 [机器可读结果](results/gusu-alarm-pilot-20261009.json)。
- 对用户提供的20条阀冷日志完成双机实测：两份模型均随候选换位改变正常/异常结论，当前不足以可靠判断该新任务。见 [实测与补充训练来源](../../docs/training/decision-model-valve-cooling-probe-20261009.md)；案例独立保存，未加入冻结训练集。
- 已整理 [24个阀冷事件窗口候选](../../data/decision/gusu-valve-windows-v1-candidates/README.md)，含4855条事件和72项待复核判断，按事件日分组。当前缺少可确认正常样本，13个输入超过4096 token，尚未继续训练；见 [数据交付与缺口](../../docs/training/decision-model-valve-window-dataset-20261009.md)。

## 无需训练环境的检查

在仓库根目录执行：

```sh
python3 -m experiments.decision_model.data verify --check-sources
uv run pytest -q tests/test_decision_pilot.py
python3 -m experiments.decision_model.train \
  --output .semantic/decision-model-pilot/preflight --preflight-only
```

没有本地原始PDF时去掉 `--check-sources`，仍可核对冻结文件哈希、标签和分组。重新构建必须另选空目录：

```sh
python3 -m experiments.decision_model.data build \
  --output .semantic/decision-model-pilot/rebuilt
```

真实tokenizer与上游数据协议核对，仅下载代码和tokenizer，不加载模型权重：

```sh
uv run --no-project --python 3.12 \
  --with 'transformers>=5,<6' --with 'httpx[socks]' \
  python -m experiments.decision_model.contract_check
```

该检查按 `upstream.lock.json` 校验源码哈希，只加载原版纯数据函数/数据类，以避开GPU库导入。它确实执行原版 `build_dataset` 和 `encode_record`，但不等同于完整Unsloth运行时测试。

## CUDA训练入口

`train.py` 调用官方 `FastDecisionModel` 和 `DecisionTrainer`，未自写分类头或替代loss。固定基座为 `unsloth/Qwen3.5-0.8B`，revision见锁文件；采用4-bit LoRA，r=16，backbone学习率2e-4，head学习率1e-4，默认2轮。运行目录必须为空，避免混用检查点。

先按机器CUDA/PyTorch组合建立独立的Unsloth环境；然后安装这里锁定的Unsloth源码版本及所需Hugging Face依赖。4-bit路径还需要bitsandbytes：

```sh
python -m pip install \
  'unsloth[huggingface] @ git+https://github.com/unslothai/unsloth.git@cc9128134156cc51f94eeb76d3d19f80bddbc8a7' bitsandbytes

# 下载固定revision，并保存、核对每个文件的SHA256。
python -m experiments.decision_model.download \
  --output .semantic/decision-model-pilot/base

# 使用训练集做1步非零学习率更新，核对梯度与head/LoRA参数变化。
python -m experiments.decision_model.train \
  --model-dir .semantic/decision-model-pilot/base \
  --output .semantic/decision-model-pilot/smoke --smoke-test
python -m experiments.decision_model.verify_reload \
  --run .semantic/decision-model-pilot/smoke

# 独立正式试点目录；不继承smoke的权重。
python -m experiments.decision_model.train \
  --model-dir .semantic/decision-model-pilot/base \
  --output .semantic/decision-model-pilot/run01
python -m experiments.decision_model.verify_reload \
  --run .semantic/decision-model-pilot/run01
```

当前入口针对CUDA。上游另有Apple Silicon/MLX路径，不能据此说Unsloth整体不支持Mac；本试点尚未验证MLX训练。

流水线依次完成：数据/长度检查 → 新初始化决策头基线 → 固定轮数训练 → 校准前评测 → 独立校准集温度拟合 → 最终测试 → 保存adapter与head及逐题预测。没有Hub发布或自动启动推理服务。基线是“原语言模型加新初始化的决策头”，不是原语言模型的问答能力。

`--smoke-test`只使用训练样本做更新及重载探针，不评估或校准测试集。正式试点与smoke都检查有限loss、有限且非零的head/LoRA梯度、两组参数实际变化。保存调用官方`DecisionTrainer.save_model`，保留FP32 head。官方`model.save_pretrained`默认把head转为BF16，实际发现这会让几乎并列的选择翻转，因此不作为本试点的主保存格式。

`verify_reload`在新进程加载adapter和head，以固定的6个训练场景核对全部离散预测相同、概率最大绝对差不超过预先设定的0.02；不会因为概率差很小就忽略选择变化。检查前先写出`reload_actual.json`，失败也保留逐项结果。训练脚本结束时状态为`awaiting_reload_verification`，重载通过才标为`complete`。

共享GPU服务器可用`pipeline.py --run <新目录> --model-dir <基座目录> --wait-service <现有任务.service>`排队。它等待下载完成、现有服务MainPID归零及GPU无计算进程，再依次执行smoke、重载、正式试点、重载；每阶段失败即停，状态写入`pipeline_status.json`。需保持已下载的基座目录可用；adapter记录本地基座路径，迁移时也须迁移同一revision的基座并更新路径。

校准和测试只使用每个场景的自然语言版本，分别是7和13个场景；不得用两种表达将有效样本量翻倍。校准集不参与梯度更新，不用于挑选checkpoint。这个规模只验证链路，不足以估计现场漏报、误报或可靠概率。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `catalog.py` | 已抽读规则、模拟条件分支及候选标签 |
| `data.py` | 来源核对、数据导出、选项键随机映射、冻结及完整性检查 |
| `contract_check.py` | 使用固定上游代码和真实tokenizer核对旧版 choice-only 数据协议 |
| `typed_contract_check.py` | 核对规程五的 `noul`、`choice`、`score` 数据协议，并可运行上游 Clef 编码 |
| `train.py` | 官方训练、校准、评估及保存入口 |
| `download.py` | 固定模型revision下载与文件哈希验证 |
| `runtime_check.py` / `verify_reload.py` | 梯度、参数更新和新进程重载一致性检查 |
| `pipeline.py` | 等待现有GPU任务结束，串行执行smoke与试点 |
| `probe_case.py` / `cases/` | 已保存模型的独立案例推理、选项换位检查及可追溯结果 |
| `valve_windows.py` / `valve_window_catalog.json` / `valve_window_sources.json` | 历史阀冷窗口提取、候选标注、来源绑定、分组和复核包 |
| `valve_window_lengths.py` | 固定tokenizer与官方编码器测量完整候选输入，CPU运行，不加载权重 |
| `valve_windows_v2.py` | 按主机和时间拆分长窗口、携带同日历史状态，冻结证据追踪弱监督集 |
| `phenomenon.py` | 将阀冷日志标准化为观测、现象候选、缺失证据和可追溯证据图；不确认物理故障 |
| `procedure_rules_v1.json` / `procedure_reasoning.py` | 将已识别现象映射到规程五核查项、缺失证据和下一项核查建议；不生成自动处置指令 |
| `state_builder.py` | 将现象和规程核查结果编译为自然语言任务级 state，并把完整结果分离到 audit |
| `train_valve.py` | v2专项训练、旧模型对照、选项换位及观测缺失测试；不拟合置信度 |
| `upstream.lock.json` | 上游源码commit、文件SHA256与模型revision |

候选标签和规则摘要尚未由业务专家审核。特别是同一手册不同段落可能有细节差异，本批未纳入“定值校验出错”与后文泛称“定值出错”的解释问题；源码中的“短时间/长时间”也保留定性描述，没有编造秒数阈值。

第二轮阀冷试验见[数据及实验说明](../../docs/training/decision-model-valve-v2-20261009.md)。v1快照保留；v2有69个子窗口，完整输入均不超过4096 token，按事件日隔离。它训练的是目标信号证据与复归追踪，不是完整的系统健康分类器；缺少经核实的正常样本，标签仍是规则候选。
