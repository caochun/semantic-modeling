# 任务级 State → Decision Model 服务

这是一个三阶段的研究型服务：

~~~text
原始日志
   ↓
GLM State Builder
   ↓
可核对的任务级语义 State
   ↓
Unsloth FastDecisionModel
   ↓
结构化候选判断与选项概率
~~~

原始日志不会直接作为 Decision Model 的输入。State Builder 提取观察事实、状态变化、缺失证据和不确定性，并保留可回到原始输入的 evidence_event_ids。State 可以在页面上编辑，确认后才进入决策模型。

## 接口

- GET /：三阶段浏览器工作台。
- GET /health：Decision Model、GPU 和 GLM State Builder 配置状态。
- GET /api/tasks：任务级决策问题定义和流程元数据。
- POST /v1/state：同步生成任务级 State。
- POST /v1/state/stream：以 SSE 返回解析、GLM 生成、模型增量片段、State 完成等阶段反馈。
- POST /v1/decision：接收已经确认的 state 和问题集合，返回各问题选项概率。

/v1/state 请求示例：

~~~json
{
  "station": "姑苏",
  "task": "换流站监盘：根据原始日志归纳当前任务级状态",
  "logs": "event_time\\tstation\\thost\\tcontent\\n..."
}
~~~

/v1/decision 的输入必须是任务级 State。服务不会在该接口中解析原始日志，也不会生成控制命令。当前模型输出是实验性候选判断，不能代替运行人员对证据、规程和授权条件的复核。

## 配置

State Builder 使用 .env 中的：

~~~text
GLM_API_URL=...
GLM_API_KEY=...
GLM_MODEL=glm-5.3
GLM_REASONING_EFFORT=high
~~~

Decision Model 使用 DECISION_ADAPTER 指向本地 Unsloth adapter。没有 GPU 或 adapter 时，服务仍能启动并显示明确的未就绪状态，但不能运行最终决策。

## 启动

~~~bash
python app.py --host 0.0.0.0 --port 9120
~~~

前端会通过 /v1/state/stream 显示处理阶段和 GLM 流式片段；收到完整 JSON 后再显示可核对的任务级 State。Decision Model 本身输出结构化概率，因此决策阶段只显示运行状态和最终结果。
