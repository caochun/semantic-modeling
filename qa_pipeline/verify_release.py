"""Check released QA, evidence, ordered steps, export parity and viewer links.

This is an artifact integrity check, not a semantic or expert certification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from .paths import ROOT, FOCUSED, TEMPLATE


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(text):
    return re.sub(r'\s+', '', text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT / '.semantic/qa_operations_20261008')
    parser.add_argument('--builder', type=Path, default=Path(__file__).with_name('build.py'),
                        help='Exact builder used by the release; historical releases need the archived builder')
    args = parser.parse_args()
    directory = args.dataset.resolve()
    qa = rows(directory / 'qa.jsonl')
    contexts = {c['id']: c for c in json.loads((directory / 'contexts.json').read_text())}
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert sha(directory / 'qa.jsonl') == manifest['qa_sha256']
    assert sha(args.builder) == manifest['builder_sha256'], 'Builder changed; supply the exact archived --builder'
    assert sha(FOCUSED) == manifest['focused_qa_sha256']
    assert sha(TEMPLATE) == manifest['viewer_template_sha256']
    assert len(qa) == manifest['qa']
    assert len({r['id'] for r in qa}) == len(qa)
    assert set(contexts) == {r['context_id'] for r in qa}
    sources = {s['source_id']: s for s in [json.loads(p.read_text()) for p in (directory / 'sources').glob('*.json')]}
    blocks = {sid: {b['id']: b for b in s['blocks']} for sid, s in sources.items()}
    evidence_checks, order_checks = 0, 0
    for context in contexts.values():
        assert context['truncation'] is False
        for e in context['spans']:
            assert e['text'] == blocks[e['source_id']][e['block_id']]['text'][e['start']:e['end']]
            evidence_checks += 1
    for r in qa:
        context_evidence = {e['id'] for e in contexts[r['context_id']]['spans']}
        assert set(r['source_ids']) == {e['source_id'] for e in r['evidence']}
        assert {e['id'] for e in r['evidence']} <= context_evidence
        answer, cursor = norm(r['answer']), 0
        for step in r['ordered_steps']:
            at = answer.find(norm(step['text']), cursor)
            assert at >= 0, (r['id'], step['source_clause'])
            cursor = at + len(norm(step['text']))
            order_checks += 1
        for check in r['answer_checklist']:
            assert norm(check['text']) in answer
    solution = [r for r in qa if r['family'] != 'operation_sequence']
    assert rows(directory / 'solution_qa.jsonl') == solution
    exports_checked = 0
    for kind in ['direct', 'context']:
        exported = rows(directory / ('sft_' + kind + '.jsonl'))
        filtered = rows(directory / ('sft_solution_' + kind + '.jsonl'))
        assert len(exported) == len(qa) and len(filtered) == len(solution)
        for r, record in zip(qa, exported):
            assert [m['role'] for m in record['messages']] == ['system', 'user', 'assistant']
            assert record['messages'][2]['content'] == r['answer']
            user = r['question'] if kind == 'direct' else '文档证据及关联上下文：\n\n' + contexts[r['context_id']]['text'] + '\n\n问题：' + r['question']
            assert record['messages'][1]['content'] == user
            exports_checked += 1
        assert filtered == [record for r, record in zip(qa, exported) if r['family'] != 'operation_sequence']
    units = json.loads((directory / 'coverage_units.json').read_text())
    held = json.loads((directory / 'held_units.json').read_text())
    assert len({u['id'] for u in units}) == len(units)
    assert {u['id'] for u in units if u['coverage_status'] == 'held'} == {h['unit_id'] for h in held}
    selection = json.loads((directory / 'source_selection.json').read_text())
    assert len(selection) == manifest['unique_documents']
    assert sum(s['qa_count'] > 0 for s in selection) == manifest['documents_used']
    html = (directory / 'index.html').read_text()
    payload = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)[1])
    assert [r['id'] for r in payload['rows']] == [r['id'] for r in qa]
    for r, display in zip(qa, payload['rows']):
        assert display['question'] == r['question'] and display['answer'] == r['answer']
        assert display['review'] == r['review']
        assert all(sid in payload['spans'] for sid in display['evidence_ids'])
        assert all(sid in payload['spans'] for sid in payload['contexts'][r['context_id']]['span_ids'])
    result = {'status': 'passed', 'qa': len(qa), 'solution_qa': len(solution),
              'source_span_checks': evidence_checks, 'ordered_step_checks': order_checks,
              'training_export_rows_checked': exports_checked,
              'viewer_question_answer_parity': True, 'held_units_consistent': True,
              'semantic_quality_certified': False, 'expert_review': 'not_performed'}
    (directory / 'release_checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
