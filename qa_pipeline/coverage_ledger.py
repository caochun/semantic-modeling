"""Inventory sources before linking QA; never infer coverage from context size.

Numbered clauses and Word paragraphs are source candidates, not a certified
denominator of valuable business facts. Uninterpreted material stays visible.
"""
import argparse
from collections import Counter, defaultdict
import html
import json
from pathlib import Path
import re

from .quality_pilot import ROOT, SOURCE, digest, norm, read_rows, save, sha
from .verify_quality import verify_source_reviews
from .snapshots import namespace, verify as verify_snapshot

PDF = 'd_98d56bcdf4e49412'
WORD = 'd_4b964d3ff677714b'
NUMBER = re.compile(r'(?m)^[ \t]*(\d+(?:\.\d+)*)[ \t]+(\S[^\n]*)')


def span(source, block, start=0, end=None):
    end = len(block['text']) if end is None else end
    return {'source_id': source['source_id'], 'block_id': block['id'],
            'start': start, 'end': end, 'text': block['text'][start:end],
            'locator': block['locator']}


def unit(source, key, kind, spans, **extra):
    return {'id': source['source_id'] + '/' + key, 'source_id': source['source_id'],
            'kind': kind, 'spans': spans, 'text': '\n'.join(s['text'] for s in spans),
            'value_status': 'pending_business_value_review',
            'qa_coverage': 'missing_reviewed_qa', 'fact_links': [], **extra}


def pdf_catalog(source, body_start=11):
    """Retain every page, including unnumbered business text and continuations."""
    units, sections, current = [], [], None
    occurrences = Counter()
    toc = []
    for block in source['blocks']:
        page = int(block['id'].removeprefix('page'))
        if page < body_start:
            kind = ('front_matter' if page <= 4 else
                    'preface_business_candidate' if page == 5 else 'table_of_contents')
            units.append(unit(source, block['id'], kind, [span(source, block)],
                              qa_coverage='not_applicable' if kind != 'preface_business_candidate'
                              else 'missing_reviewed_qa',
                              value_status='metadata_retained' if kind != 'preface_business_candidate'
                              else 'pending_business_value_review'))
            if kind == 'table_of_contents':
                toc.extend((m.group(1), m.group(2).strip()) for m in re.finditer(
                    r'(?m)^\s*(\d+(?:\.\d+)*)\s+(.+?)\s*\.{3,}\s*\d+\s*$', block['text']))
            continue
        matches = list(NUMBER.finditer(block['text']))
        first = matches[0].start() if matches else len(block['text'])
        if first:
            segment = span(source, block, 0, first)
            if current is not None:
                current['spans'].append(segment)
            elif segment['text'].strip():
                units.append(unit(source, block['id'] + '/unnumbered',
                                  'unassigned_body_text', [segment]))
        for i, match in enumerate(matches):
            number = match.group(1)
            occurrences[number] += 1
            suffix = '' if occurrences[number] == 1 else '#' + str(occurrences[number])
            current = unit(source, 'clause/' + number + suffix, 'numbered_clause',
                           [span(source, block, match.start(),
                                 matches[i + 1].start() if i + 1 < len(matches)
                                 else len(block['text']))], number=number,
                           section_id='.'.join(number.split('.')[:2]),
                           outline_depth=len(number.split('.')))
            units.append(current)
    numbers = {u['number'] for u in units if 'number' in u}
    for item in units:
        if 'number' not in item:
            continue
        item['text'] = '\n'.join(s['text'] for s in item['spans'])
        item['has_numbered_children'] = any(n.startswith(item['number'] + '.') for n in numbers)
        # Heading containers remain in the catalog. No automatic business-value exclusion.
        item['kind'] = 'numbered_container' if item['has_numbered_children'] else 'numbered_clause'
    for number, title in toc:
        sections.append({'source_id': source['source_id'], 'number': number, 'title': title,
                         'body_heading_found': number in numbers})
    return units, sections


def word_catalog(source):
    """Use the document TOC and ALL body blocks, not an existing QA selector."""
    blocks = source['blocks']
    start = next(i for i, b in enumerate(blocks)
                 if b['kind'] == 'paragraph' and norm(b['text']) == '范围')
    preface = next((i for i, b in enumerate(blocks[:start]) if norm(b['text']) == '前言'), start)
    toc_start = next((i for i, b in enumerate(blocks[:start]) if b['kind'] == 'toc'), start)
    toc = []
    for b in blocks[:start]:
        text = b['text'].strip()
        match = re.match(r'^(\d+(?:\.\d+)*)\s+(.+?)\t\d+$', text)
        if match:
            toc.append((match.group(1), match.group(2)))
        else:
            match = re.match(r'^附录\s*([A-Z])\s+(.+?)\t\d+$', text)
            if match:
                toc.append(('附录' + match.group(1), '附录' + match.group(1) + ' ' + match.group(2)))
    positions, sections, cursor = {}, [], start
    for number, title in toc:
        index = next((i for i in range(cursor, len(blocks))
                      if blocks[i]['kind'] == 'paragraph' and norm(blocks[i]['text']) == norm(title)), None)
        sections.append({'source_id': source['source_id'], 'number': number, 'title': title,
                         'body_heading_found': index is not None})
        if index is not None:
            positions[index] = number
            cursor = index + 1
    units, section = [], 'unassigned'
    for i, block in enumerate(blocks):
        if i < start:
            kind = ('table_of_contents' if block['kind'] == 'toc' or norm(block['text']) == '目录'
                    else 'preface_business_candidate' if preface <= i < toc_start
                    else 'front_matter')
            business = kind == 'preface_business_candidate'
            units.append(unit(source, block['id'], kind, [span(source, block)],
                              value_status='pending_business_value_review' if business else 'metadata_retained',
                              qa_coverage='missing_reviewed_qa' if business else 'not_applicable'))
            continue
        section = positions.get(i, section)
        kind = ('uninterpreted_table_cell' if block['kind'] == 'legacy_table_linearized'
                else 'body_paragraph')
        if block.get('has_uninterpreted_object'):
            kind = 'uninterpreted_object_paragraph'
        units.append(unit(source, block['id'], kind, [span(source, block)],
                          section_id=section, heading_block=i in positions,
                          structure_status='automatic_numbering_not_reconstructed'))
    return units, sections


def quote_matches(unit_, fact, evidence):
    """A quote overlap is a navigation link, never complete semantic coverage."""
    if unit_['source_id'] != fact['source_id']:
        return False
    quote = norm(fact['source_quote'])
    return any(s['block_id'] == e['block_id'] and s['start'] < e['end']
               and e['start'] < s['end'] and quote in norm(s['text'])
               for s in unit_['spans'] for e in evidence
               if e['id'] in fact['evidence_ids'])


def apply_assessment(item, assessment, facts):
    if item['kind'].startswith('uninterpreted_'):
        raise ValueError('Uninterpreted structure cannot be marked covered')
    if assessment['unit_sha256'] != digest({k: item[k] for k in ['id', 'text', 'spans']}):
        raise ValueError('Stale source-unit assessment: ' + item['id'])
    keys = assessment['fact_keys']
    if not keys or any(key not in facts for key in keys):
        raise ValueError('Unreviewed fact used in coverage assessment')
    if assessment['facts_sha256'] != digest([facts[k] for k in keys]):
        raise ValueError('Stale fact assessment: ' + item['id'])
    checks = {'all_propositions', 'conditions', 'permissions_prohibitions',
              'order_outcomes_restoration', 'scope_numbers'}
    if set(assessment['checks']) != checks or any(v != 'pass' for v in assessment['checks'].values()):
        raise ValueError('Partial or uncertain clause cannot be marked covered')
    if not assessment['notes'].strip():
        raise ValueError('Missing semantic coverage rationale')
    if any(not any(link['key'] == key for link in item['fact_links']) for key in keys):
        raise ValueError('Coverage facts have no precise source quote in this unit')
    item['value_status'] = 'business_information_reviewed'
    item['qa_coverage'] = 'reviewed_against_extracted_unit'
    item['coverage_assessment'] = assessment


def load_reviewed(directory):
    if (directory / 'snapshot.json').is_file():
        result = verify_snapshot(directory)
        if result['kind'] != 'qa_batch_snapshot':
            raise ValueError('Expected a batch snapshot, not a release')
        return (read_rows(directory / 'candidates.jsonl'),
                json.loads((directory / 'chains.json').read_text()))
    manifest = json.loads((directory / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        if sha(directory / name) != expected:
            raise ValueError('Frozen QA input changed: ' + name)
    rows = read_rows(directory / 'candidates.jsonl')
    reviews = json.loads((directory / 'source_reviews.json').read_text())
    for name, expected in reviews['bound_files'].items():
        if sha(directory / name) != expected:
            raise ValueError('Bound QA review changed: ' + name)
    verify_source_reviews(rows, reviews)
    return rows, json.loads((directory / 'chains.json').read_text())


def render(output, units, registry, stats):
    # The source text and statuses are fully searchable offline. HTML escapes script delimiters.
    data = [{'id': u['id'], 'kind': u['kind'], 'section': u.get('section_id', ''),
             'status': u['qa_coverage'], 'text': u['text'],
             'qa': sorted({q for link in u['fact_links'] for q in link['qa_ids']}),
             'note': u.get('coverage_assessment', {}).get('notes', '')} for u in units]
    encoded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    page = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>文档业务覆盖台账</title>
<style>body{max-width:1100px;margin:28px auto;padding:0 20px;font:16px/1.7 system-ui;color:#182638}input,select{font:inherit;padding:8px;margin:8px 6px 8px 0}article{padding:16px;margin:12px 0;border:1px solid #ccd6e1;border-radius:8px}pre{font:inherit;white-space:pre-wrap}small{color:#536577}button{padding:8px}summary{cursor:pointer}</style>
<h1>文档业务覆盖台账</h1><p>SUMMARY</p>
<p>条款/段落是待解释的来源单元，不是“全部有价值事实”的已认证分母。短引文命中只显示部分关联；只有另行逐条审阅的单元才标记已核对。图表、编号与未盘点文档仍是缺口。</p>
<input id="search" placeholder="搜索条款、设备或原文"><select id="status"><option value="">全部状态</option><option value="missing_reviewed_qa">缺少复核QA</option><option value="reviewed_fact_quote_overlap_partial">仅部分事实关联</option><option value="reviewed_against_extracted_unit">已逐条核对文本覆盖</option><option value="not_applicable">目录与元信息</option></select>
<select id="source"><option value="">全部文档</option><option value="d_98d56bcdf4e49412">第五分册</option><option value="d_4b964d3ff677714b">第三分册</option></select>
<p id="count"></p><main id="rows"></main><button id="more">显示更多</button>
<script>const data=DATA;let limit=30;const labels={missing_reviewed_qa:'缺少复核QA',reviewed_fact_quote_overlap_partial:'仅部分事实关联，未确认完整覆盖',reviewed_against_extracted_unit:'已逐条核对提取文本覆盖',not_applicable:'目录/元信息保留'};
const el=id=>document.getElementById(id);function paint(){let term=el('search').value.toLowerCase(),state=el('status').value,source=el('source').value;let found=data.filter(x=>(!state||x.status===state)&&(!source||x.id.startsWith(source))&&(!term||(x.id+' '+x.section+' '+x.text).toLowerCase().includes(term)));el('count').textContent='匹配 '+found.length+' 项，展示 '+Math.min(limit,found.length)+' 项';el('rows').replaceChildren();for(let x of found.slice(0,limit)){let a=document.createElement('article'),s=document.createElement('small'),p=document.createElement('p'),d=document.createElement('details'),t=document.createElement('summary'),pre=document.createElement('pre');s.textContent=x.id+' · '+x.kind;p.textContent=labels[x.status]+(x.qa.length?'；QA：'+x.qa.join('，'):'');t.textContent='展开原文与覆盖核对说明';pre.textContent=x.text+(x.note?'\n\n核对说明：'+x.note:'');d.append(t,pre);a.append(s,p,d);el('rows').append(a)}}for(let id of ['search','status','source'])el(id).addEventListener('input',()=>{limit=30;paint()});el('more').onclick=()=>{limit+=30;paint()};paint();</script></html>'''
    summary = (f"登记{len(registry)}份去重业务文档；本次独立盘点2份来源。"
               f"逐题复核QA {stats['reviewed_qa']} 条；"
               f"逐条核对提取文本覆盖 {stats['unit_coverage'].get('reviewed_against_extracted_unit', 0)} 项。"
               '这些数字不能解释为全库知识覆盖率。')
    (output / 'index.html').write_text(page.replace('SUMMARY', html.escape(summary)).replace('DATA', encoded))


def preface_findings(source):
    """Keep title/preface mismatches tied to their actual source identity."""
    if '设备概况' in source['scope']:
        return []
    return [{'block_id': b['id'], 'finding': 'preface_self_description_conflicts_with_volume_title',
             'note': '前言称本册为设备概况分册，与' + source['scope'] +
                     '标题不一致，待核实来源。'}
            for b in source['blocks'] if '本册为设备概况分册' in b['text']]


def build(source, output, datasets, assessments_path=None):
    output.mkdir(parents=True, exist_ok=True)
    extracted = {p.stem: json.loads(p.read_text()) for p in (source / 'sources').glob('*.json')}
    units, sections = [], []
    for sid, parser in [(PDF, pdf_catalog), (WORD, word_catalog)]:
        src = extracted[sid]
        if sha(ROOT / src['path']) != src['sha256']:
            raise ValueError('Raw source changed: ' + sid)
        items, outline = parser(src)
        units.extend(items)
        sections.extend(outline)
    by_block = defaultdict(list)
    for item in units:
        for s in item['spans']:
            by_block[(s['source_id'], s['block_id'])].append(item)
    facts, dataset_bindings, chain_rows = {}, [], []
    reviewed_qa_by_source = defaultdict(set)
    qa_count = 0
    for directory in datasets:
        rows, chains = load_reviewed(directory)
        qa_count += len(rows)
        dataset_name = namespace(directory)
        dataset_bindings.append({'directory': str(directory.relative_to(ROOT)),
                                 'manifest_sha256': sha(directory / 'manifest.json'),
                                 'source_reviews_sha256': sha(directory / 'source_reviews.json')})
        for chain in chains:
            chain_questions = [r for r in rows if r['business_unit_id'] == chain['id']]
            chain_rows.append({'dataset': dataset_name, 'chain': chain['id'], 'title': chain['title'],
                               'qa': len(chain_questions), 'full_chain_qa': [r['id'] for r in chain_questions
                                                                          if r['type'] == 'full_chain'],
                               'closure': 'reviewed_declared_plan_not_entire_document',
                               'scope': chain['scope']})
            for fact in chain['facts']:
                key = dataset_name + ':' + chain['id'] + '/' + fact['id']
                qids = [r['id'] for r in chain_questions if fact['id'] in r['required_fact_ids']]
                if not qids:
                    continue
                facts[key] = fact
                reviewed_qa_by_source[fact['source_id']].update(dataset_name + ':' + q for q in qids)
                linked = set()
                for e in chain['direct_spans']:
                    if e['id'] not in fact['evidence_ids']:
                        continue
                    source_block = next(b for b in extracted[e['source_id']]['blocks'] if b['id'] == e['block_id'])
                    if source_block['text'][e['start']:e['end']] != e['text']:
                        raise ValueError('Reviewed source span changed: ' + e['id'])
                    for item in by_block[(e['source_id'], e['block_id'])]:
                        if item['id'] not in linked and quote_matches(item, fact, chain['direct_spans']):
                            item['fact_links'].append({'key': key, 'qa_ids': qids,
                                                      'link_basis': 'precise_quote_overlap_only'})
                            item['qa_coverage'] = 'reviewed_fact_quote_overlap_partial'
                            linked.add(item['id'])
    by_id = {u['id']: u for u in units}
    assessment_paths = ([] if assessments_path is None else
                        [assessments_path] if isinstance(assessments_path, Path) else assessments_path)
    assessments = [row for path in assessment_paths for row in json.loads(path.read_text())['rows']]
    if len({a['unit_id'] for a in assessments}) != len(assessments):
        raise ValueError('Duplicate coverage assessments; resolve their review provenance explicitly')
    for assessment in assessments:
        apply_assessment(by_id[assessment['unit_id']], assessment, facts)
    registry = json.loads((source / 'source_selection.json').read_text())
    catalog_findings = {sid: preface_findings(extracted[sid]) for sid in [PDF, WORD]}
    reviewed_sources = {f['source_id'] for f in facts.values()}
    for sid in reviewed_sources:
        if sha(ROOT / extracted[sid]['path']) != extracted[sid]['sha256']:
            raise ValueError('Reviewed raw source changed: ' + sid)
    linked_keys = {link['key'] for item in units for link in item['fact_links']}
    unlinked = [{'key': key, 'source_id': fact['source_id'], 'source_quote': fact['source_quote'],
                 'reason': 'source_not_independently_cataloged' if fact['source_id'] not in {PDF, WORD}
                 else 'precise_quote_mapping_requires_review'}
                for key, fact in facts.items() if key not in linked_keys]
    for record in registry:
        sid = record['source_id']
        record['legacy_reported_qa_count'] = record.pop('qa_count', 0)
        record['reviewed_qa_count'] = len(reviewed_qa_by_source[sid])
        record['extracted_artifact_available'] = sid in extracted
        record['independent_catalog_available'] = sid in {PDF, WORD}
        record['reviewed_qa_fact_evidence_present'] = sid in reviewed_sources
        record['coverage_state'] = ('partial_catalog_pending_semantics_and_structure' if sid in {PDF, WORD}
                                    else 'independent_business_catalog_not_built')
        record['interpretation_issues'] = extracted.get(sid, {}).get('issues', [])
        record['source_metrics'] = extracted.get(sid, {}).get('metrics', {})
        record['independent_catalog_findings'] = catalog_findings.get(sid, [])
    per_source = []
    for sid in [PDF, WORD]:
        items = [u for u in units if u['source_id'] == sid]
        outline = [s for s in sections if s['source_id'] == sid]
        per_source.append({'source_id': sid, 'scope': extracted[sid]['scope'], 'units': len(items),
                           'kinds': dict(Counter(u['kind'] for u in items)),
                           'unit_coverage': dict(Counter(u['qa_coverage'] for u in items)),
                           'toc_entries': len(outline),
                           'toc_missing_body_heading': [s for s in outline if not s['body_heading_found']],
                           'unresolved_source_issues': extracted[sid]['issues'],
                           'independent_catalog_findings': catalog_findings.get(sid, [])})
    stats = {'registered_documents': len(registry), 'available_extracted_documents': len(extracted),
             'independently_cataloged_documents': 2, 'documents_with_reviewed_fact_evidence': len(reviewed_sources),
             'reviewed_qa': qa_count, 'reviewed_chain_plans': len(chain_rows),
             'fact_plan_entries': len(facts), 'fact_entries_are_not_deduplicated_knowledge': True,
             'source_units': len(units), 'unit_coverage': dict(Counter(u['qa_coverage'] for u in units)),
             'unlinked_reviewed_fact_entries': len(unlinked),
             'business_fact_denominator_status': 'not_established', 'global_coverage_rate': None,
             'source_structure_completeness_certified': False, 'expert_review': 'not_performed',
             'sources': per_source}
    save(output / 'source_registry.json', registry)
    save(output / 'source_units.json', units)
    save(output / 'source_sections.json', sections)
    save(output / 'reviewed_facts.json', facts)
    save(output / 'unlinked_reviewed_facts.json', unlinked)
    save(output / 'chain_coverage.json', chain_rows)
    save(output / 'summary.json', stats)
    save(output / 'manifest.json', {'builder_sha256': sha(Path(__file__)),
                                  'source_registry_sha256': sha(source / 'source_selection.json'),
                                  'source_artifacts': {sid: sha(source / 'sources' / (sid + '.json'))
                                                       for sid in sorted({PDF, WORD} | reviewed_sources)},
                                  'raw_sources_sha256': {sid: extracted[sid]['sha256']
                                                         for sid in sorted({PDF, WORD} | reviewed_sources)},
                                  'reviewed_datasets': dataset_bindings,
                                  'assessment_files': {str(path.resolve().relative_to(ROOT)): sha(path)
                                                       for path in assessment_paths},
                                  'files': {n: sha(output / n) for n in ['source_registry.json', 'source_units.json',
                                            'source_sections.json', 'reviewed_facts.json', 'unlinked_reviewed_facts.json',
                                            'chain_coverage.json', 'summary.json']}})
    render(output, units, registry, stats)
    lines = ['# 文档业务信息覆盖台账', '',
             f'登记 {len(registry)} 份去重业务文档，已有提取产物 {len(extracted)} 份，本次独立盘点 2 份。', '',
             f'逐题复核 QA {qa_count} 条，{len(chain_rows)} 个链路规划，{len(facts)} 个事实规划条目（未按知识去重）。', '',
             '**当前不能给出全库知识覆盖率：业务事实分母尚未完成，图表与编号结构仍有未解释项。**', '',
             '## 状态的含义', '',
             '- `missing_reviewed_qa`：尚未关联逐题复核的 QA，不代表旧候选不存在。',
             '- `reviewed_fact_quote_overlap_partial`：已复核事实的短引文触及此条，尚未逐条确认该来源单元全部命题已覆盖。',
             '- `reviewed_against_extracted_unit`：额外按整条提取文本核对全部命题、条件、权限、顺序、结果与数值；不能据此认证原文件图表完整或来源现行有效。',
             '- `not_applicable`：封面、目录等元信息仍留在台账；前言中的业务参数未排除。', '',
             '## 来源盘点', '']
    for row in per_source:
        lines.extend([f"- {row['scope']}：{row['units']} 个来源片段；目录 {row['toc_entries']} 项，"
                      f"未匹配正文标题 {len(row['toc_missing_body_heading'])} 项。",
                      '  状态：' + json.dumps(row['unit_coverage'], ensure_ascii=False),
                      '  结构缺口：' + '；'.join(row['unresolved_source_issues'])])
        for finding in row['independent_catalog_findings']:
            lines.append('  独立盘点疑点：' + finding['block_id'] + '，' + finding['note'])
    lines.extend(['', '原文单元由源文档直接盘点，未使用旧 QA、coverage_units 或旧出题选择器构造分母。',
                  '两种解析粒度不同，不把 PDF 条款数与 Word 表格单元格数相加算业务覆盖率。',
                  '暂缓、未解释、尚未盘点文档不从全库边界删除。历史文件与其他站点只提供限定范围的证据。', '',
                  '[离线搜索页面](index.html) · [来源登记](source_registry.json) · [全量条款/段落](source_units.json) · '
                  '[章节目录](source_sections.json) · [统计](summary.json)'])
    (output / 'README.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(stats, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, action='append', required=True)
    parser.add_argument('--assessments', type=Path, action='append')
    args = parser.parse_args()
    build(args.source, args.output, [d.resolve() for d in args.dataset], args.assessments)


if __name__ == '__main__':
    main()
