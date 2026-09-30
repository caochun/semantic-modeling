"""Task-local vocabulary, evidence assertions, and versioned model updates."""
import copy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Evidence(Strict):
    passage_id: str
    quote: str = Field(min_length=4)


class Goal(Strict):
    id: str
    question: str
    mode: Literal['explain', 'evaluate']


class Gap(Strict):
    id: str
    goal_id: str
    kind: Literal['knowledge', 'data', 'conflict', 'mapping']
    description: str
    query: str = ''
    source_type: Literal['documents', 'logs', 'all', 'user'] = 'documents'


class TaskSpec(Strict):
    objective: str
    scope: str
    path_contains: str = ''
    goals: list[Goal] = Field(min_length=1)
    gaps: list[Gap] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def references(self):
        ids = [g.id for g in self.goals]
        if len(ids) != len(set(ids)) or any(g.goal_id not in ids for g in self.gaps):
            raise ValueError('目标 ID 必须唯一，缺口必须指向已有目标')
        if len({g.id for g in self.gaps}) != len(self.gaps):
            raise ValueError('缺口 ID 必须唯一')
        return self


class Node(Strict):
    id: str
    kind: Literal['entity', 'event', 'concept']
    label: str
    scope: str
    description: str = ''
    definition: str = Field(default='', description='definition is accepted as a source-compatible alias for description')
    concept_ids: list[str] = Field(default_factory=list, description='未复核的检索线索；正式概念化请提交 concept_links')


EdgeKind = Literal['entity_entity', 'entity_event', 'event_entity', 'event_event', 'conceptualization', 'other']


class Atom(Strict):
    subject: str = Field(description='节点 ID，规则中可使用 ?x 变量')
    predicate: str = Field(min_length=1, description='按领域证据命名的谓词，如 切换目标、切换前状态')
    object: str = Field(description='节点 ID 或文字/数值，规则中可使用 ?x 变量')
    context: str = Field(min_length=1, description='精确的站点/系统/事件时刻或阶段标识，规则可用 ?ctx 变量')
    negative: bool = False


class Fact(Strict):
    id: str
    statement: str
    atom: Atom
    evidence: list[Evidence] = Field(min_length=1)
    edge_kind: EdgeKind = 'other'
    relation_concept_ids: list[str] = Field(default_factory=list, description='未复核的检索线索；正式关系概念化请提交 concept_links')


class ConceptLink(Strict):
    id: str
    element_id: str = Field(description='role=node 时为实体/事件 ID；role=relation 时为事实边 ID')
    concept_id: str
    role: Literal['node', 'relation']
    evidence: list[Evidence] = Field(min_length=1)


class Guard(Strict):
    left: str = Field(description='已由前提绑定的变量或常量')
    op: Literal['eq', 'ne', 'gt', 'ge', 'lt', 'le']
    right: str


class Rule(Strict):
    id: str
    statement: str
    premises: list[Atom] = Field(min_length=1)
    guards: list[Guard] = Field(default_factory=list)
    conclusion: Atom
    evidence: list[Evidence] = Field(min_length=1)


class PathQuery(Strict):
    source_id: str
    target_id: str = Field(default='', description='可留空以查询路径终点')
    predicates: list[str] = Field(default_factory=list, max_length=6, description='按顺序匹配的关系；留空则遍历有向边')
    context: str = Field(min_length=1, description='路径中每条边必须具有相同上下文')
    max_hops: int = Field(default=4, ge=1, le=6)


class Binding(Strict):
    id: str = Field(description='对应 TaskSpec 的 goal ID')
    targets: list[str] = Field(default_factory=list, description='explain 模式要解释的事实/规则 ID')
    query: Atom | None = None
    path_query: PathQuery | None = Field(default=None, description='显式图多跳查询；不能将连通自动解释为因果或异常')


class GapResolution(Strict):
    id: str
    gap_id: str
    support_ids: list[str] = Field(min_length=1, description='足以补齐缺口的事实/规则/查询映射 ID')
    reason: str


class ModelDelta(Strict):
    summary: str
    nodes: list[Node] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    concept_links: list[ConceptLink] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    bindings: list[Binding] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    gap_resolutions: list[GapResolution] = Field(default_factory=list)
    retract_ids: list[str] = Field(default_factory=list, description='撤回错误元素；改变含义时使用新 ID，重新复核相关查询映射')


class ConceptDelta(Strict):
    summary: str
    nodes: list[Node] = Field(default_factory=list, description='新增的 concept 节点')
    concept_links: list[ConceptLink] = Field(min_length=1)

    @model_validator(mode='after')
    def concepts_only(self):
        if any(n.kind != 'concept' for n in self.nodes):
            raise ValueError('概念化阶段只能新增 concept 节点')
        return self


class Assessment(Strict):
    id: str
    verdict: Literal['supported', 'insufficient', 'conflict']
    reason: str


class ModelReview(Strict):
    assessments: list[Assessment]
    gaps: list[Gap] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique(self):
        if len({a.id for a in self.assessments}) != len(self.assessments):
            raise ValueError('复核 ID 不得重复')
        return self


FIELDS = ('nodes', 'facts', 'concept_links', 'rules', 'bindings', 'gap_resolutions')


def variables(atom):
    return {getattr(atom, k) for k in ('subject', 'object', 'context') if getattr(atom, k).startswith('?')}


def edge_kind(atom, nodes):
    source = nodes.get(atom['subject'], {}).get('kind')
    target = nodes.get(atom['object'], {}).get('kind')
    if source == 'concept' or target == 'concept':
        return 'conceptualization'
    return f'{source}_{target}' if source and target else 'other'


def check_delta(delta, model, sources):
    from .store import normalize
    old = {x['id']: (field, x) for field in FIELDS for x in model.get(field, [])}
    ids = [x.id for field in FIELDS for x in getattr(delta, field)]
    if len(ids) != len(set(ids)):
        raise ValueError('图元素、规则、映射及缺口解决记录的 ID 不得重复')
    for field in FIELDS:
        for x in getattr(delta, field):
            if x.id in old:
                prior_field, prior = old[x.id]
                # Bindings are re-reviewed when the query changes; immutable graph
                # IDs prevent unnoticed changes to facts already used by a proof.
                if field != prior_field or (field != 'bindings' and any(prior.get(k) != v for k, v in x.model_dump().items())):
                    raise ValueError('已有元素含义不能用相同 ID 覆盖，请撤回并用新 ID：' + x.id)
    merged = {f: {x['id']: x for x in model.get(f, []) if x['id'] not in delta.retract_ids} for f in FIELDS}
    for field in FIELDS:
        merged[field].update({x.id: x.model_dump() for x in getattr(delta, field)})
    nodes = merged['nodes']
    concepts = {i for i, n in nodes.items() if n['kind'] == 'concept'}
    goals = {g['id']: g for g in model['task']['goals']}
    gaps = {g['id']: g for g in model['gaps']}
    gaps.update({g.id: g.model_dump() for g in delta.gaps})
    for g in delta.gaps:
        if g.goal_id not in goals:
            raise ValueError('缺口引用了未知目标')
    for node in delta.nodes:
        if set(node.concept_ids) - concepts:
            raise ValueError('概念提示必须引用 concept 节点')
    errors = {}
    for field in FIELDS[1:]:
        for item in getattr(delta, field):
            checks, atoms = [], []
            for ev in getattr(item, 'evidence', []):
                source = sources.get(ev.passage_id)
                if not source or normalize(ev.quote) not in normalize(source['text']):
                    checks.append('摘录不匹配或来源未读取：' + ev.passage_id)
            if isinstance(item, Fact):
                atoms = [item.atom]
                if variables(item.atom):
                    checks.append('事实不能包含变量')
                expected = edge_kind(item.atom.model_dump(), nodes)
                if item.edge_kind != 'other' and item.edge_kind != expected:
                    checks.append('关系类型与端点节点类型不一致，应为 ' + expected)
                if set(item.relation_concept_ids) - concepts:
                    checks.append('关系概念提示必须引用 concept 节点')
            elif isinstance(item, Rule):
                atoms = [*item.premises, item.conclusion]
                bound = set().union(*(variables(p) for p in item.premises))
                needed = variables(item.conclusion) | {x for g in item.guards for x in (g.left, g.right) if x.startswith('?')}
                if needed - bound:
                    checks.append('规则结论或比较条件有未绑定变量')
            elif isinstance(item, ConceptLink):
                if item.concept_id not in concepts:
                    checks.append('概念映射目标必须为 concept 节点')
                if item.role == 'node' and nodes.get(item.element_id, {}).get('kind') not in {'entity', 'event'}:
                    checks.append('节点概念化必须引用实体或事件')
                if item.role == 'relation' and item.element_id not in merged['facts']:
                    checks.append('关系概念化必须引用事实边')
            elif isinstance(item, Binding):
                goal = goals.get(item.id)
                if not goal:
                    checks.append('查询映射必须对应任务目标')
                if sum(bool(x) for x in (item.targets, item.query, item.path_query)) != 1:
                    checks.append('targets、query、path_query 必须且只能提供一种')
                if item.targets and (not goal or goal['mode'] != 'explain'):
                    checks.append('targets 只用于解释目标')
                if set(item.targets) - (merged['facts'].keys() | merged['rules'].keys()):
                    checks.append('解释目标引用了未知模型元素')
                if item.query:
                    atoms = [item.query]
                if item.path_query:
                    pq = item.path_query
                    if pq.source_id not in nodes:
                        checks.append('路径起点必须是节点')
                    if len(pq.predicates) > pq.max_hops:
                        checks.append('路径关系数量超过最大跳数')
            elif isinstance(item, GapResolution):
                if item.gap_id not in gaps:
                    checks.append('未知缺口')
                if set(item.support_ids) - (merged['facts'].keys() | merged['rules'].keys() | merged['bindings'].keys()):
                    checks.append('缺口解决依据不存在')
            for atom in atoms:
                if not atom.subject.startswith('?') and atom.subject not in nodes:
                    checks.append('主语必须是已定义节点：' + atom.subject)
                if not atom.context.strip():
                    checks.append('缺少适用上下文')
            errors[item.id] = checks
    return errors


def empty_model(task):
    return {'schema_version': 2, 'graph_schema_version': 1, 'version': 0, 'task': task.model_dump(),
            **{f: [] for f in FIELDS}, 'gaps': [dict(g.model_dump(), status='open') for g in task.gaps],
            'graph': {'nodes': [], 'edges': [], 'concept_links': [], 'paths': []},
            'inference': None, 'validation': {}, 'phase': 'planning'}


def build_graph(model):
    nodes = copy.deepcopy(model.get('nodes', []))
    for node in nodes:
        if node.get('definition') and not node.get('description'):
            node['description'] = node['definition']
    node_map = {n['id']: n for n in nodes}
    links = copy.deepcopy(model.get('concept_links', []))
    edges = []
    for fact in model.get('facts', []):
        a = fact['atom']
        edges.append({'id': fact['id'], 'fact_id': fact['id'], 'source_id': a['subject'],
                      'predicate': a['predicate'], 'target_id': a['object'], 'context': a['context'],
                      'negative': a['negative'], 'kind': edge_kind(a, node_map),
                      'statement': fact['statement'], 'evidence': fact['evidence'],
                      'concept_ids': [l['concept_id'] for l in links if l['element_id'] == fact['id']
                                      and l['role'] == 'relation' and l.get('status') == 'supported'],
                      'status': fact.get('status', 'candidate'), 'review': fact.get('review')})
    return {'nodes': nodes, 'edges': edges, 'concept_links': links, 'paths': []}


def apply_delta(model, delta, review, checks):
    result = copy.deepcopy(model)
    assessments = {a.id: a.model_dump() for a in review.assessments}
    for field in FIELDS:
        items = {x['id']: x for x in result.get(field, []) if x['id'] not in delta.retract_ids}
        for element in getattr(delta, field):
            value = element.model_dump()
            if field != 'nodes':
                a = assessments.get(element.id, {'verdict': 'insufficient', 'reason': '未返回独立语义复核'})
                value['review'] = {**a, 'check_errors': checks.get(element.id, [])}
                if isinstance(element, Binding):
                    # A query mapping is a structural model assertion rather
                    # than a source fact. It may be accepted when structurally
                    # valid even if the semantic reviewer cannot quote it.
                    value['status'] = 'supported' if not checks.get(element.id) and a['verdict'] != 'conflict' else 'insufficient'
                else:
                    value['status'] = a['verdict'] if not checks.get(element.id) else 'insufficient'
            items[element.id] = value
        result[field] = list(items.values())
    # Retractions invalidate dependent items, never leave a supported dangling edge.
    nodes = {n['id']: n for n in result['nodes']}
    all_node_ids = set(nodes) | {n['id'] for n in model['nodes']}
    active = {x['id']: x for x in result['nodes']}
    active.update({x['id']: x for f in FIELDS for x in result[f]})
    for field in ('facts', 'rules', 'concept_links', 'bindings', 'gap_resolutions'):
        for x in result[field]:
            refs = []
            if field in ('facts', 'rules'):
                atoms = [x['atom']] if field == 'facts' else [*x['premises'], x['conclusion']]
                refs = [a[k] for a in atoms for k in ('subject', 'object') if a[k] in all_node_ids]
            elif field == 'concept_links':
                refs = [x['element_id'], x['concept_id']]
            elif field == 'bindings':
                refs = x['targets']
                if x.get('path_query'):
                    refs = [x['path_query']['source_id']]
                if x.get('query'):
                    refs = [x['query'][k] for k in ('subject', 'object') if x['query'][k] in all_node_ids]
            else:
                refs = x['support_ids']
            if any(r not in active or active[r].get('status', 'supported') != 'supported' for r in refs):
                x['status'] = 'insufficient'
                x['review'] = {'verdict': 'insufficient', 'reason': '依赖元素已撤回或未通过复核'}
    gaps = {g['id']: g for g in result['gaps']}
    goals = {g['id'] for g in model['task']['goals']}
    for g in [*delta.gaps, *review.gaps]:
        if g.goal_id in goals:
            gaps[g.id] = {**g.model_dump(), 'status': 'open'}
    for g in gaps.values():
        resolutions = [r for r in result['gap_resolutions'] if r['gap_id'] == g['id'] and r['status'] == 'supported']
        g['status'] = 'resolved' if resolutions else 'open'
        g['resolution_ids'] = [r['id'] for r in resolutions]
    result['gaps'] = list(gaps.values())
    result['version'] += 1
    result['phase'] = 'modeled'
    result['inference'] = None
    result['graph'] = build_graph(result)
    return result
