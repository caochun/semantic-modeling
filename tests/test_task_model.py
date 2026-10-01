import asyncio
import json

from fastapi.testclient import TestClient

from semantic_agent.app import create_app
from semantic_agent.inference import infer
from semantic_agent.task_agent import TaskModelingAgent
from semantic_agent.session_agent import ModelSessionAgent
from semantic_agent.task_model import (
    Assessment, Atom, Binding, ConceptLink, Evidence, Fact, Gap, Goal, ModelDelta,
    ModelReview, Node, PathQuery, Rule, TaskSpec, apply_delta, check_delta, empty_model,
)
from test_prototype import workspace


def simple_model():
    task = TaskSpec(
        objective='判断控制系统目标状态', scope='姑苏站控制系统',
        goals=[Goal(id='goal', question='目标系统是否处于备用状态？', mode='evaluate')],
        gaps=[Gap(id='gap', goal_id='goal', kind='data', description='缺少切换前状态')],
    )
    model = empty_model(task)
    delta = ModelDelta(
        summary='建立状态判断模型',
        nodes=[Node(id='system', kind='entity', label='控制系统', scope='姑苏站'),
               Node(id='standby', kind='concept', label='备用状态', scope='姑苏站')],
        facts=[Fact(id='fact.standby', statement='切换目标处于备用状态',
                    atom=Atom(subject='system', predicate='hasState', object='standby', context='姑苏站'),
                    evidence=[Evidence(passage_id='p_abc123', quote='原文明确说明')])],
        bindings=[Binding(id='goal', query=Atom(subject='system', predicate='hasState', object='standby', context='姑苏站'))],
    )
    sources = {'p_abc123': {'id': 'p_abc123', 'text': '原文明确说明'}}
    checks = check_delta(delta, model, sources)
    review = ModelReview(assessments=[Assessment(id='fact.standby', verdict='supported', reason='原文支持'),
                                      Assessment(id='goal', verdict='supported', reason='查询映射已核对')])
    return apply_delta(model, delta, review, checks)


def test_task_model_has_evidence_backed_replayable_inference():
    model = simple_model()
    report = infer(model)
    assert report['results'][0]['status'] == 'supported'
    assert report['proofs'] and report['validation']['valid']


def test_task_model_exposes_explicit_concept_instance_and_mapping_layers():
    model = simple_model()
    assert model['layer_schema_version'] == 1
    assert {n['id'] for n in model['concept_layer']['nodes']} == {'standby'}
    assert {n['id'] for n in model['instance_layer']['nodes']} == {'system'}
    assert [x['id'] for x in model['instance_layer']['facts']] == ['fact.standby']
    assert model['mapping_layer']['node_to_concept'] == []
    assert model['mapping_layer']['relation_to_concept'] == []


def test_unknown_is_not_treated_as_contradiction():
    model = simple_model()
    model['bindings'][0]['query']['object'] = 'unknown-state'
    report = infer(model)
    assert report['results'][0]['status'] == 'unknown'
    assert report['results'][0]['missing']


def test_explicit_graph_preserves_typed_edges_and_concept_mappings():
    task = TaskSpec(
        objective='解释控制系统切换', scope='姑苏站控制系统',
        goals=[Goal(id='goal', question='切换目标的状态约束是什么？', mode='explain')],
        gaps=[Gap(id='gap', goal_id='goal', kind='knowledge', description='缺少切换约束')],
    )
    model = empty_model(task)
    quote = '发生系统切换时，只能切换至正处于STANDBY状态的系统。'
    delta = ModelDelta(
        summary='提取事件关系并概念化',
        nodes=[Node(id='switch', kind='event', label='控制系统切换', scope='姑苏站'),
               Node(id='target', kind='entity', label='目标系统', scope='姑苏站'),
               Node(id='standby', kind='concept', label='备用状态', scope='姑苏站')],
        facts=[Fact(id='fact.target', statement='切换事件有目标系统',
                    atom=Atom(subject='switch', predicate='hasTarget', object='target', context='姑苏站'),
                    edge_kind='event_entity', relation_concept_ids=['standby'],
                    evidence=[Evidence(passage_id='p_graph', quote=quote)])],
        concept_links=[ConceptLink(id='link.switch', element_id='switch', concept_id='standby', role='node',
                                  evidence=[Evidence(passage_id='p_graph', quote=quote)])],
        bindings=[Binding(id='goal', targets=['fact.target'])],
    )
    sources = {'p_graph': {'id': 'p_graph', 'text': quote}}
    checks = check_delta(delta, model, sources)
    assert not any(checks.values())
    review = ModelReview(assessments=[Assessment(id='fact.target', verdict='supported', reason='事实有原文支持'),
                                      Assessment(id='link.switch', verdict='supported', reason='概念映射有原文依据'),
                                      Assessment(id='goal', verdict='supported', reason='目标映射有效')])
    built = apply_delta(model, delta, review, checks)
    assert built['graph']['edges'][0]['kind'] == 'event_entity'
    assert built['graph']['edges'][0]['status'] == 'supported'
    assert any(x['element_id'] == 'switch' and x['role'] == 'node' for x in built['graph']['concept_links'])


def test_inference_exposes_replayable_proof_path():
    task = TaskSpec(
        objective='判断切换是否违反状态约束', scope='姑苏站控制系统',
        goals=[Goal(id='goal', question='切换是否异常？', mode='evaluate')],
        gaps=[Gap(id='gap', goal_id='goal', kind='data', description='缺少实际目标状态')],
    )
    model = empty_model(task)
    quote = '发生系统切换时，只能切换至正处于STANDBY状态的系统。'
    delta = ModelDelta(
        summary='建立可计算的切换判断',
        nodes=[Node(id='switch', kind='event', label='系统切换', scope='姑苏站'),
               Node(id='target', kind='entity', label='目标系统', scope='姑苏站'),
               Node(id='standby', kind='concept', label='STANDBY', scope='姑苏站'),
               Node(id='ok', kind='concept', label='状态满足', scope='姑苏站')],
        facts=[
            Fact(id='fact.switch', statement='发生系统切换',
                 atom=Atom(subject='switch', predicate='occurs', object='target', context='姑苏站'),
                 edge_kind='event_entity', evidence=[Evidence(passage_id='p_path', quote=quote)]),
            Fact(id='fact.state', statement='目标系统处于STANDBY',
                 atom=Atom(subject='target', predicate='hasState', object='standby', context='姑苏站'),
                 edge_kind='conceptualization', evidence=[Evidence(passage_id='p_path', quote=quote)]),
        ],
        rules=[Rule(id='rule.ok', statement='发生切换且目标为STANDBY则状态满足',
                    premises=[Atom(subject='switch', predicate='occurs', object='?target', context='姑苏站'),
                              Atom(subject='?target', predicate='hasState', object='standby', context='姑苏站')],
                    conclusion=Atom(subject='switch', predicate='stateCheck', object='ok', context='姑苏站'),
                    evidence=[Evidence(passage_id='p_path', quote=quote)])],
        bindings=[Binding(id='goal', query=Atom(subject='switch', predicate='stateCheck', object='ok', context='姑苏站'))],
    )
    sources = {'p_path': {'id': 'p_path', 'text': quote}}
    checks = check_delta(delta, model, sources)
    assert not any(checks.values())
    review = ModelReview(assessments=[Assessment(id='fact.switch', verdict='supported', reason='事实有依据'),
                                      Assessment(id='fact.state', verdict='supported', reason='事实有依据'),
                                      Assessment(id='rule.ok', verdict='supported', reason='规则有依据'),
                                      Assessment(id='goal', verdict='supported', reason='目标映射有效')])
    built = apply_delta(model, delta, review, checks)
    report = infer(built)
    assert report['results'][0]['status'] == 'supported'
    assert report['paths']
    assert report['validation']['paths']['valid']
    assert {'fact.switch', 'fact.state', 'rule.ok'} <= set(report['paths'][0]['edge_ids'])


def test_question_driven_directed_graph_query_records_frontier_and_path():
    task = TaskSpec(
        objective='解释事件到状态的证据链', scope='姑苏站',
        goals=[Goal(id='goal', question='切换事件如何关联到目标状态？', mode='evaluate')],
        gaps=[Gap(id='gap', goal_id='goal', kind='mapping', description='尚未确定事件到状态的关系')],
    )
    model = empty_model(task)
    quote = '系统切换后目标系统状态发生变化。'
    delta = ModelDelta(
        summary='建立有向事件路径',
        nodes=[Node(id='event', kind='event', label='系统切换', scope='姑苏站'),
               Node(id='target', kind='entity', label='目标系统', scope='姑苏站'),
               Node(id='state', kind='concept', label='运行状态', scope='姑苏站')],
        facts=[Fact(id='edge.one', statement='切换事件作用于目标系统',
                    atom=Atom(subject='event', predicate='targets', object='target', context='姑苏站'),
                    edge_kind='event_entity', evidence=[Evidence(passage_id='p_query', quote=quote)]),
               Fact(id='edge.two', statement='目标系统具有运行状态',
                    atom=Atom(subject='target', predicate='hasState', object='state', context='姑苏站'),
                    edge_kind='conceptualization', evidence=[Evidence(passage_id='p_query', quote=quote)])],
        bindings=[Binding(id='goal', path_query=PathQuery(source_id='event', target_id='state',
                                                          predicates=['targets', 'hasState'], context='姑苏站'))],
    )
    sources = {'p_query': {'id': 'p_query', 'text': quote}}
    checks = check_delta(delta, model, sources)
    assert not any(checks.values())
    review = ModelReview(assessments=[Assessment(id='edge.one', verdict='supported', reason='边有依据'),
                                      Assessment(id='edge.two', verdict='supported', reason='边有依据'),
                                      Assessment(id='goal', verdict='supported', reason='路径目标有效')])
    built = apply_delta(model, delta, review, checks)
    report = infer(built)
    assert report['results'][0]['status'] == 'supported'
    assert report['results'][0]['path_ids']
    assert report['validation']['valid']
    assert report['graph_queries']['goal']['paths'][0]['edge_ids'] == ['edge.one', 'edge.two']


def test_task_model_versions_keep_planning_and_inference_snapshots(workspace):
    settings, store = workspace
    task = TaskSpec(
        objective='记录任务模型版本', scope='测试站',
        goals=[Goal(id='goal', question='是否有模型？', mode='explain')],
    )
    model = empty_model(task)
    run_id = store.create_run('analysis', task.objective)
    store.save_task_model(run_id, model)
    model['version'] = 1
    model['phase'] = 'modeled'
    store.save_task_model(run_id, model)
    versions = store.task_model_versions(run_id)
    assert [(x['version'], x['phase']) for x in versions] == [(0, 'planning'), (1, 'modeled')]


def test_completed_task_model_can_start_read_only_model_session(workspace):
    _, store = workspace
    model = simple_model()
    model['phase'] = 'answered'
    run_id = store.create_run('analysis', '只读模型空间')
    store.save_task_model(run_id, model)
    space_id, created = store.create_model_space(run_id)
    assert created and store.model_space(space_id)['version'] == model['version']
    session_id = store.create_agent_session(space_id)
    store.add_session_message(session_id, 'user', '模型是否支持这个问题？')
    session = store.agent_session(session_id)
    assert session['model_space_id'] == space_id
    assert store.session_messages(session_id)[0]['role'] == 'user'


def test_read_only_model_session_does_not_write_model_or_knowledge(workspace):
    settings, store = workspace
    model = simple_model()
    model['phase'] = 'answered'
    run_id = store.create_run('analysis', '只读会话模型')
    store.save_task_model(run_id, model)
    space_id, _ = store.create_model_space(run_id)
    session_id = store.create_agent_session(space_id)
    store.add_session_message(session_id, 'user', '请解释当前模型的结论')

    class SessionClient:
        async def chat(self, messages, tools=None, *, review=False, on_delta=None):
            content = '当前模型支持该结论，但本次只读会话不会修改模型。'
            if on_delta:
                await on_delta({'content': content})
            return {'role': 'assistant', 'content': content}, {'total_tokens': 3}

    message_run = store.create_run('session_message', '请解释当前模型的结论')
    result = asyncio.run(ModelSessionAgent(settings, store, session_id, SessionClient()).run(message_run, '请解释当前模型的结论'))
    assert result['read_only'] is True
    assert store.task_model(run_id)['version'] == model['version']
    assert store.knowledge() == []


def test_task_model_api_exposes_snapshot_and_versions(workspace):
    settings, store = workspace
    task = TaskSpec(
        objective='API 快照', scope='测试站',
        goals=[Goal(id='goal', question='是否有模型？', mode='explain')],
    )
    run_id = store.create_run('analysis', task.objective)
    store.save_task_model(run_id, empty_model(task))
    with TestClient(create_app(settings)) as client:
        snapshot = client.get(f'/api/runs/{run_id}/task-model')
        versions = client.get(f'/api/runs/{run_id}/task-model/versions')
    assert snapshot.status_code == 200
    assert snapshot.json()['graph_schema_version'] == 1
    assert versions.status_code == 200
    assert versions.json()[0]['phase'] == 'planning'


class FakeTaskClient:
    def __init__(self, pid):
        self.pid, self.step = pid, 0

    async def chat(self, messages, tools=None, *, review=False, on_delta=None):
        if review:
            payload = json.loads(messages[-1]['content'])
            if 'model_delta' in payload:
                delta = payload['model_delta']
                ids = [x['id'] for x in [*delta['facts'], *delta['rules'], *delta['bindings']]]
                content = {'assessments': [{'id': i, 'verdict': 'supported', 'reason': '测试原文支持'} for i in ids], 'gaps': []}
            else:
                content = {'answer': '根据任务模型，目标系统满足已建模的备用状态条件，但仍需核对实际切换前状态。[' + self.pid + ']',
                           'issues': [], 'unresolved_questions': ['缺少切换前实际状态']}
            return {'role': 'assistant', 'content': json.dumps(content, ensure_ascii=False)}, {'total_tokens': 20}
        self.step += 1
        if self.step == 1:
            name, args = 'initialize_task', {
                'objective': '判断控制系统目标状态', 'scope': '姑苏站控制系统',
                'goals': [{'id': 'goal', 'question': '目标系统是否处于备用状态？', 'mode': 'evaluate'}],
                'gaps': [{'id': 'gap', 'goal_id': 'goal', 'kind': 'data', 'description': '缺少切换前状态'}],
            }
        elif self.step == 2:
            name, args = 'search_sources', {'query': '备用状态', 'limit': 2}
        elif self.step == 3:
            name, args = 'read_passages', {'ids': [self.pid]}
        elif self.step == 4:
            name, args = 'propose_model_update', {
                'summary': '提取状态事实',
                'nodes': [{'id': 'system', 'kind': 'entity', 'label': '控制系统', 'scope': '姑苏站'},
                          {'id': 'standby', 'kind': 'concept', 'label': '备用状态', 'scope': '姑苏站'}],
                'facts': [{'id': 'fact.standby', 'statement': '切换目标处于备用状态',
                           'atom': {'subject': 'system', 'predicate': 'hasState', 'object': 'standby', 'context': '姑苏站'},
                           'evidence': [{'passage_id': self.pid, 'quote': '发生系统切换时，只能切换至正处于备用状态的系统。'}]}],
                'bindings': [{'id': 'goal', 'query': {'subject': 'system', 'predicate': 'hasState', 'object': 'standby', 'context': '姑苏站'}}],
            }
        elif self.step == 5:
            name, args = 'infer_model', {}
        else:
            name, args = 'submit_model_answer', {
                'answer': '任务模型中的规程事实表明，切换目标需要处于备用状态；这只能验证规则条件，不能替代对本次切换前实际状态的核对。[' + self.pid + ']',
                'model_version': 1, 'proof_ids': [], 'unresolved_questions': ['缺少切换前实际状态'],
            }
        return {'role': 'assistant', 'tool_calls': [{'id': 'call_' + str(self.step), 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}, {'total_tokens': 10}


def test_model_first_agent_persists_model_before_answer(workspace):
    settings, store = workspace
    pid = store.search('系统切换')[0]['id']
    run_id = store.create_run('analysis', '判断控制系统目标状态')
    result = asyncio.run(TaskModelingAgent(settings, store, FakeTaskClient(pid)).run(run_id, '判断控制系统目标状态', 8))
    assert result['task_model']['schema_version'] == 2
    assert result['task_model']['phase'] == 'answered'
    assert result['task_model']['inference']['results'][0]['status'] == 'supported'
    assert result['answer_review']['status'] == 'reviewed'
    assert store.task_model(run_id)['version'] == result['task_model']['version']
