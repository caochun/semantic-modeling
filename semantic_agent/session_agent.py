"""Read-only agent sessions backed by an immutable task-model snapshot."""
import json

from .llm import GLMClient, ModelError
from .streaming import StreamReporter


SESSION_SYSTEM = """你是一个只读的业务知识增强 Agent。
用户已经选择了一个固定版本的任务级模型。这个模型是本会话唯一的业务知识基座；你可以使用其中的概念层、实例层、规则、证据和推理边界回答问题。

严格约束：
1. 不创建、修改、撤回或发布任何模型、知识或概念映射。本次会话产生的判断只是临时回答。
2. 不把用户新提供的日志自动写入模型。把它们当作本次会话的输入数据，和模型已有事实明确区分。
3. 只使用所选模型快照中 status=supported 的事实、规则和映射。模型没有覆盖的内容必须明确说未知或超出范围。
4. 规范要求、历史日志和本次用户输入不是同一种事实；保留站点、设备、时间、条件和适用范围。
5. 引用模型证据中的原文片段时使用已有的 [p_xxx] ID。不能伪造引用，也不能把模型中的标题当作原文证据。
6. 回答先给结论状态（已支持、疑似、未知或超出范围），再说明命中的模型规则、输入数据、证据和仍需核对的信息。
7. 不输出私有推理过程，只输出简洁、可核查的业务回答。
"""


class ModelSessionAgent:
    def __init__(self, settings, store, session_id, client=None):
        self.settings = settings
        self.store = store
        self.session_id = session_id
        self.client = client or GLMClient(settings)

    async def run(self, run_id, question):
        session = self.store.agent_session(self.session_id)
        if not session:
            raise ModelError('会话不存在')
        space = self.store.model_space(session['model_space_id'])
        if not space or space['version'] != session['model_version']:
            raise ModelError('会话绑定的模型空间不可用')
        model = space['snapshot']
        history = self.store.session_messages(self.session_id)[-12:]
        # The API stores the current user message before launching the job;
        # append it once below instead of duplicating it in the prompt.
        if history and history[-1]['role'] == 'user' and history[-1]['content'] == question:
            history = history[:-1]
        context = {
            'model_space': {
                'id': space['id'], 'title': space['title'], 'version': space['version'],
                'objective': space['objective'], 'scope': space['scope'],
            },
            'task_model': {
                'concept_layer': model.get('concept_layer', {}),
                'instance_layer': model.get('instance_layer', {}),
                'mapping_layer': model.get('mapping_layer', {}),
                'gaps': model.get('gaps', []),
                'validation': model.get('validation', {}),
                'inference': model.get('inference', {}),
                'task': model.get('task', {}),
            },
        }
        messages = [{'role': 'system', 'content': SESSION_SYSTEM},
                    {'role': 'user', 'content': '选定模型快照：\n' + json.dumps(context, ensure_ascii=False)}]
        for item in history:
            if item['role'] in {'user', 'assistant'}:
                messages.append({'role': item['role'], 'content': item['content']})
        messages.append({'role': 'user', 'content': question})
        stream = StreamReporter(self.store, run_id, 1)
        try:
            response, usage = await self.client.chat(messages, on_delta=stream)
            stream.emit(response, force=True)
            answer = str(response.get('content') or '').strip()
            if len(answer) < 10:
                raise ModelError('模型没有返回有效回答')
            self.store.add_session_message(self.session_id, 'assistant', answer, run_id)
            result = {'answer': answer, 'model_space_id': space['id'],
                      'model_version': space['version'], 'usage': usage,
                      'read_only': True}
            self.store.finish_run(run_id, 'completed', result)
            self.store.trace(run_id, 'complete', '只读模型会话回答完成')
            return result
        except Exception as exc:
            self.store.finish_run(run_id, 'failed', error=str(exc)[:500])
            self.store.trace(run_id, 'error', '只读模型会话失败：' + str(exc)[:300])
            return None
