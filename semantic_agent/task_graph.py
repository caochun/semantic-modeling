"""Directed, context-preserving graph queries. Connectivity is not causation."""
from collections import defaultdict, deque
import hashlib
import json

from .task_model import build_graph


def graph_paths(model, query, *, max_paths=64, max_work=4000):
    graph = build_graph(model)
    by_source = defaultdict(list)
    negative = {(e['source_id'], e['predicate'], e['target_id'], e['context'])
                for e in graph['edges'] if e['negative'] and e['status'] == 'supported'}
    for edge in graph['edges']:
        identity = (edge['source_id'], edge['predicate'], edge['target_id'], edge['context'])
        if edge['status'] == 'supported' and not edge['negative'] and edge['context'] == query['context'] and identity not in negative:
            by_source[edge['source_id']].append(edge)
    queue = deque([([query['source_id']], [])])
    paths, frontier, work = [], [], 0
    predicates = query.get('predicates', [])
    max_hops = len(predicates) or query['max_hops']
    complete = True
    while queue:
        node_ids, edges = queue.popleft()
        if len(edges) >= max_hops:
            continue
        next_edges = by_source[node_ids[-1]]
        if predicates:
            next_edges = [e for e in next_edges if e['predicate'] == predicates[len(edges)]]
        if not next_edges:
            frontier.append({'node_id': node_ids[-1], 'predicate': predicates[len(edges)] if predicates else '',
                             'context': query['context'], 'edge_ids': [e['id'] for e in edges]})
        for edge in next_edges:
            work += 1
            if work > max_work or len(paths) >= max_paths:
                complete = False
                break
            if edge['target_id'] in node_ids:
                continue
            extended = [*edges, edge]
            extended_nodes = [*node_ids, edge['target_id']]
            endpoint_ok = not query.get('target_id') or edge['target_id'] == query['target_id']
            depth_ok = not predicates or len(extended) == len(predicates)
            if endpoint_ok and depth_ok:
                edge_ids = [e['id'] for e in extended]
                pid = 'graph_path_' + hashlib.sha256(json.dumps([model['version'], edge_ids]).encode()).hexdigest()[:16]
                paths.append({'id': pid, 'kind': 'graph', 'node_ids': extended_nodes,
                              'edge_ids': edge_ids, 'context': query['context'], 'status': 'supported',
                              'evidence': [ev for e in extended for ev in e['evidence']],
                              'steps': extended, 'model_version': model['version']})
            if len(extended) < max_hops:
                queue.append((extended_nodes, extended))
        if not complete:
            break
    return {'paths': paths, 'search_complete': complete, 'operations': work,
            'frontier': frontier, 'note': '路径仅证明所列关系链存在；因果或异常结论需要另有规则。'}


def verify_graph_query(model, query, report):
    # Re-run traversal from immutable facts: validates direction, predicates,
    # scope, provenance, end points and completeness, not merely edge existence.
    return report == graph_paths(model, query)


def search_graph(model, query):
    """Concepts connect retrieval targets without becoming inference premises."""
    words = query.casefold().split()
    labels = {n['id']: n for n in model['nodes']}
    matches = {n['id'] for n in model['nodes'] if any(w in (n['label'] + n['description']).casefold() for w in words)}
    links = [l for l in model.get('concept_links', []) if l.get('status') == 'supported']
    concept_ids = {labels[i]['id'] for i in matches if labels[i]['kind'] == 'concept'}
    concept_ids.update(l['concept_id'] for l in links if l['element_id'] in matches)
    matches.update(l['element_id'] for l in links if l['concept_id'] in concept_ids)
    edges = [e for e in build_graph(model)['edges'] if e['id'] in matches or e['source_id'] in matches or e['target_id'] in matches]
    return {'nodes': [labels[i] for i in matches if i in labels], 'edges': edges,
            'concept_links': [l for l in links if l['concept_id'] in concept_ids],
            'gaps': [g for g in model['gaps'] if g['status'] == 'open'],
            'note': '概念化帮助发现相关关系；不自动合并实体，不证明概念内所有实例共享性质。'}
