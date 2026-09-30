"""Model-only Horn inference with explicit negation and replayable proof records.

No documents, LLM or graph connectivity are used as inference premises.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from .task_graph import graph_paths, verify_graph_query

FIELDS = ('subject', 'predicate', 'object', 'context', 'negative')


def model_fingerprint(model):
    fields = ('version', 'task', 'nodes', 'facts', 'rules', 'bindings', 'concept_links', 'gaps')
    return hashlib.sha256(json.dumps({k: model.get(k) for k in fields}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def key(atom):
    return tuple(atom[k] for k in FIELDS)


def opposite(atom):
    return {**atom, 'negative': not atom['negative']}


def bind(pattern, atom, bindings=None):
    result = dict(bindings or {})
    for field in FIELDS:
        expected, actual = pattern[field], atom[field]
        if field in ('subject', 'object', 'context') and expected.startswith('?'):
            if expected in result and result[expected] != actual:
                return None
            result[expected] = actual
        elif expected != actual:
            return None
    return result


def instantiate(atom, bindings):
    return {k: bindings.get(v, v) if isinstance(v, str) else v for k, v in atom.items()}


def guards_pass(guards, bindings):
    for g in guards:
        a, b = bindings.get(g['left'], g['left']), bindings.get(g['right'], g['right'])
        if a.startswith('?') or b.startswith('?'):
            return False
        op = g['op']
        if op in ('eq', 'ne'):
            ok = a == b if op == 'eq' else a != b
        else:
            try:
                left, right = Decimal(a), Decimal(b)
                if not left.is_finite() or not right.is_finite():
                    return False
                ok = {'gt': left > right, 'ge': left >= right, 'lt': left < right, 'le': left <= right}[op]
            except InvalidOperation:
                return False
        if not ok:
            return False
    return True


def infer(model, *, limit=4000, _verify=True):
    proofs, atoms = {}, {}
    rules = [r for r in model['rules'] if r.get('status') == 'supported']
    work = 0
    truncated = False

    def add(atom, source_id=None, rule=None, premises=None, bindings=None):
        if key(atom) in atoms:
            return False
        pid = 'proof_' + hashlib.sha256(json.dumps(atom, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        proof = {'id': pid, 'atom': atom, 'fact_id': source_id, 'rule_id': rule,
                 'premise_ids': premises or [], 'bindings': bindings or {}}
        atoms[key(atom)] = pid
        proofs[pid] = proof
        return True

    for fact in model['facts']:
        if fact.get('status') == 'supported':
            add(fact['atom'], source_id=fact['id'])
    changed = True
    while changed and not truncated:
        changed = False
        for rule in rules:
            states = [({}, [])]
            for premise in rule['premises']:
                next_states = []
                for bindings, ids in states:
                    for proof in list(proofs.values()):
                        work += 1
                        if work > limit:
                            truncated = True
                            break
                        matched = bind(premise, proof['atom'], bindings)
                        if matched is not None:
                            next_states.append((matched, [*ids, proof['id']]))
                    if truncated:
                        break
                states = next_states
                if truncated or not states:
                    break
            if truncated:
                break
            for bindings, ids in states:
                if guards_pass(rule['guards'], bindings):
                    conclusion = instantiate(rule['conclusion'], bindings)
                    if any(isinstance(v, str) and v.startswith('?') for v in conclusion.values()):
                        continue
                    changed |= add(conclusion, rule=rule['id'], premises=ids, bindings=bindings)

    def conflicted(pid, visiting=None):
        visiting = set(visiting or ())
        if pid in visiting:
            return True
        p = proofs[pid]
        return key(opposite(p['atom'])) in atoms or any(conflicted(x, visiting | {pid}) for x in p['premise_ids'])

    for pid, proof in proofs.items():
        proof['status'] = 'conflict' if conflicted(pid) else 'supported'
    items = {x['id']: x for x in [*model['facts'], *model['rules']]}
    bindings = {x['id']: x for x in model['bindings'] if x.get('status') == 'supported'}
    results, graph_queries = [], {}
    for goal in model['task']['goals']:
        binding = bindings.get(goal['id'])
        result = {'goal_id': goal['id'], 'question': goal['question'], 'mode': goal['mode'],
                  'status': 'unknown', 'proof_ids': [], 'model_ids': [], 'missing': []}
        if not binding:
            result['missing'] = ['尚未建立经过复核的问题到模型的查询映射']
        elif binding.get('path_query'):
            traversal = graph_paths(model, binding['path_query'])
            graph_queries[goal['id']] = traversal
            result['path_ids'] = [p['id'] for p in traversal['paths']]
            result['model_ids'] = list(dict.fromkeys(i for p in traversal['paths'] for i in p['edge_ids']))
            result['proof_ids'] = [p['id'] for p in proofs.values() if p.get('fact_id') in result['model_ids']]
            result['status'] = 'incomplete' if not traversal['search_complete'] else 'supported' if traversal['paths'] else 'unknown'
            result['missing'] = [] if traversal['paths'] else traversal['frontier'] or ['模型中尚无符合目标的有向证据路径']
        elif binding.get('targets'):
            selected = [items.get(i) for i in binding['targets']]
            valid = [x for x in selected if x and x.get('status') == 'supported']
            result['model_ids'] = [x['id'] for x in valid]
            result['proof_ids'] = [p['id'] for p in proofs.values()
                                   if p.get('fact_id') in result['model_ids'] or
                                   p.get('rule_id') in result['model_ids']]
            # A contradictory fact cannot be presented as unqualified evidence.
            conflict = any(x.get('atom') and key(opposite(x['atom'])) in atoms for x in valid)
            result['status'] = 'conflict' if conflict else 'supported' if valid and len(valid) == len(selected) else 'unknown'
            if result['status'] == 'unknown':
                result['missing'] = ['解释所需的模型条目尚未全部通过复核']
        elif binding.get('query'):
            query = binding['query']
            matches = [p for p in proofs.values() if bind(query, p['atom']) is not None]
            against = [p for p in proofs.values() if bind(opposite(query), p['atom']) is not None]
            if matches:
                result['proof_ids'] = [p['id'] for p in matches]
                result['status'] = 'conflict' if any(p['status'] == 'conflict' for p in matches) else 'supported'
            elif against and not any(str(v).startswith('?') for v in query.values()):
                result['proof_ids'] = [p['id'] for p in against]
                result['status'] = 'conflict' if any(p['status'] == 'conflict' for p in against) else 'contradicted'
            else:
                result['missing'] = missing_premises(query, rules, proofs)
        results.append(result)
    if truncated:
        # A later derivation may expose a conflict, so unfinished closure cannot verify a positive claim.
        for result in results:
            result['status'] = 'incomplete'
            result['missing'].append('推理搜索尚未完成，当前结果不能标为已验证')
    report = {'model_version': model['version'], 'model_fingerprint': model_fingerprint(model),
              'results': results, 'proofs': list(proofs.values()), 'graph_queries': graph_queries,
              'search_complete': not truncated and all(g['search_complete'] for g in graph_queries.values()), 'operations': work,
              'work_limit': limit}
    report['paths'] = build_proof_paths(model, report)
    if _verify:
        report['validation'] = verify_report(model, report)
    return report


def verify_report(model, report):
    validation = verify_proofs(model, report)
    validation['paths'] = verify_paths(model, report)
    replay = infer(model, limit=report.get('work_limit', 4000), _verify=False)
    # Replay the goal evaluation too: valid premises alone cannot validate an
    # altered conclusion, omitted conflict, or a claim of search completeness.
    if any(report.get(k) != v for k, v in replay.items()):
        validation['errors'].append('查询结论或搜索覆盖与模型重放不一致')
    validation['valid'] = validation['valid'] and validation['paths']['valid'] and not validation['errors']
    return validation


def missing_premises(query, rules, proofs):
    missing = []
    for rule in rules:
        if rule['conclusion']['predicate'] != query['predicate'] or rule['conclusion']['negative'] != query['negative']:
            continue
        # Ground query values bind rule variables. Query variables remain open.
        constraints = {}
        compatible = True
        for field in ('subject', 'object', 'context'):
            head, wanted = rule['conclusion'][field], query[field]
            if wanted.startswith('?'):
                continue
            if head.startswith('?'):
                if head in constraints and constraints[head] != wanted:
                    compatible = False
                constraints[head] = wanted
            elif head != wanted:
                compatible = False
        if not compatible:
            continue
        states = [constraints]
        for premise in rule['premises']:
            next_states = []
            for state in states:
                found = [b for p in proofs.values() if (b := bind(premise, p['atom'], state)) is not None]
                if found:
                    next_states.extend(found)
                else:
                    missing.append(instantiate(premise, state))
                    next_states.append(state)
            states = next_states[:64]
        if not missing and rule['guards']:
            missing.append({'rule_id': rule['id'], 'unsatisfied_guards': rule['guards']})
    return missing or ['当前模型没有支持此查询的事实或适用规则']


def verify_proofs(model, report):
    """Replay every inference against the immutable model snapshot."""
    facts = {f['id']: f for f in model['facts'] if f.get('status') == 'supported'}
    rules = {r['id']: r for r in model['rules'] if r.get('status') == 'supported'}
    verified, errors = {}, []
    if report.get('model_version') != model['version'] or report.get('model_fingerprint') != model_fingerprint(model):
        errors.append('模型快照与推理版本不一致')
    for proof in report['proofs']:
        if proof['id'] in verified:
            errors.append('推理步骤 ID 重复：' + proof['id'])
            continue
        valid = False
        if proof['fact_id']:
            fact = facts.get(proof['fact_id'])
            valid = bool(fact and key(fact['atom']) == key(proof['atom']) and not proof['premise_ids'] and not proof['rule_id'])
        elif proof['rule_id'] in rules:
            rule = rules[proof['rule_id']]
            ids = proof['premise_ids']
            bindings = {}
            valid = len(ids) == len(rule['premises'])
            for pattern, pid in zip(rule['premises'], ids):
                bindings = bind(pattern, verified[pid]['atom'], bindings) if valid and pid in verified else None
                if bindings is None:
                    valid = False
                    break
            if valid:
                valid = (guards_pass(rule['guards'], bindings) and
                         key(instantiate(rule['conclusion'], bindings)) == key(proof['atom']) and
                         bindings == proof['bindings'])
        if valid:
            verified[proof['id']] = proof
        else:
            errors.append('无法重放推理：' + proof['id'])
    bindings = {b['id']: b for b in model['bindings']}
    for goal_id, traversal in report.get('graph_queries', {}).items():
        query = bindings.get(goal_id, {}).get('path_query')
        if not query or not verify_graph_query(model, query, traversal):
            errors.append('图路径无法重放：' + goal_id)
    return {'valid': not errors and report['search_complete'], 'errors': errors,
            'verified_steps': len(verified), 'note': '验证仅覆盖此模型的证据复核与推理步骤；不等同于全领域正确性证明。'}


def proof_dependencies(model, report, ids):
    proofs = {p['id']: p for p in report['proofs']}
    items = {i['id']: i for i in [*model['facts'], *model['rules']]}
    result, visited = {}, set()
    def visit(pid):
        if pid in visited:
            return
        visited.add(pid)
        if pid in items:
            result[pid] = items[pid]
        elif pid in proofs:
            p = proofs[pid]
            visit(p['fact_id'] or p['rule_id'])
            for parent in p['premise_ids']:
                visit(parent)
    for pid in ids:
        visit(pid)
    return list(result.values())


def _append_unique(target, values):
    for value in values:
        if value not in target:
            target.append(value)


def _proof_path(pid, proofs, items, memo, visiting=None):
    """Flatten a proof DAG into an auditable evidence path.

    Rules may have several conjunctive premises, so the path keeps the
    premise branches in one ordered list while retaining every proof step.
    This is deliberately a proof path rather than a claim that the rule is a
    simple binary graph edge.
    """
    if pid in memo:
        return memo[pid]
    visiting = set(visiting or ())
    if pid in visiting or pid not in proofs:
        return None
    proof = proofs[pid]
    parts = []
    for parent in proof.get('premise_ids', []):
        child = _proof_path(parent, proofs, items, memo, visiting | {pid})
        if child:
            parts.append(child)
    node_ids, edge_ids, proof_ids, evidence_ids, steps = [], [], [], [], []
    for part in parts:
        _append_unique(node_ids, part['node_ids'])
        _append_unique(edge_ids, part['edge_ids'])
        _append_unique(proof_ids, part['proof_ids'])
        _append_unique(evidence_ids, part['evidence_ids'])
        steps.extend(part['steps'])
    atom = proof['atom']
    element_id = proof.get('fact_id') or proof.get('rule_id')
    item = items.get(element_id, {})
    step = {
        'proof_id': pid, 'element_id': element_id,
        'kind': 'fact' if proof.get('fact_id') else 'rule',
        'source_id': atom['subject'], 'predicate': atom['predicate'],
        'target_id': atom['object'], 'context': atom['context'],
        'negative': atom.get('negative', False),
        'evidence': item.get('evidence', []),
    }
    steps.append(step)
    _append_unique(node_ids, [atom['subject'], atom['object']])
    _append_unique(edge_ids, [element_id])
    _append_unique(proof_ids, [pid])
    for evidence in item.get('evidence', []):
        _append_unique(evidence_ids, [evidence.get('passage_id')])
    path = {'node_ids': node_ids, 'edge_ids': edge_ids,
            'proof_ids': proof_ids, 'evidence_ids': evidence_ids,
            'steps': steps}
    memo[pid] = path
    return path


def build_proof_paths(model, report, *, max_paths=64):
    """Create bounded, replayable semantic paths for every goal result."""
    proofs = {p['id']: p for p in report.get('proofs', [])}
    items = {x['id']: x for x in [*model.get('facts', []), *model.get('rules', [])]}
    memo, paths, seen = {}, [], set()
    for result in report.get('results', []):
        for pid in result.get('proof_ids', []):
            path = _proof_path(pid, proofs, items, memo)
            if not path:
                continue
            identity = (result['goal_id'], tuple(path['proof_ids']))
            if identity in seen:
                continue
            seen.add(identity)
            path = dict(path)
            path['id'] = 'path_' + hashlib.sha256(
                json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:16]
            path['goal_id'] = result['goal_id']
            path['status'] = result.get('status', 'unknown')
            path['search_complete'] = report.get('search_complete', False)
            paths.append(path)
            if len(paths) >= max_paths:
                return paths
    return paths


def verify_paths(model, report):
    """Verify that each displayed path is composed of verified proof steps."""
    proofs = {p['id']: p for p in report.get('proofs', [])}
    elements = {x['id']: x for x in [*model.get('facts', []), *model.get('rules', [])]}
    errors = []
    if report.get('paths', []) != build_proof_paths(model, report):
        errors.append('展示的证明依赖与实际推理步骤不一致')
    verified = set()
    for path in report.get('paths', []):
        if not path.get('proof_ids'):
            errors.append('路径没有推理步骤：' + str(path.get('id')))
            continue
        valid = True
        for pid in path['proof_ids']:
            proof = proofs.get(pid)
            if not proof:
                valid = False
                errors.append('路径包含不存在的推理步骤：' + str(pid))
                continue
            element_id = proof.get('fact_id') or proof.get('rule_id')
            if element_id not in elements:
                valid = False
                errors.append('路径引用了不存在的模型元素：' + str(element_id))
        if valid:
            verified.add(path['id'])
    return {'valid': not errors and len(verified) == len(report.get('paths', [])),
            'verified_paths': len(verified), 'errors': errors}
