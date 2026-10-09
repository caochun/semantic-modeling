"""Combine frozen, source-reviewed QA batches for reading, without SFT export."""
import argparse
from collections import Counter
import json
from pathlib import Path

from .coverage_ledger import load_reviewed
from .quality_pilot import ROOT, digest, jsonl, render, save, sha
from .snapshots import namespace


def build(datasets, output):
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'manifest.json').exists():
        raise ValueError('Frozen corpus exists; choose a new output directory')
    candidates, chains, reviews, bindings = [], [], [], []
    for directory in datasets:
        rows, source_chains = load_reviewed(directory)
        source_reviews = json.loads((directory / 'source_reviews.json').read_text())
        by_id = {r['id']: r for r in source_reviews['rows']}
        prefix = namespace(directory) + ':'
        bindings.append({'directory': str(directory.relative_to(ROOT)),
                         'manifest_sha256': sha(directory / 'manifest.json'),
                         'source_reviews_sha256': sha(directory / 'source_reviews.json')})
        for chain in source_chains:
            chains.append({**chain, 'id': prefix + chain['id'],
                           'origin_dataset': str(directory.relative_to(ROOT))})
        for row in rows:
            record = {**row, 'id': prefix + row['id'],
                      'business_unit_id': prefix + row['business_unit_id'],
                      'context_id': prefix + row['context_id'],
                      'quality_status': 'document_fidelity_reviewed_by_ai',
                      'origin': {'dataset': str(directory.relative_to(ROOT)), 'id': row['id'],
                                 'candidate_sha256': digest(row)},
                      'training_eligible': False}
            candidates.append(record)
            reviews.append({**by_id[row['id']], 'id': record['id'],
                            'candidate_sha256': digest(record),
                            'origin_review_sha256': digest(by_id[row['id']])})
    if len({r['id'] for r in candidates}) != len(candidates):
        raise ValueError('Duplicate source batch included')
    jsonl(output / 'qa.jsonl', candidates)
    save(output / 'chains.json', chains)
    save(output / 'source_reviews.json', {'reviewer': 'Codex（AI来源核对，非业务专家）',
         'bound_files': {name: sha(output / name) for name in ['qa.jsonl', 'chains.json']}, 'rows': reviews})
    manifest = {'qa': len(candidates), 'business_units': len(chains),
                'question_types': dict(Counter(r['type'] for r in candidates)),
                'builder_sha256': sha(Path(__file__)), 'batches': bindings,
                'files': {name: sha(output / name) for name in ['qa.jsonl', 'chains.json', 'source_reviews.json']},
                'training_eligible': False, 'training_export_created': False,
                'expert_review': 'not_performed', 'global_coverage_rate': None}
    save(output / 'manifest.json', manifest)
    render(output, candidates, chains)
    page = (output / 'index.html').read_text().replace(
        f'{len(chains)}个完整业务链路、{len(candidates)}题。',
        f'{len(chains)}个业务单元（含公共规则与处置链路）、{len(candidates)}题。')
    search = '''<label>搜索问答 <input id="search" placeholder="设备、情景或关键词" style="padding:8px;font:inherit"></label><p id="count"></p>
<script>document.getElementById('search').addEventListener('input',function(){const term=this.value.toLowerCase();let n=0;document.querySelectorAll('article').forEach(a=>{const show=a.innerText.toLowerCase().includes(term);a.hidden=!show;if(show)n++});document.getElementById('count').textContent='匹配 '+n+' 条问答'});</script>'''
    (output / 'index.html').write_text(page.replace('<h1>QA质量样本</h1>', '<h1>已复核 QA 汇总</h1>' + search))
    (output / 'review.md').write_text('# 已复核 QA 汇总\n\n'
        f'{len(candidates)}条问答，{len(chains)}个业务单元。各题通过的是所列文档忠实性复核，'
        '仍未认证来源现行性或全库覆盖，也未导出新训练文件。\n\n'
        '汇总QA保留原批次、原ID、原候选摘要及逐题来源核对记录；原始事实核验和复答均保留在各批次。\n\n' +
        '\n'.join('- [' + d.name + '](../' + d.name + '/review.md)' for d in datasets) + '\n')
    print(json.dumps(manifest, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build([p.resolve() for p in args.dataset], args.output)


if __name__ == '__main__':
    main()
