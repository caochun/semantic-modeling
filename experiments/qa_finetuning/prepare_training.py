"""Freeze a grouped pilot split and both inference views; no model calls."""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.semantic/qa_operations_20261008'
OUT = ROOT / '.semantic/qa_train_1p7b_20261008'
SYSTEM = ('你根据指定站点、设备和版本回答直流站运行问题。保留适用条件、'
          '操作顺序、禁止事项、条件分支和恢复确认。区分历史记录、厂家手册与运行规程。'
          '已经掌握且适用条件明确的知识可以直接回答；关键条件不足、资料未定义或'
          '版本范围不明时说明缺口，不能补造数字、根因、联锁或操作权限。')


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))


def main():
    source_rows = [json.loads(l) for l in (SOURCE / 'solution_qa.jsonl').read_text().splitlines()]
    contexts = {c['id']: c for c in json.loads((SOURCE / 'contexts.json').read_text())}
    groups = defaultdict(list)
    for row in source_rows:
        groups[row['chain_id']].append(row)
    # Purposeful development coverage, stable within this source snapshot.
    # This is not claimed to be a previously unseen document final test.
    primary = []
    for family, count in [('incident_full_chain', 8), ('operating_constraints', 6),
                          ('protection_action_mapping', 4), ('historical_case_analysis', 3),
                          ('conditional_workflow', 3), ('conditional_diagnosis', 2),
                          ('cross_document_workflow', 2), ('insufficient_evidence', 2),
                          ('false_premise_correction', 1), ('source_conflict', 1)]:
        candidates = [r for r in source_rows if r['family'] == family and 100 <= len(r['answer']) <= 2200]
        candidates.sort(key=lambda r: digest('development_seed_42/' + r['chain_id']))
        selected = []
        for row in candidates:
            if row['chain_id'] in {r['chain_id'] for r in primary + selected}:
                continue
            selected.append(row)
            if len(selected) >= count:
                break
        primary += selected
    dev_groups = {r['chain_id'] for r in primary}
    train = [r for r in source_rows if r['chain_id'] not in dev_groups]
    held = [r for r in source_rows if r['chain_id'] in dev_groups]
    assert train and primary and not ({r['chain_id'] for r in train} & dev_groups)
    train_records = [{'id': r['id'], 'chain_id': r['chain_id'], 'family': r['family'],
                      'generation': r['generation'], 'context_id': r['context_id'],
                      'question': r['question'], 'answer': r['answer'],
                      'context': contexts[r['context_id']]['text'], 'system': SYSTEM,
                      'answer_checklist': r['answer_checklist']} for r in train]
    evaluation = []
    for row in primary:
        evaluation.append({'id': row['id'], 'chain_id': row['chain_id'], 'family': row['family'],
                           'context_id': row['context_id'], 'question': row['question'],
                           'answer': row['answer'], 'context': contexts[row['context_id']]['text'],
                           'system': SYSTEM, 'answer_checklist': row['answer_checklist'],
                           'evaluation_role': 'development_group_held_out_not_final_test'})
    # Fit diagnostics answer questions on trained knowledge. They are explicitly
    # training questions and may NOT be reported as unseen-question accuracy.
    fit = [r for r in train_records if r['generation'] == 'codex_source_grounded_composition'
           and 100 <= len(r['answer']) <= 1800]
    fit.sort(key=lambda r: digest('fit_diagnostic_42/' + r['id']))
    fit = fit[:8]
    for row in fit:
        evaluation.append({**row, 'evaluation_role': 'trained_question_fit_diagnostic'})
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'frozen.json').exists():
        raise ValueError('Pilot data already frozen; use the recorded files')
    write_jsonl(OUT / 'train.jsonl', train_records)
    write_jsonl(OUT / 'development.jsonl', evaluation)
    write_jsonl(OUT / 'held_qa.jsonl', held)
    train_spans = {e['id'] for r in train for e in r['evidence']}
    dev_overlap = {r['id']: len(train_spans & {e['id'] for e in r['evidence']}) for r in primary}
    result = {'date': '2026-10-08', 'source_qa_sha256': digest((SOURCE / 'qa.jsonl').read_text()),
              'source_solution_rows': len(source_rows), 'train': len(train_records),
              'held_rows': len(held), 'held_groups': len(dev_groups),
              'development_questions': len(primary), 'trained_question_fit_diagnostics': len(fit),
              'same_chain_cross_split': False, 'shared_document_knowledge_may_remain': True,
              'shared_expanded_dependency_contexts_may_remain': True,
              'dev_direct_evidence_span_overlap': dev_overlap,
              'split_note': '链路分组开发对照；不是严格未见文档泛化或最终测试。闭卷未训练链路不能作为已学知识固化成功率。fit题仅检测训练内容拟合。',
              'train_family_counts': dict(Counter(r['family'] for r in train_records)),
              'expert_review': 'not_performed', 'input_truncation': False,
              'model': 'Qwen/Qwen3-1.7B', 'revision': '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e',
              'training_goal': 'pilot_closed_book_direct_SFT_baseline_before_prompt_distillation',
              'files': {name: digest((OUT / name).read_text()) for name in ['train.jsonl', 'development.jsonl', 'held_qa.jsonl']}}
    (OUT / 'frozen.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (OUT / 'README.md').write_text(f'''# Qwen3-1.7B首轮闭卷SFT试验数据

训练{len(train_records)}条，链路分组留出{len(held)}条（{len(dev_groups)}组），实际开发问题{len(primary)}条，另有{len(fit)}条训练题拟合诊断。所有候选未经业务专家审核。

开发题按完整chain_id排除训练，但同文档知识和关联上下文可能重叠；不是独立最终测试，不声称严格未见文档。训练题复答仅用于检查知识拟合，不代表新问法泛化。

训练输入不含原文；原文保留在元信息和开卷对照中，完整前提写在问题中。先验证普通direct SFT，再据教师质量和闭卷/开卷对照决定蒸馏。没有把全部操作票按条数主导训练，也没有把资料为空统一标拒答。
''')
    print(json.dumps({k: result[k] for k in ['train', 'held_rows', 'held_groups', 'development_questions', 'trained_question_fit_diagnostics', 'training_goal']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
