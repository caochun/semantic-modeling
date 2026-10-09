"""Build source-grounded HVDC operations QA candidates without changing rawdoc.

Use the existing inventory/extraction audit, verify source fingerprints, recover
legacy Word text via its native piece table, and retain complete procedure units.
No model-generated operational facts or invented incident outcomes are added.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile

from .paths import ROOT, FOCUSED, TEMPLATE
AUDIT = ROOT / '.semantic/corpus_audit_20261007'
DEFAULT_OUT = ROOT / '.semantic/qa_operations_20261008'
REG5 = 'd_98d56bcdf4e49412'
REG3 = 'd_4b964d3ff677714b'
OVERVIEW = 'd_c4cb0963bdc8968a'
CASES = 'd_6db5fdecb63b02ef'
REPORT = 'd_784fd46ed9b06a57'
TRIPS = 'd_8da81a55b3d575de'
TICKETS = ['d_f0746ca20dd09b8b', 'd_a028bad21bde4c37',
           'd_a31193a6bbeecc88', 'd_8df0ccefda82f0ea',
           'd_61779ce69f242c03', 'd_eec2b92502a92c10']
SYSTEM = ('你根据指定站点、设备和版本的文档回答直流站运行问题。'
          '保留文档中的适用条件、步骤顺序、禁止事项、分支及恢复确认。'
          '区分历史案例、厂家说明和运行规程。文档未说明的内容不得补造；'
          '来源冲突或条件不足时明确说明缺口。')


def norm(s):
    return re.sub(r'\s+', '', s)


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def jsonl(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


def field_result(raw):
    """Retain nested Word field results, not their instructions."""
    stack, result = [], []
    for ch in raw:
        if ch == '\x13':
            stack.append(False)
        elif ch == '\x14':
            if not stack:
                raise ValueError('Unmatched field separator')
            stack[-1] = True
        elif ch == '\x15':
            if not stack:
                raise ValueError('Unmatched field end')
            stack.pop()
        elif all(stack):
            result.append(ch)
    if stack:
        raise ValueError('Unclosed Word field')
    return ''.join(result)


def word_text(path):
    """Read the main story using [MS-DOC] Fib/Clx/Pcd CP order.

    Paragraph numbering, styles, figures and table layout are NOT reconstructed.
    CP positions refer to the Word main story, not printed pages or text lines.
    Fail closed on encryption, unsupported structure or ambiguous decoding.
    """
    import olefile
    with olefile.OleFileIO(path) as ole:
        w = ole.openstream('WordDocument').read()
        ident, nfib = struct.unpack_from('<HH', w, 0)
        if ident != 0xA5EC or nfib < 0xC1:
            raise ValueError('Unsupported Word binary FIB')
        flags = struct.unpack_from('<H', w, 10)[0]
        if flags & (0x0100 | 0x8000):
            raise ValueError('Encrypted/obfuscated Word document')
        table = ole.openstream('1Table' if flags & 0x0200 else '0Table').read()
        off = 32
        csw = struct.unpack_from('<H', w, off)[0]
        off += 2 + csw * 2
        cslw = struct.unpack_from('<H', w, off)[0]
        off += 2
        lw = struct.unpack_from('<' + 'I' * cslw, w, off)
        off += cslw * 4
        nfc = struct.unpack_from('<H', w, off)[0]
        off += 2
        if cslw < 4 or nfc <= 33:
            raise ValueError('Missing main story or CLX')
        fc, lcb = struct.unpack_from('<II', w, off + 33 * 8)
        clx = table[fc:fc + lcb]
        if len(clx) != lcb:
            raise ValueError('CLX outside table stream')
        pos = 0
        while pos < len(clx) and clx[pos] == 1:
            pos += 3 + struct.unpack_from('<H', clx, pos + 1)[0]
        if pos >= len(clx) or clx[pos] != 2:
            raise ValueError('Missing piece table')
        length = struct.unpack_from('<I', clx, pos + 1)[0]
        plc = clx[pos + 5:pos + 5 + length]
        if len(plc) != length or (length - 4) % 12:
            raise ValueError('Invalid piece table length')
        count = (length - 4) // 12
        cps = struct.unpack_from('<' + 'I' * (count + 1), plc, 0)
        if cps[0] != 0 or any(b < a for a, b in zip(cps, cps[1:])) or cps[-1] < lw[3]:
            raise ValueError('Invalid CP ranges')
        parts = []
        for i in range(count):
            start, end = cps[i:i + 2]
            if start >= lw[3]:
                break
            n = min(end, lw[3]) - start
            pcd = struct.unpack_from('<I', plc, 4 * (count + 1) + i * 8 + 2)[0]
            compressed, at = bool(pcd & 0x40000000), pcd & 0x3FFFFFFF
            if compressed:
                at //= 2
                piece = w[at:at + n]
                # Compressed non-ASCII uses document-specific codepages: reject here.
                if any(c >= 128 for c in piece):
                    raise ValueError('Non-ASCII compressed piece requires codepage handling')
                text = piece.decode('ascii')
            else:
                piece = w[at:at + n * 2]
                text = piece.decode('utf-16le', errors='strict')
            if len(piece) != n * (1 if compressed else 2) or len(text) != n:
                raise ValueError('CP length/Unicode mismatch')
            parts.append(text)
        raw = ''.join(parts)
        if len(raw) != lw[3]:
            raise ValueError('Main story incomplete')
        return raw, {'nFib': nfib, 'piece_count': count, 'main_story_cp': lw[3],
                     'tables_reconstructed': False, 'automatic_numbering_reconstructed': False}


def legacy_blocks(path):
    raw, metrics = word_text(path)
    # Resolve fields globally because TOC fields span paragraphs.
    visible, stack = [], []
    for cp, ch in enumerate(raw):
        if ch == '\x13':
            stack.append(False)
        elif ch == '\x14':
            if not stack:
                raise ValueError('Unmatched field separator')
            stack[-1] = True
        elif ch == '\x15':
            if not stack:
                raise ValueError('Unmatched field end')
            stack.pop()
        elif all(stack):
            visible.append((cp, ch))
    if stack:
        raise ValueError('Unclosed Word fields')
    blocks, current = [], []
    for cp, ch in visible + [(len(raw), '\r')]:
        if ch not in {'\r', '\x0c'}:
            current.append((cp, ch))
            continue
        if not current:
            continue
        # A table's final cell marker can be followed immediately by an ordinary
        # heading. Keeping both in one block swallows the next section boundary.
        # Split on the actual Word cell markers; do not infer table geometry.
        segments, chunk = [], []
        for point in current:
            if point[1] == '\x07':
                if chunk:
                    segments.append((chunk, 'legacy_table_linearized'))
                chunk = []
            else:
                chunk.append(point)
        if chunk:
            segments.append((chunk, None))
        for points, table_kind in segments:
            literal = ''.join(c for _, c in points)
            text = re.sub(r'[\x00-\x06\x08\x0b\x0e-\x1f]', '', literal).strip()
            if text:
                kind = table_kind or ('toc' if '\t' in text else 'paragraph')
                blocks.append({'id': f'cp{points[0][0]}_{points[-1][0]+1}', 'kind': kind,
                               'text': text, 'raw_text': raw[points[0][0]:points[-1][0]+1],
                               'cp_start': points[0][0], 'cp_end': points[-1][0]+1,
                               'has_uninterpreted_object': any(c in literal for c in ['\x01', '\x08']),
                               'locator': f'Word 主正文 CP {points[0][0]}–{points[-1][0]+1}，不是页码'})
        current = []
    return blocks, metrics


def column(n):
    result = ''
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def xls_blocks(path):
    import xlrd
    book = xlrd.open_workbook(path, formatting_info=True)
    blocks, sheets = [], []
    for si, sheet in enumerate(book.sheets(), 1):
        merged = {}
        for r0, r1, c0, c1 in sheet.merged_cells:
            for r in range(r0, r1):
                for c in range(c0, c1):
                    merged[r, c] = (r0, c0)
        rows = []
        for ri in range(sheet.nrows):
            cells = []
            for ci in range(sheet.ncols):
                anchor = merged.get((ri, ci), (ri, ci))
                raw = sheet.cell_value(ri, ci)
                value = sheet.cell_value(*anchor)
                cells.append({'column': ci + 1, 'address': f'{column(ci+1)}{ri+1}',
                              'raw': str(raw), 'text': str(value),
                              'origin': f'{column(anchor[1]+1)}{anchor[0]+1}',
                              'inherited_explicit_merge': anchor != (ri, ci)})
            row = {'id': f's{si}:r{ri+1}', 'kind': 'sheet_row', 'sheet': sheet.name,
                   'sheet_index': si, 'row': ri + 1, 'cells': cells,
                   'text': '；'.join(f'{c["address"]}：{c["text"] or "（空白）"}' for c in cells),
                   'locator': f'工作表“{sheet.name}”第{ri+1}行，列号与原文件一致'}
            rows.append(row)
            blocks.append(row)
        sheets.append({'index': si, 'name': sheet.name, 'nrows': sheet.nrows,
                       'ncols': sheet.ncols, 'merged_ranges_zero_based_exclusive': sheet.merged_cells,
                       'row_ids': [r['id'] for r in rows]})
    return blocks, {'sheets': sheets, 'blank_cells_forward_filled': False}


def load_sources(out):
    inventory = [json.loads(l) for l in (AUDIT / 'inventory.jsonl').read_text().splitlines()]
    for item in inventory:
        if digest((ROOT / 'rawdoc' / item['path']).read_bytes()) != item['sha256']:
            raise ValueError(f'Source changed since inventory: {item["path"]}')
    canonical = {r['content_id']: r for r in inventory if r['is_canonical_copy']}
    sources, selection = {}, []
    for sid, item in canonical.items():
        category = item['category']
        if category in {'运行规程', '典型操作票', '故障资料'}:
            relevance = 'primary'
            reason = '运行约束、异常处置、操作顺序或保护动作/故障解释'
        elif category in {'产品说明书', '定值与参数', '运行图册'}:
            relevance = 'supporting'
            reason = '设备原理、厂家报警、定值判据或接线拓扑；须匹配站点/版本'
        elif category == '调试记录':
            relevance = 'historical'
            reason = '调试时序与历史缺陷，不直接作为现行处置规定'
        else:
            relevance = 'observational'
            reason = '事件时序线索，日志本身通常没有根因或处置结果'
        source_path = ROOT / 'rawdoc' / item['path']
        row = {'source_id': sid, 'path': 'rawdoc/' + item['path'], 'sha256': item['sha256'],
               'category': category, 'relevance': relevance, 'selection_reason': reason,
               'currency': 'unverified', 'audit_status': item['extraction']['status'],
               'duplicate_paths': [r['path'] for r in inventory if r['content_id'] == sid],
               'filename_version_hints': item['version_hints'], 'qa_count': 0,
               'station_policy': '来源逐条限定；其他站点不得自动等同于姑苏站'}
        selection.append(row)
        if relevance not in {'primary', 'supporting'}:
            continue
        data = json.loads((AUDIT / item['extracted_json']).read_text())
        blocks = data['blocks']
        metrics = data['metrics']
        if sid == REG3:
            blocks, metrics = legacy_blocks(source_path)
            row['audit_status'] = 'native_word_text_recovered'
        elif sid == TRIPS:
            blocks, metrics = xls_blocks(source_path)
            row['audit_status'] = 'xls_merged_cells_recovered'
        source = {**row, 'blocks': blocks, 'metrics': metrics, 'issues': data.get('issues', [])}
        if sid == REG3:
            source['issues'] = ['legacy_tables_and_figures_not_interpreted', 'automatic_numbering_not_reconstructed']
        if sid == TRIPS:
            source['issues'] = ['table_markings_describe_source_matrix_not_verified_field_outputs']
        source['scope'] = scope(source)
        sources[sid] = source
        if relevance == 'primary' or sid in PRODUCTS:
            save(out / 'sources' / (sid + '.json'), source)
    return sources, selection, inventory


def scope(source):
    sid, name = source['source_id'], Path(source['path']).name
    if sid == REG5:
        return '姑苏站《设备异常及故障应急处置》第五分册6.0版（2025年9月）'
    if sid == REG3:
        return '姑苏站《运行方式及设备运行规定》第三分册6.0版（2026年2月）'
    if sid == OVERVIEW:
        return '姑苏站《设备概况》6.0版（2025年9月）'
    if sid == CASES:
        return '《姑苏换流站典型问题及故障案例汇编》V4（文件名日期2024-08-21）中的历史记录'
    if sid == REPORT:
        return 'RD2S0077025-0012 Rev00（2025年9月16日）对9月14日事件的分析'
    return '所提供的《' + name + '》'


PRODUCTS = {
    'd_b75bb4266943b664': [('7', '桥臂控制单元运行工况及异常处理', [24])],
    'd_1ca28e18b9caa815': [('7text', '合并单元闭锁与报警的处置原则', [41])],
    'd_30a1a8e682ddeb00': [('7text', '换流变保护装置闭锁与报警的处置原则', [67])],
    'd_26cbcbb315e89489': [('7intro', '阀控制单元报警定位途径', [27])],
}


def joined(blocks):
    return '\n\n'.join(b['text'].strip() for b in blocks)


def pdf_sections(source):
    """Exact offsets into page text; page breaks never define procedure boundaries."""
    sections, current = [], None
    for b in source['blocks'][10:]:
        matches = list(re.finditer(r'(?m)^\s*(\d{1,2}\.\d{1,2})\s+([^\n]+?)\s*$', b['text']))
        last = 0
        for m in matches:
            if current and m.start() > last:
                current['spans'].append(span(source, b, last, m.start()))
            current = {'id': m.group(1), 'title': m.group(2).strip(), 'spans': []}
            sections.append(current)
            last = m.start()
        if current and last < len(b['text']):
            current['spans'].append(span(source, b, last, len(b['text'])))
    for s in sections:
        s['raw_text'] = ''.join(x['text'] for x in s['spans'])
        lines = s['raw_text'].splitlines()
        # Remove printed page numbers and chapter headings carried across sections.
        lines = [l.strip() for l in lines if not re.fullmatch(r'\s*\d+\s*', l)
                 and not re.match(r'^\s*\d+\s+\S.*(?:预案|规定|系统)$', l)]
        s['text'] = '\n'.join(l for l in lines if l.strip())
        s['text'] = re.sub(r'(?<![。；;：:！？!?\n])\n(?!(?:\d+(?:\.\d+)+|[（(]\d+[）)]))', '', s['text'])
    return sections


def span(source, block, start=0, end=None):
    end = len(block['text']) if end is None else end
    return {'id': f'{source["source_id"]}/{block["id"]}:{start}:{end}',
            'source_id': source['source_id'], 'block_id': block['id'],
            'start': start, 'end': end, 'text': block['text'][start:end],
            'locator': block['locator']}


def block_spans(source, blocks):
    return [span(source, b) for b in blocks]


def clauses(text):
    """Keep each source numbered clause intact, including its internal branches."""
    matches = list(re.finditer(r'(?m)^(\d+(?:\.\d+){2,})\s*', text))
    return [{'source_clause': m.group(1), 'text': text[m.end():matches[i+1].start()
              if i+1 < len(matches) else len(text)].strip()}
            for i, m in enumerate(matches)]


def procedure_units(source):
    units, current = [], None
    for b in source['blocks']:
        m = re.match(r'^操作任务[：:]\s*(.+)', b['text'])
        if m:
            current = {'id': b['id'], 'title': m.group(1), 'blocks': [b], 'steps': []}
            units.append(current)
        elif current:
            # A category heading belongs to the next group, not the previous procedure.
            if b['kind'] != 'paragraph' and b['kind'] != 'line':
                current = None
            elif re.match(r'^[（(][一二三四五六七八九十]+[）)]', b['text']) or re.match(r'^[一二三四五六七八九十]+[、．.]', b['text']):
                current = None
            else:
                current['blocks'].append(b)
                current['steps'].append(b)
    return units


class Builder:
    def __init__(self, sources):
        self.sources = sources
        self.rows, self.units, self.contexts, self.holds = [], [], {}, []

    def context(self, key, evidence, related=()):
        all_spans = {x['id']: x for x in evidence}
        for x in related:
            all_spans.setdefault(x['id'], x)
        source_ids = list(dict.fromkeys(x['source_id'] for x in all_spans.values()))
        text = '\n\n'.join(f'[{x["id"]}] {self.sources[x["source_id"]]["scope"]}\n{x["text"]}'
                           for x in all_spans.values())
        cid = 'ctx_' + digest(text.encode())[:16]
        self.contexts.setdefault(cid, {'id': cid, 'chain_id': key, 'source_ids': source_ids,
                                      'spans': list(all_spans.values()), 'text': text,
                                      'truncation': False})
        return cid

    def add(self, key, family, title, question, answer, evidence, related=(),
            checklist=None, steps=None, flags=(), source_unit=None, generation='source_extractive'):
        cid = self.context(key, evidence, related)
        source_ids = list(dict.fromkeys(x['source_id'] for x in evidence))
        if not question or not answer or not evidence:
            raise ValueError('Empty QA or evidence')
        items = checklist or [{'text': x['text'], 'match': 'normalized_substring',
                               'source_ids': x.get('source_ids', [e['id'] for e in evidence])}
                              for x in (steps or [])]
        row = {'id': f'OPS{len(self.rows)+1:05d}', 'chain_id': key, 'family': family,
               'topic': title, 'question': question, 'answer': answer,
               'source_ids': source_ids, 'context_id': cid, 'evidence': evidence,
               'answer_checklist': items, 'ordered_steps': steps or [],
               'condition_clauses': [x for x in (steps or []) if re.search(r'若|如果|当|否则|只有|方可', x['text'])],
               'prohibition_clauses': [x for x in (steps or []) if re.search(r'禁止|不得|不允许|严禁', x['text'])],
               'restoration_clauses': [x for x in (steps or []) if re.search(r'恢复|复归|重新投入|再投入', x['text'])],
               'generation': generation, 'split': 'train_candidate', 'source_unit': source_unit,
               'review': {'source_grounding': 'mechanically_checked_at_build',
                          'independent_model_review': 'not_performed', 'expert_review': 'not_performed'},
               'flags': list(flags)}
        self.rows.append(row)
        return row


REG5_LINKS = {'3.5': ['10.2', '6.11', '9.15'], '4.8': ['4.4'],
              '7.8': ['3.3'], '9.6': ['7.8'], '9.12': ['3.4'],
              '10.1': ['10.3'], '10.6': ['10.3'], '10.11': ['10.3'],
              '12.18': ['12.19']}


def dependency_closure(section):
    result, pending = [], list(REG5_LINKS.get(section, []))
    while pending:
        target = pending.pop(0)
        if target == section or target in result:
            continue
        result.append(target)
        pending.extend(REG5_LINKS.get(target, []))
    return result


def build_incidents(b):
    source = b.sources[REG5]
    sections = pdf_sections(source)
    index = {x['id']: x for x in sections}
    for unit in sections:
        key = f'{REG5}/section{unit["id"]}'
        b.units.append({'id': key, 'source_id': REG5, 'title': unit['title'],
                        'kind': 'incident_plan', 'spans': unit['spans'],
                        'dependencies': [f'{REG5}/section{x}' for x in dependency_closure(unit['id'])],
                        'dependency_basis': 'curated_source_reference_or_required_related_check',
                        'coverage_status': 'source_extractive_candidate'})
        if unit['id'] == '8.4':
            b.units[-1]['coverage_status'] = 'held'
            b.holds.append({'unit_id': key, 'reason': 'PF条件“8个以下”与“7个”重叠，不能改写为互斥分支',
                            'evidence': unit['spans'], 'disposition': 'only_ambiguity_QA_allowed'})
            continue
        if unit['id'] == '15.13':
            b.units[-1]['coverage_status'] = 'held'
            b.holds.append({'unit_id': key, 'reason': '15.13.2.2.4原文“可判断未误报警”疑有笔误，不能自行修正结论',
                            'evidence': unit['spans'], 'disposition': 'only_ambiguity_QA_allowed'})
            continue
        if int(unit['id'].split('.')[0]) < 2:
            b.units[-1]['coverage_status'] = 'indexed_scope_only'
            continue
        related = index['2.1']['spans'] + index['2.2']['spans']
        for target in dependency_closure(unit['id']):
            related += index[target]['spans']
        steps = clauses(unit['text'])
        issue_flags = []
        if '重复）' in unit['text']:
            issue_flags.append('source_contains_explicit_duplicate_clause')
        if '定值' in unit['text']:
            issue_flags.append('numeric_setting_not_inferred_from_plan')
        general = unit['id'].startswith('2.')
        question = (f'按{source["scope"]}，“{unit["title"]}”的通用要求是什么？请保留指挥权限、汇报、紧急处置和通信异常时的适用规定。' if general else
                    f'按{source["scope"]}，遇到“{unit["title"].removesuffix("处理预案")}”时，如何识别现象并按原文完整处理？请保留条件分支、汇报及恢复要求。')
        answer = f'该版规程的对应预案如下（编号保留原文；各条件分支按其适用情况执行）：\n\n{unit["text"]}'
        b.add(key, 'incident_general_rules' if general else 'incident_full_chain', unit['title'], question, answer, unit['spans'], related,
              steps=steps, flags=issue_flags, source_unit=unit['id'])
    return index


def build_tickets(b, rules_topics):
    operating_rules = b.sources[REG3]
    common = block_spans(operating_rules, rules_topics['5.5'])
    for sid in TICKETS:
        source = b.sources[sid]
        seen = {}
        for unit in procedure_units(source):
            key = sid + '/' + unit['id']
            text = joined(unit['steps'])
            disposition = 'source_extractive_candidate'
            reason = None
            if not unit['steps']:
                reason = '操作任务无可提取步骤'
            elif any(x['kind'] == 'table' for x in unit['blocks']):
                reason = '表格布局未核验'
            elif re.search(r'[xX×]{2,}|某[台套]|\bXX\b', unit['title'] + text):
                reason = '存在未替换的模板设备/端子占位符'
            elif re.search(r'[（(][\s＿_]*[）)]', unit['title'] + text):
                reason = '操作票包含待现场填写的空白值；不作为已确定条件的完整任务QA'
            elif '错误！未找到' in text:
                reason = '原文引用断裂'
            fingerprint = digest(norm(unit['title'] + text).encode())
            if fingerprint in seen:
                reason = '同来源内任务及步骤完全重复'
            if reason:
                disposition = 'held'
                b.holds.append({'unit_id': key, 'title': unit['title'], 'reason': reason,
                                'duplicate_of': seen.get(fingerprint), 'source_id': sid})
            else:
                seen[fingerprint] = key
            b.units.append({'id': key, 'source_id': sid, 'title': unit['title'], 'kind': 'operation_ticket',
                            'spans': block_spans(source, unit['blocks']),
                            'dependencies': [f'{REG3}/topic5.5'], 'coverage_status': disposition,
                            'step_count': len(unit['steps'])})
            if reason:
                continue
            steps = [{'source_clause': str(i), 'text': x['text'], 'source_ids': [span(source, x)['id']]}
                     for i, x in enumerate(unit['steps'], 1)]
            q = f'按{source["scope"]}，完成“{unit["title"]}”这一任务的完整操作顺序是什么？保留操作前检查和每一步状态确认。'
            a = '该操作票列出的完整顺序如下：\n\n' + '\n'.join(f'{i}. {x["text"]}' for i, x in enumerate(unit['steps'], 1))
            b.add(key, 'operation_sequence', unit['title'], q, a, block_spans(source, unit['blocks']), common,
                  steps=steps, flags=['ticket_requires_applicable_operating_conditions',
                                      'automatic_numbering_replaced_by_source_paragraph_order'],
                  source_unit=unit['id'])


def build_rules(b):
    source = b.sources[REG3]
    blocks = source['blocks']
    body_start = next(i for i, x in enumerate(blocks) if x['kind'] == 'paragraph' and x['text'] == '范围')
    toc = []
    for x in blocks[:body_start]:
        m = re.match(r'^(\d+(?:\.\d+)*)\s+(.+?)\t\d+$', x['text'])
        if m:
            toc.append((m[1], m[2].strip()))
    starts, cursor = [], body_start
    for number, title in toc:
        at = next((i for i in range(cursor, len(blocks))
                   if blocks[i]['kind'] == 'paragraph' and norm(blocks[i]['text']) == norm(title)), None)
        if at is None:
            raise ValueError(f'Word TOC/body heading not resolved: {number} {title}')
        starts.append((at, number))
        cursor = at + 1
    if len({number for _, number in starts}) != len(starts):
        raise ValueError('Duplicate TOC section identifiers')
    topics = {}
    for n, (at, num) in enumerate(starts):
        end = starts[n+1][0] if n+1 < len(starts) else len(blocks)
        # Last main topic ends before appendices.
        appendix = next((i for i in range(at+1, end) if re.match(r'^附录[A-Z]', blocks[i]['text'])), end)
        chosen = blocks[at:appendix]
        if len(chosen) <= 1:
            continue
        topics[num] = chosen
        key = sid_key = f'{REG3}/topic{num}'
        flags = []
        if any(x['kind'] == 'legacy_table_linearized' or x.get('has_uninterpreted_object') for x in chosen):
            flags.append('uninterpreted_table_or_object')
        held = bool(flags or len(joined(chosen)) > 14000 or '.' not in num)
        b.units.append({'id': sid_key, 'source_id': REG3, 'title': chosen[0]['text'],
                        'kind': 'operating_rules', 'spans': block_spans(source, chosen),
                        'coverage_status': 'held' if held else 'source_extractive_candidate',
                        'flags': flags})
        if held:
            b.holds.append({'unit_id': key, 'reason': '完整单元含未解析图表、超过首批文本范围或为章标题',
                            'source_id': REG3})
            continue
        steps = [{'source_clause': x['id'], 'text': x['text'], 'source_ids': [span(source, x)['id']]} for x in chosen[1:]]
        q = f'按{source["scope"]}，“{chosen[0]["text"]}”有哪些运行要求、条件和限制？请完整保留原文规定。'
        b.add(key, 'operating_constraints', chosen[0]['text'], q,
              '该版文档在这一节的规定如下：\n\n' + joined(chosen[1:]), block_spans(source, chosen),
              steps=steps, flags=['legacy_word_text_only'], source_unit=num)
    return topics


def enrich_incident_contexts(b, topics):
    # These are explicit object-level links for expanded validity checking.
    # Uninterpreted table cells remain flagged and are not turned into facts.
    explicit = {'3.5': ['8.7'], '7.6': ['8.8'], '7.14': ['8.8'],
                '7.15': ['8.8'], '9.22': ['8.8']}
    for row in b.rows:
        if row['family'] not in {'incident_full_chain', 'incident_general_rules'}:
            continue
        number = row['source_unit']
        linked = explicit.get(number, [])
        if not linked:
            continue
        old = b.contexts[row['context_id']]
        related = old['spans'] + [s for topic in linked for s in block_spans(b.sources[REG3], topics[topic])]
        row['context_id'] = b.context(row['chain_id'], row['evidence'], related)
        row['flags'].append('cross_volume_operating_constraints_in_expanded_context')
        if number == '9.22':
            supplement = ('\n\n关联限制：第三分册6.0版（2026年2月）8.8另规定，'
                          '姑苏站全压运行不允许在线手动切除直流滤波器；LCC半压工况下，'
                          '还须保证该极至少一组直流滤波器，并在操作前检查穿越电流'
                          '小于等于刀闸最大切除电流340A。不能只依据以上2025年预案的'
                          '电流分支给出无运行方式前提的切除结论；两种电流判据及适用版本'
                          '需核对，不能自行当作同一定值。')
            row['answer'] += supplement
            row['evidence'] += block_spans(b.sources[REG3], topics['8.8'])
            row['source_ids'].append(REG3)
            row['flags'].append('cross_volume_applicability_requires_confirmation')
        for unit in b.units:
            if unit['id'] == row['chain_id']:
                unit.setdefault('dependencies', []).extend(f'{REG3}/topic{x}' for x in linked)
    used = {r['context_id'] for r in b.rows}
    b.contexts = {cid: c for cid, c in b.contexts.items() if cid in used}


def build_matrix(b):
    source = b.sources[TRIPS]
    for sheet in source['metrics']['sheets']:
        si = sheet['index']
        rows = [x for x in source['blocks'] if x['sheet_index'] == si]
        # Four matrices have 2 header rows/3 identity columns; VSC has 3/2.
        header_count, identity_count = (3, 2) if sheet['name'] == 'VSC换流器区' else (2, 3)
        headers = rows[header_count - 1]['cells']
        for row in rows[header_count:]:
            identities = row['cells'][:identity_count]
            if not identities[0]['text'].strip():
                continue
            marked = []
            for cell in row['cells'][identity_count:]:
                if cell['text'] in {'×', '●', '√'}:
                    header = headers[cell['column'] - 1]
                    if not header['text'].strip():
                        marked.append({'unresolved': True, 'cell': cell})
                    else:
                        marked.append({'action': norm(header['text']), 'address': cell['address'],
                                       'header_address': header['address'], 'mark': cell['text']})
            if not marked:
                continue
            key = f'{TRIPS}/{row["id"]}'
            title = ' / '.join(norm(c['text']) for c in identities if c['text'].strip())
            b.units.append({'id': key, 'source_id': TRIPS, 'title': title,
                            'kind': 'protection_action_row', 'spans': block_spans(source, rows[:header_count]+[row]),
                            'coverage_status': 'held' if any(x.get('unresolved') for x in marked) else 'source_extractive_candidate'})
            if any(x.get('unresolved') for x in marked):
                b.holds.append({'unit_id': key, 'reason': '有动作标记但对应列标题为空', 'source_id': TRIPS})
                continue
            answer = f'在工作表“{sheet["name"]}”第{row["row"]}行，以下列被原表标记：\n' + '\n'.join(
                f'{i}. {x["action"]}（{x["address"]}={x["mark"]}；表头{x["header_address"]}）。' for i, x in enumerate(marked, 1))
            answer += '\n空白列只表示本表未作该动作标记，不能据此补出现场接线、实际定值或动作延时。'
            checklist = [{'text': x['action'], 'match': 'normalized_substring',
                          'source_ids': [span(source, row)['id'], span(source, rows[header_count-1])['id']]}
                         for x in marked]
            b.add(key, 'protection_action_mapping', title,
                  f'按{source["scope"]}工作表“{sheet["name"]}”，信号“{title}”这一行标记了哪些出口或功能？',
                  answer, block_spans(source, rows[:header_count]+[row]),
                  block_spans(source, rows), checklist=checklist,
                  flags=['matrix_version_and_effective_status_unverified'], source_unit=row['id'])


def build_cases(b):
    source = b.sources[CASES]
    blocks = source['blocks']
    for i, x in enumerate(blocks):
        if not x['text'].startswith('典型案例'):
            continue
        chosen = [x]
        for nxt in blocks[i+1:]:
            if nxt['text'].startswith(('典型案例', '图', '验收相关注意事项')) or len(nxt['text']) < 30:
                break
            chosen.append(nxt)
        text = joined(chosen)
        key = CASES + '/' + x['id']
        title = x['text'].split('：', 1)[0] + '（' + x['id'] + '）'
        steps = [{'source_clause': c['id'], 'text': c['text'], 'source_ids': [span(source, c)['id']]} for c in chosen]
        b.units.append({'id': key, 'source_id': CASES, 'title': title, 'kind': 'historical_case',
                        'spans': block_spans(source, chosen), 'coverage_status': 'source_extractive_candidate'})
        b.add(key, 'historical_case_analysis', title,
              f'按{source["scope"]}，{title}记载了什么现象、原因及处理/整改结果？仅回答记录已有的内容。',
              '该案例记录如下；未记载的根因或验证结果不能补全：\n\n' + text,
              block_spans(source, chosen), steps=steps,
              flags=['historical_result_not_current_operating_instruction', 'figures_not_used_to_infer_facts'], source_unit=x['id'])


def build_products(b):
    for sid, units in PRODUCTS.items():
        source = b.sources[sid]
        for label, title, pages in units:
            blocks = [x for x in source['blocks'] if x['id'] in ['page'+str(p) for p in pages]]
            raw = joined(blocks)
            if label == '7text':
                # Use the prose before the alarm table; don't infer flattened cell mappings.
                raw = raw.split('表 7-1')[0]
            elif label == '7intro':
                raw = raw.split('7.1.1')[0]
            key = sid + '/' + label
            b.units.append({'id': key, 'source_id': sid, 'title': title, 'kind': 'manufacturer_fault_guidance',
                            'spans': block_spans(source, blocks), 'coverage_status': 'source_extractive_candidate'})
            b.add(key, 'manufacturer_fault_guidance', title,
                  f'按{source["scope"]}，{title}有哪些规定？保留厂家对复归、重启或检修的限制。',
                  '该厂家手册的相关文字规定如下：\n\n' + raw.strip(), block_spans(source, blocks),
                  flags=['manufacturer_scope_not_automatically_site_configuration'])


def build_authored(b, incident_index, rules_topics):
    path = FOCUSED
    records = json.loads(path.read_text()) if path.exists() else []
    for r in records:
        ev, related = [], []
        for target, specs in [(ev, r['evidence']), (related, r.get('related', []))]:
            for spec in specs:
                sid = spec['source_id']
                source = b.sources[sid]
                if 'section' in spec:
                    target += incident_index[spec['section']]['spans']
                elif 'topic' in spec:
                    target += block_spans(source, rules_topics[spec['topic']])
                else:
                    blocks = [x for x in source['blocks'] if x['id'] in spec['blocks']]
                    if len(blocks) != len(spec['blocks']):
                        raise ValueError(f'Unknown evidence block: {spec}')
                    target += block_spans(source, blocks)
        checks = [{'text': x, 'match': 'normalized_substring', 'source_ids': [e['id'] for e in ev]}
                  for x in r.get('required_answer_fragments', [])]
        key = r.get('chain_id') or ('focused/' + r['key'])
        b.add(key, r['family'], r['topic'], r['question'], r['answer'], ev, related,
              checklist=checks, flags=r.get('flags', []), generation='codex_source_grounded_composition')


def apply_roundtrip_reviews(b, out):
    path = out / 'roundtrip_review.json'
    if not path.exists():
        return
    records = json.loads(path.read_text())['rows']
    reviewed = {r['question_sha256']: r for r in records}
    for row in b.rows:
        review = reviewed.get(digest(row['question'].encode()))
        if review is None:
            continue
        if (review['answer_sha256'] != digest(row['answer'].encode()) or
                review['context_sha256'] != digest(b.contexts[row['context_id']]['text'].encode())):
            raise ValueError(f'Stale roundtrip review for {row["id"]}; review must match the full answer and context')
        row['review'].update(independent_model_review='answer_blind_reanswer_compared_with_sources_by_codex',
                             reanswer_verdict=review['reanswer_verdict'],
                             candidate_verdict=review['candidate_verdict'],
                             review_record='roundtrip_review.json')


def validate(b):
    checks = 0
    source_blocks = {sid: {x['id']: x for x in s['blocks']} for sid, s in b.sources.items()}
    for context in b.contexts.values():
        for e in context['spans']:
            blocks = source_blocks[e['source_id']]
            if blocks[e['block_id']]['text'][e['start']:e['end']] != e['text']:
                raise ValueError('Evidence offset mismatch')
            checks += 1
    questions = set()
    for row in b.rows:
        q = norm(row['question'])
        if q in questions:
            raise ValueError('Duplicate question')
        questions.add(q)
        answer = norm(row['answer'])
        for item in row['answer_checklist']:
            if norm(item['text']) not in answer:
                raise ValueError(f'Checklist omission: {row["id"]} {item["text"][:100]}')
        if any(x['id'] not in {e['id'] for e in b.contexts[row['context_id']]['spans']} for x in row['evidence']):
            raise ValueError('Evidence not in complete context')
    return {'qa': len(b.rows), 'contexts': len(b.contexts), 'source_span_checks': checks,
            'unique_questions': True, 'answer_checklist_coverage': 'all_declared_items_present',
            'source_fingerprints': 'all_inventory_business_files_checked_including_duplicate_copies',
            'semantic_accuracy': 'not_estimated', 'expert_review': 'not_performed',
            'independent_roundtrip': 'selected_sample_completed' if any(
                r['review']['independent_model_review'] != 'not_performed' for r in b.rows) else 'not_performed',
            'independent_roundtrip_rows': sum(r['review']['independent_model_review'] != 'not_performed' for r in b.rows),
            'input_truncation': False}


def viewer(out, b):
    # Share repeated evidence and operating-rule dependencies in the browser.
    # The JSONL exports retain the original full records.
    spans = {e['id']: e for c in b.contexts.values() for e in c['spans']}
    keys = ['id', 'chain_id', 'family', 'topic', 'question', 'answer', 'source_ids',
            'context_id', 'generation', 'flags', 'review']
    rows = [{**{k: r[k] for k in keys}, 'evidence_ids': [e['id'] for e in r['evidence']],
             'answer_checklist': [x['text'] for x in r['answer_checklist']]} for r in b.rows]
    contexts = {cid: {'span_ids': [e['id'] for e in c['spans']]} for cid, c in b.contexts.items()}
    payload = json.dumps({'rows': rows, 'contexts': contexts, 'spans': spans,
                          'scopes': {sid: s['scope'] for sid, s in b.sources.items()}},
                         ensure_ascii=False).replace('<', '\\u003c')
    html = TEMPLATE.read_text(encoding='utf-8').replace('PAYLOAD', payload)
    (out / 'index.html').write_text(html, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sources, selection, inventory = load_sources(out)
    b = Builder(sources)
    incidents = build_incidents(b)
    topics = build_rules(b)
    enrich_incident_contexts(b, topics)
    build_tickets(b, topics)
    build_matrix(b)
    build_cases(b)
    build_products(b)
    build_authored(b, incidents, topics)
    apply_roundtrip_reviews(b, out)
    result = validate(b)
    counts = Counter(sid for r in b.rows for sid in r['source_ids'])
    for row in selection:
        row['qa_count'] = counts[row['source_id']]
        if not row['qa_count']:
            row['construction_status'] = ('historical_or_observational_index_only'
                                         if row['relevance'] in {'historical', 'observational'} else
                                         'indexed_not_yet_interpreted_or_not_selected_for_first_release')
        else:
            row['construction_status'] = 'QA_candidates_built'
    jsonl(out / 'qa.jsonl', b.rows)
    # A solution-focused view prevents device-by-device ticket repetition from
    # dominating a later training run. It is a view, not extra independent data.
    solution = [r for r in b.rows if r['family'] != 'operation_sequence']
    jsonl(out / 'solution_qa.jsonl', solution)
    save(out / 'contexts.json', list(b.contexts.values()))
    save(out / 'coverage_units.json', b.units)
    save(out / 'held_units.json', b.holds)
    save(out / 'source_selection.json', selection)
    with (out / 'source_selection.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['source_id', 'path', 'category', 'relevance', 'selection_reason',
                                             'audit_status', 'currency', 'construction_status', 'qa_count'])
        writer.writeheader()
        writer.writerows({k: x[k] for k in writer.fieldnames} for x in selection)
    for kind in ['direct', 'context']:
        records = []
        for r in b.rows:
            user = r['question'] if kind == 'direct' else '文档证据及关联上下文：\n\n' + b.contexts[r['context_id']]['text'] + '\n\n问题：' + r['question']
            records.append({'messages': [{'role': 'system', 'content': SYSTEM},
                                         {'role': 'user', 'content': user},
                                         {'role': 'assistant', 'content': r['answer']}]})
        jsonl(out / ('sft_' + kind + '.jsonl'), records)
        solution_ids = {r['id'] for r in solution}
        jsonl(out / ('sft_solution_' + kind + '.jsonl'),
              [record for record, row in zip(records, b.rows) if row['id'] in solution_ids])
    jsonl(out / 'export_index.jsonl', [{'row': i, 'id': r['id'], 'chain_id': r['chain_id'],
                                       'context_id': r['context_id'], 'family': r['family'],
                                       'generation': r['generation'], 'split': r['split']}
                                      for i, r in enumerate(b.rows, 1)])
    result.update(family_counts=dict(Counter(r['family'] for r in b.rows)),
                  generation_counts=dict(Counter(r['generation'] for r in b.rows)),
                  documents_used=len(counts), relevance_counts=dict(Counter(r['relevance'] for r in selection)),
                  held_units=len(b.holds), source_units=len(b.units),
                  max_answer_characters=max(len(r['answer']) for r in b.rows),
                  max_context_characters=max(len(c['text']) for c in b.contexts.values()),
                  length_note='字符数不是token数；导出保留完整内容，训练前必须按实际token长度选择窗口，禁止静默截断。')
    save(out / 'validation.json', result)
    save(out / 'manifest.json', {'created_date': '2026-10-08', 'status': 'source_grounded_candidates',
                                 'builder_sha256': digest(Path(__file__).read_bytes()),
                                 'focused_qa_sha256': digest(FOCUSED.read_bytes()),
                                 'viewer_template_sha256': digest(TEMPLATE.read_bytes()),
                                 'raw_files': sum(p.is_file() for p in (ROOT / 'rawdoc').rglob('*')),
                                 'business_files': len(inventory),
                                 'unique_documents': len(selection),
                                 'audit_inventory_sha256': digest((AUDIT / 'inventory.jsonl').read_bytes()),
                                 'qa_sha256': digest((out / 'qa.jsonl').read_bytes()), **result})
    viewer(out, b)
    (out / 'preview.md').write_text('# 直流站运行问题 QA\n\n' + '\n\n'.join(
        f'## {r["id"]} · {r["family"]}\n\n问：{r["question"]}\n\n答：{r["answer"]}\n\n来源：{", ".join(r["source_ids"])}；链路：{r["chain_id"]}' for r in b.rows), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
