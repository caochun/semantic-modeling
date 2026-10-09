# 文档 QA 基线评测

先测未微调模型拿到完整资料后的回答，再根据实际错误决定是否训练。本轮使用 39 条留出候选（FAN 17、CAAP 22），不混入 132 条训练候选。参考答案未经专家认证，不能将这一小样本成绩直接解释成现场业务能力。

后续进展：39题基线已完成，其观察被用于第六批训练候选设计，训练累计150条。因此这些题的后续用途为开发对照，不再作为未见最终测试；原始输入、参考和基线记录保持原样，最终测试须另留资料。

## 配置与预检

运行器 `qa_pipeline/baseline.py` 只需 Python 3.11+ 标准库，不依赖项目服务或额外 SDK。读取仓库 `.env` 的字面值（支持引号和注释，不展开变量），进程环境变量优先；密钥不会写入运行记录。

支持三类配置：

|`--provider`|地址|模型名|密钥|
|---|---|---|---|
|`glm`|`GLM_API_URL`|`GLM_MODEL`|`GLM_API_KEY`，或 `ZHIPU_API_KEY`|
|`deepseek`|`DEEPSEEK_API_URL`，缺省为官方 `/v1` 地址|`DEEPSEEK_MODEL`|`DEEPSEEK_API_KEY`|
|`compatible`|`QA_API_URL`|`QA_MODEL`|`QA_API_KEY`，本地无鉴权服务可省略|

地址和模型也可通过 `--api-url`、`--model` 指定。服务需支持非流式 Chat Completions，返回 `choices[0].message.content` 和 `finish_reason`。地址中不允许携带密钥；不自动跟随重定向。

以 GLM 为例，从仓库根目录预检：

```sh
python3 -m qa_pipeline baseline --provider glm --reasoning-effort low --preflight .semantic/qa_baseline_preflight_20261007.json
```

预检不访问服务、不收费，只检查配置与 39 条输入。它记录完整消息的字符数、UTF-8 字节数；两者都不是 token 数。可通过 `--context-window` 记录确认后的模型窗口，但运行器不内置分词器，不能据此声称 token 容量校验通过。完整资料原样发送，不截断；须核对服务端未开启自动截断，窗口不足应调整模型或窗口后新建运行。

输出上限默认 8192 token，可用 `--max-output-tokens` 调整。推理模型的额度可能同时包含思考消耗，不能视为保证可输出 8192 token 正文。GLM 推理强度用 `--reasoning-effort`；DeepSeek 思考模式用 `--thinking enabled/disabled`，开启时按接口支持情况指定推理强度。`--temperature` 未指定时使用服务默认值，运行记录明确标识，不声称确定性复现。

## 获取真实回答

先发一题检查兼容性，确认后在相同配置下续跑其余题：

```sh
python3 -m qa_pipeline baseline --provider glm --reasoning-effort low --output .semantic/qa_baseline_run_001 --limit 1
python3 -m qa_pipeline baseline --provider glm --reasoning-effort low --output .semantic/qa_baseline_run_001 --resume
```

第一条仍固定全部 39 条输入，`--limit 1` 只限制本次请求数；它不是单题完整基线。结束码 2 表示尚未收齐全套完整回答或遇到配置问题。切换模型、参数、输入或运行器代码后，须新建目录，避免混合不同实验。

每题单独请求，只读取 `eval_prompts.jsonl`。不用训练答案、不读取参考答案、不使用建模 agent 的已有知识。运行文件：

- `run.json`：模型、接口、参数、输入与运行器 SHA-256，不含密钥。
- `prompts.jsonl`：本次完整输入快照。
- `attempts.jsonl`：追加写入请求开始、响应时间、正文、接口返回模型名、结束原因与 token 使用量（若接口提供）。不保存私有推理内容。
- `answers.jsonl`：只有完整结束的回答，包含真实收到的空字符串，可直接交给评分工具。
- `status.json`：完整、空回答、截断、连接失败和未尝试计数；无模型成绩。

出现非完整响应即停止本次发送。失败正文（若收到）、截断内容和中断时尚未确定结果的请求保留在日志；不会自动再次请求同一题。核实问题后，显式使用 `--resume --retry-failed` 才会重试这些题；普通 `--resume` 仅执行尚未尝试的题。已成功的题不重复调用。

正常退出或 Ctrl+C 会清理运行锁。进程被强制终止时可能留下 `.running`；先确认对应进程已结束，再移走锁文件续跑，不能在仍运行时并发启动同一目录。

## 评审和比较

收齐回答后准备人工评审：

```sh
python3 .semantic/qa_release_20261007/eval/grade.py prepare .semantic/qa_baseline_run_001/answers.jsonl .semantic/qa_baseline_run_001/review_001.jsonl
# 人工对照原文填写 point_scores、reviewer、status，以及关键错误的序号和依据。
python3 .semantic/qa_release_20261007/eval/grade.py summarize .semantic/qa_baseline_run_001/review_001.jsonl .semantic/qa_baseline_run_001/scores_001.json
```

评分前同时检查 `status.json`：接口错误或截断不应无说明地当成知识错误。评分记录仍保留全部 39 题，缺失回答待处理；未评完不产生全套均分。建议在 `notes` 中按以下类型记录具体回答和原文位置：漏前提、对象混淆、顺序/恢复步骤缺失、忽视例外、无依据补造、误解文档冲突。最后比较两份资料的成绩与关键错误，不能只看一个总分。

若由模型协助评分，须单独标为模型辅助评审，不能填成业务专家人工审阅。本轮工具不自动打语义分。之后若根据这 39 题调提示或训练，须将其改作开发集，并另留最终测试资料。

工具验证使用临时合成响应，不是模型评测结果：

```sh
python3 -B -m unittest discover -s tests -p test_qa_baseline.py -v
```
