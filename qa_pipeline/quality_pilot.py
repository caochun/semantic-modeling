"""Evidence-first QA quality pilot; creates no training export or pass verdict.

Facts and question coverage are specified before rendering answers. Integrity
checks enforce traceability and declared coverage; semantic reviews are separate.
"""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import re

from .paths import ROOT, SOURCE, DATA, RUNS

DEFAULT = RUNS / 'quality-pilot'
SEED = DATA / 'batches/20261008/batch01/seed.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def norm(value):
    return re.sub(r'\s+', '', value)


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def jsonl(path, values):
    path.write_text(''.join(json.dumps(v, ensure_ascii=False) + '\n' for v in values))


def source_span(sources, selector):
    """Resolve explicit document locations without depending on old QA rows."""
    source = sources[selector['source_id']]
    block = next(b for b in source['blocks'] if b['id'] == selector['block_id'])
    start, end = selector['start'], selector['end']
    if (type(start) is not int or type(end) is not int or
            not 0 <= start < end <= len(block['text'])):
        raise ValueError('Invalid direct source offsets')
    if block['kind'] == 'legacy_table_linearized' or block.get('has_uninterpreted_object'):
        raise ValueError('Uninterpreted source structure requires review before QA generation')
    return {'id': f"{source['source_id']}/{block['id']}:{start}:{end}",
            'source_id': source['source_id'], 'block_id': block['id'],
            'start': start, 'end': end, 'text': block['text'][start:end],
            'locator': block['locator']}


def validate_record(row, chain):
    facts = {f['id']: f for f in chain['facts']}
    required = row['required_fact_ids']
    if not required or len(required) != len(set(required)) or not set(required) <= set(facts):
        raise ValueError(f"Invalid coverage plan: {row['id']}")
    segments = row['answer_segments']
    referenced = [f for segment in segments for f in segment['fact_ids']]
    if set(referenced) != set(required):
        raise ValueError(f"Answer segments omit or add declared facts: {row['id']}")
    indices = {fact: i for i, segment in enumerate(segments) for fact in segment['fact_ids']}
    for before, after in row['required_order_edges']:
        if indices[before] >= indices[after]:
            raise ValueError(f"Declared order violation: {row['id']}")
    if row['type'] == 'full_chain' and set(required) != set(facts):
        raise ValueError(f"Full-chain plan incomplete: {row['id']}")
    for segment in segments:
        if not segment['text'].strip() or not segment['fact_ids']:
            raise ValueError(f"Untraceable answer segment: {row['id']}")
    lines = [re.sub(r'^\d+[.、]\s*', '', line).strip() for line in row['answer'].splitlines()]
    if any(count >= 3 for line, count in Counter(lines).items() if len(line) >= 12):
        raise ValueError(f"Repeated answer lines: {row['id']}")


def render(directory, records, chains):
    by_id = {c['id']: c for c in chains}
    esc = html.escape
    cards = []
    for row in records:
        chain = by_id[row['business_unit_id']]
        facts = {f['id']: f for f in chain['facts']}
        details = ''.join('<li><b>' + esc(fid) + '</b> ' + esc(facts[fid]['text']) +
                          '<blockquote>' + esc(facts[fid]['source_quote']) + '</blockquote></li>'
                          for fid in row['required_fact_ids'])
        context = ''.join('<p><b>' + esc(s['id']) + '</b></p><pre>' + esc(s['text']) + '</pre>'
                          for s in chain['context_spans'])
        history = ''.join('<p>' + esc(m['role']) + '：' + esc(m['content']) + '</p>'
                          for m in row['history'])
        cards.append('<article><small>' + esc(row['id'] + ' · ' + row['type']) +
                     '</small><h2>' + esc(row['question']) + '</h2>' + history +
                     '<pre>' + esc(row['answer']) + '</pre><details><summary>本题必答事实与来源</summary><ol>' +
                     details + '</ol></details><details><summary>完整链路及关联原文</summary>' +
                     context + '</details></article>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>QA质量样本</title>
<style>body{max-width:1100px;margin:32px auto;padding:0 20px;font:16px/1.7 system-ui;color:#172334;background:#f5f7fa}article{background:white;padding:24px;margin:18px 0;border:1px solid #ccd4df;border-radius:10px}h2{font-size:19px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}summary{cursor:pointer;color:#235c91}blockquote{border-left:3px solid #c6d2df;padding-left:14px;color:#465569}</style>
<h1>QA质量样本</h1><p>CHAIN_COUNT个完整业务链路、QA_COUNT题。页面展示候选及原文，审核结论以review.md为准；暂不导出训练数据。</p>'''
    page = page.replace('CHAIN_COUNT', str(len(chains))).replace('QA_COUNT', str(len(records)))
    (directory / 'index.html').write_text(page + ''.join(cards) + '</html>')


def build(source, directory, seed=SEED):
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'manifest.json').exists():
        raise ValueError('Frozen quality pilot exists; choose a new output directory')
    old = read_rows(source / 'qa.jsonl')
    old_by_id = {r['id']: r for r in old}
    contexts = {c['id']: c for c in json.loads((source / 'contexts.json').read_text())}
    sources = {s['source_id']: s for s in
               [json.loads(p.read_text()) for p in (source / 'sources').glob('*.json')]}
    spec = json.loads(seed.read_text())
    chains, records, prompts = [], [], []
    for plan in spec['chains']:
        evidence, expanded = {}, {}
        for rid in plan.get('source_rows', []):
            row = old_by_id[rid]
            for span in row['evidence']:
                if plan.get('evidence_blocks') and span['block_id'] not in plan['evidence_blocks']:
                    continue
                evidence[span['id']] = span
            for span in contexts[row['context_id']]['spans']:
                expanded[span['id']] = span
        for selector in plan.get('source_spans', []):
            direct = source_span(sources, selector)
            evidence[direct['id']] = direct
        for selector in plan.get('context_source_spans', []):
            context = source_span(sources, selector)
            expanded[context['id']] = context
        expanded.update(evidence)
        if not evidence:
            raise ValueError('Business unit has no direct source evidence: ' + plan['id'])
        for span in expanded.values():
            block = next(b for b in sources[span['source_id']]['blocks'] if b['id'] == span['block_id'])
            if block['text'][span['start']:span['end']] != span['text']:
                raise ValueError('Source extraction offset changed')
        # Detect changed raw files, not only internally consistent extraction.
        for sid in {s['source_id'] for s in expanded.values()}:
            src = sources[sid]
            raw_path = ROOT / src['path']
            expected = src.get('sha256')
            if not expected or sha(raw_path) != expected:
                raise ValueError(f'Raw source fingerprint mismatch: {sid}')
        facts = []
        for fact in plan['facts']:
            matches = [s for s in evidence.values() if norm(fact['quote']) in norm(s['text'])]
            if len(matches) != 1:
                raise ValueError(f"Ambiguous/missing quote: {plan['id']}/{fact['id']}")
            span = matches[0]
            facts.append({'id': fact['id'], 'text': fact['text'], 'source_quote': fact['quote'],
                          'evidence_ids': [span['id']], 'source_id': span['source_id'],
                          'source_scope': sources[span['source_id']]['scope'],
                          'semantic_support_review': 'pending'})
        chain = {'id': plan['id'], 'title': plan['title'], 'scope': plan['scope'],
                 'facts': facts, 'order_edges': plan['order_edges'], 'branches': plan['branches'],
                 'direct_spans': list(evidence.values()), 'context_spans': list(expanded.values()),
                 'context_extent': 'existing_explicit_dependencies_not_exhaustive',
                 'source_currency': 'not_independently_certified'}
        if plan.get('source_unit_ids'):
            chain['source_unit_ids'] = plan['source_unit_ids']
            chain['context_extent'] = 'source_inventory_and_explicit_dependencies_not_exhaustive'
        chains.append(chain)
        fact_by_id = {f['id']: f for f in facts}
        for q in plan['questions']:
            required = q['required']
            segments = [{'text': fact_by_id[f]['text'], 'fact_ids': [f]} for f in required]
            body = '\n'.join(f'{i}. {s["text"]}' for i, s in enumerate(segments, 1))
            prefix = ('以下按适用条件选择分支，不是将所有分支依次执行。\n'
                      if q['type'] == 'full_chain' and plan['branches'] else '')
            answer = prefix + (q.get('lead', '') + '\n' if q.get('lead') else '') + body
            if q.get('tail'):
                answer += '\n' + q['tail']
            history = q.get('history', [])
            question = q['question'] if history else '按' + plan['scope'] + '，' + q['question']
            record = {'id': plan['id'] + '/' + q['key'], 'business_unit_id': plan['id'],
                      'scope': plan['scope'], 'type': q['type'], 'question': question,
                      'standalone_question': '按' + plan['scope'] + '，' + q.get('standalone_question', q['question']),
                      'history': history, 'answer': answer, 'answer_segments': segments,
                      'lead': q.get('lead', ''), 'tail': q.get('tail', ''),
                      'required_fact_ids': required,
                      'required_order_edges': [edge for edge in plan['order_edges'] if set(edge) <= set(required)],
                      'context_id': plan['id'], 'generation': 'evidence_fact_plan_to_natural_question',
                      'quality_status': 'semantic_review_pending', 'training_eligible': False}
            validate_record(record, chain)
            records.append(record)
            full_context = '\n\n'.join('[' + s['id'] + '] ' + sources[s['source_id']]['scope'] +
                                       '\n' + s['text'] for s in expanded.values())
            chat = [{'role': 'system', 'content': '仅依据完整文档及对话回答。保留条件、禁止项、顺序和恢复要求；分支按条件执行。未定义内容或适用范围不明应说明。引用证据标识，不补造事实。'}]
            # Baseline runner takes system/user only, so preserve history as explicit quoted dialogue.
            dialogue = '\n'.join(m['role'] + '：' + m['content'] for m in history)
            user = '完整文档与关联资料：\n' + full_context
            if dialogue:
                user += '\n\n既有对话：\n' + dialogue
            user += '\n\n当前问题：' + question
            prompts.append({'id': record['id'], 'context_id': plan['id'],
                            'messages': chat + [{'role': 'user', 'content': user}]})
    jsonl(directory / 'candidates.jsonl', records)
    save(directory / 'chains.json', chains)
    (directory / 'seed.json').write_bytes(seed.read_bytes())
    jsonl(directory / 'blind_prompts.jsonl', prompts)
    # Do not silently promote old structural checks or small sampled reviews.
    inventory = [{'id': r['id'], 'business_unit_id': r['chain_id'],
                  'generation': r['generation'], 'legacy_review': r['review'],
                  'quality_status': 'needs_evidence_fact_and_semantic_review',
                  'training_eligible': False} for r in old]
    jsonl(directory / 'legacy_quality_inventory.jsonl', inventory)
    manifest = {'version': spec['version'], 'qa': len(records), 'chains': len(chains),
                'legacy_candidates': len(old), 'legacy_training_eligible_under_new_gate': 0,
                'training_export_created': False, 'status': 'semantic_review_pending',
                'structure_checked': True, 'semantic_quality_passed': False,
                'builder_sha256': sha(Path(__file__)), 'seed_sha256': sha(seed),
                'source_qa_sha256': sha(source / 'qa.jsonl'),
                'files': {name: sha(directory / name) for name in
                          ['candidates.jsonl', 'chains.json', 'seed.json', 'blind_prompts.jsonl',
                           'legacy_quality_inventory.jsonl']}}
    save(directory / 'manifest.json', manifest)
    render(directory, records, chains)
    print(json.dumps({k: v for k, v in manifest.items() if k != 'files'}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    parser.add_argument('--seed', type=Path, default=SEED)
    args = parser.parse_args()
    build(args.source, args.output, args.seed)


if __name__ == '__main__':
    main()
