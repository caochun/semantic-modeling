"""Build targeted boundary/correction QA from the existing rawdoc contexts."""
from copy import deepcopy
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.semantic/qa_pipeline'))
from corpus import exports, read_json, read_jsonl, write_json
from validate_dataset import validate, check_grid_edges

PRIOR = ROOT / '.semantic/qa_batch_006_20261007/cumulative'
OUT = ROOT / '.semantic/qa_batch_007_20261007'


def seg(text, refs, basis='bounded_synthesis'):
    return {'text': text, 'source_ids': refs, 'basis': basis}


CASES = [
    {
        'base': 'gs_control_v2_016', 'topic_id': 'T01', 'category': '纠错',
        'question': '以下合成草稿把控制主机和保护主机的检修都总结为“TEST结束后自动恢复ACTIVE，且剩余保护也可继续转TEST”。请按原文改正，并保留两条路径的恢复条件。',
        'answer_segments': [seg('该草稿把两类主机的恢复目标和限制混在了一起。控制主机进入TEST前必须先切到SERVICE，检修维护结束后重新投入SERVICE；原文没有规定控制主机检修后自动恢复ACTIVE。', ['p1344']), seg('保护主机需要检修时可切到TEST，检修完成后必须确认各项功能恢复正常且无出口信号，方能恢复ACTIVE。若三套保护已有两套退出运行，剩下的运行系统不允许切到TEST。两类主机都在TEST状态禁止输出，但不能据此统一成同一恢复流程。', ['p1345', 'p1367'])],
        'targets': ['precondition', 'branch', 'recovery_gate', 'summary_consistency'],
    },
    {
        'base': 'gs_comm_b002_005', 'topic_id': 'T02', 'category': '纠错',
        'question': '请核对这句架构结论：“SCADA LAN是A/B双网，所以任意网络、硬件或其他现场网络故障都不会影响系统。”哪些范围能由文档支持，哪些不能？',
        'answer_segments': [seg('文档只支持限定范围：SCADA LAN采用星型结构和A、B双重化配置，并说明单网线或单硬件故障不会导致系统故障。', ['p1323'], 'source_paraphrase'), seg('不能把这句话扩大为任意网络、任意硬件、多处同时故障或共同原因故障都无影响，也不能把SCADA LAN的说明套用于其他现场网络；这些影响在该段没有给出。', ['p1323'], 'source_limit')],
        'targets': ['scope_limit', 'unsupported_guarantee', 'network_boundary'],
    },
    {
        'base': 'gs_protection_b003_008', 'topic_id': 'T03', 'category': '纠错',
        'question': '出口策略表写了“换流器分步闭锁”。能否据此把它改写成“先闭锁A再闭锁B，间隔固定若干秒”？请给出审慎结论。',
        'answer_segments': [seg('不能。该条只列出“换流器分步闭锁”这一策略名称，没有展开分步对象、执行先后、步间条件或等待时间。', ['p1384'], 'source_paraphrase'), seg('因此不能从“分步”二字自行补成固定操作流程或等待秒数；需要进一步的保护逻辑说明，才能确认具体动作链。', ['p1384'], 'source_limit')],
        'targets': ['unknown_detail', 'unsupported_sequence', 'unsupported_timing'],
    },
    {
        'base': 'gs_protection_b003_023', 'topic_id': 'T03', 'category': '纠错',
        'question': '有人把极保护对照表中的延时和定值直接写成“当前现场已经采用的整定值”。请依据表头和版本限制改写。',
        'answer_segments': [seg('不能直接写成当前现场已确认的整定值。表格备注写有“定值待国调下发更新”，表中数值只能作为该版本文档的记录。', ['b1711:r1', 'b1711:r2', 'b1711:r3', 'b1711:r4'], 'source_paraphrase'), seg('若要回答当前现场值，还需核实已下发的有效定值和现场配置；文件封面版本或表格本身不能替代这项核对。', ['b1711:r1'], 'source_limit')],
        'targets': ['currency_limit', 'configuration_verification', 'unsupported_current_claim'],
    },
    {
        'base': 'gs_aux_b004_021', 'topic_id': 'T04', 'category': '纠错',
        'question': '请核对这句容量结论：“UPS概况表的80/10/15kVA可以直接分别由逆变器参数表的5/30kVA相加得到，双重化就是乘二。”',
        'answer_segments': [seg('不能直接这样配对或相加。UPS概况表列站公用80kVA、低端阀组各10kVA、交流场各15kVA；逆变器参数表列额定容量5kVA和30kVA。两处数值的层次和对应设备关系没有在这些条款中展开。', ['b2502:r2', 'b2502:r3', 'b2502:r4', 'b2502:r5', 'b2502:r6', 'b2502:r7', 'b2502:r8', 'b2502:r9', 'b2502:r10', 'b2514:r4', 'b2514:r5'], 'source_paraphrase'), seg('不能用“乘二”解释全部差异，也不能假定若干逆变器并联后恰好得到系统容量；需要设备明细、配置关系或修订说明。', ['p2049', 'b2502:r2', 'b2514:r4', 'b2514:r5'], 'source_limit')],
        'targets': ['unit_boundary', 'unsupported_arithmetic', 'configuration_unknown'],
    },
    {
        'base': 'gs_aux_b004_023', 'topic_id': 'T04', 'category': '纠错',
        'question': '已知公共充电机正常空载运行，能否把A组充电机故障后的接替写成“公共充电机自动接管，按某固定顺序切换并恢复”？请保留文档真正给出的范围。',
        'answer_segments': [seg('不能仅凭正常运行说明写出故障接替步骤。正文只给出A、B负荷母线与相应充电机、蓄电池母线的配置关系，以及公共充电机空载、阀组C段单独供电等正常状态信息。', ['p2048'], 'source_paraphrase'), seg('该段没有说明A组充电机故障时的开关操作、先后顺序、联锁或恢复条件；“公共充电机空载”不等于故障时已规定自动接替，更不能据此补出并联或送电流程。', ['p2048'], 'source_limit')],
        'targets': ['normal_vs_fault', 'procedure_completeness', 'unsupported_automation'],
    },
    {
        'base': 'gs_cooling_b005_006', 'topic_id': 'T05', 'category': '纠错',
        'question': '表中“压力低6.2bar、超低7bar、高7bar、超高7.2bar”能否直接整理成一组递进、无矛盾的保护阈值？',
        'answer_segments': [seg('不能直接整理成已确认的递进阈值。原表同时写压力低6.2bar、压力超低7bar、压力高7bar、压力超高7.2bar；“超低”数值高于“低”，且与“高”相同。', ['b2009:r4'], 'source_paraphrase'), seg('当前条款没有解释列值依据，也没有给出可替换的正确数值。应保留原表并标记待核对，不能自动交换高低、改小数点或借用其他厂家的阈值。', ['b2009:r4'], 'source_limit')],
        'targets': ['table_conflict', 'numeric_preservation', 'no_invention'],
    },
    {
        'base': 'gs_cooling_b005_016', 'topic_id': 'T05', 'category': '纠错',
        'question': '请审查这句自动化规则：“两主泵都故障且进阀压力低或进阀流量低，因此只要流量低就跳闸。”文档是否足以确定这种布尔关系？',
        'answer_segments': [seg('不能确定唯一的逻辑括号。原文和功能表都使用“两主泵都故障且进阀压力低或进阀流量低”的未括号表述，至少存在“两主泵都故障且（压力低或流量低）”与“（两主泵都故障且压力低）或流量低”两种读法。', ['p1649', 'b2020:r10'], 'bounded_synthesis'), seg('两种读法对单独流量低的结果不同；当前引用不足以选定逻辑分组，不能用编程语言的运算符优先级替代业务确认。应核对实际保护逻辑图或更明确条款。', ['p1649', 'b2020:r10'], 'source_limit')],
        'targets': ['logic_ambiguity', 'unsupported_boolean', 'condition_chain'],
    },
    {
        'base': 'gs_cooling_b005_028', 'topic_id': 'T05', 'category': '纠错',
        'question': '一处写重要参数三套传感器，另一处写“两套传感器全部接入两套阀冷却控制保护系统”。能否把它们统一改写成“三套传感器分别接入三套系统”？',
        'answer_segments': [seg('不能擅自统一。概述写进阀温度、流量、压力等重要参数配置三套传感器和三套冗余保护；防拒动段又写“两套传感器全部接入两套阀冷却控制保护系统”。', ['p1664', 'p1673'], 'source_paraphrase'), seg('两处计数和范围没有在引用内容中统一解释，不能把“两套”改成“三套”，也不能推断它们必然指不同对象。若要建立准确接线映射，仍需更明确配置资料。', ['p1664', 'p1673'], 'source_limit')],
        'targets': ['source_conflict', 'preserve_unknown', 'no_silent_normalization'],
    },
    {
        'base': 'gs_cooling_b005_029', 'topic_id': 'T05', 'category': '纠错',
        'question': '功能表把“IEC60044-8光模块故障”列在跳闸逻辑栏，能否据此生成“任意一个光模块故障立即闭锁全部直流”的确定规则？',
        'answer_segments': [seg('不能。该行只能证明表中列有“IEC60044-8光模块故障”这一故障条目；定值栏为“/”，没有展开具体模块范围、冗余数量或出口组合。', ['b2055:r1', 'b2055:r15'], 'source_paraphrase'), seg('文档没有给出单个或全部故障条件、延时及统一动作后果；“/”也不是零延时或零定值的说明，不能补成任意一个故障立即闭锁全部直流。', ['b2055:r15'], 'source_limit')],
        'targets': ['table_scope', 'unsupported_timing', 'unsupported_action'],
    },
]


def main():
    prior_rows = read_jsonl(PRIOR / 'qa.jsonl')
    prior_by_id = {r['id']: r for r in prior_rows}
    contexts = {c['id']: c for c in read_json(PRIOR / 'contexts.json')}
    rows = []
    for number, case in enumerate(CASES, 1):
        base = prior_by_id[case['base']]
        row = deepcopy(base)
        row['id'] = f'gs_target_b007_{number:03d}'
        row['batch_id'] = 'batch_007'
        row['category'] = case['category']
        row['question'] = '按姑苏站《设备概况》6.0版（2025年9月），' + case['question']
        row['answer_segments'] = case['answer_segments']
        row['answer'] = '\n\n'.join(s['text'] for s in row['answer_segments'])
        row['derived_from'] = [case['base']]
        row['reinforcement_targets'] = case['targets']
        row['contribution'] = '针对候选模型容易越界、压缩或丢失链路的边界纠错题；不引入留出文档。'
        row['quality'] = {'source_quote_check': 'reused_validated_context_and_evidence',
                          'semantic_review': 'author_self_review_only', 'expert_review': 'not_performed',
                          'source_currency': 'unverified', 'status': 'candidate',
                          'case_origin': 'synthetic_document_task_not_real_case',
                          'negative_example_role': 'adversarial_question_with_correct_bounded_answer'}
        evidence_ids = list(dict.fromkeys(ref for s in row['answer_segments'] for ref in s['source_ids']))
        refs = {e['id']: e for e in base['evidence']}
        assert set(evidence_ids) <= set(refs), (row['id'], evidence_ids, refs.keys())
        row['evidence'] = [refs[e] for e in evidence_ids]
        rows.append(row)
    combined = prior_rows + rows
    for key in ('id', 'question', 'answer'):
        normalized = [re.sub(r'[\W_]+', '', r[key]).casefold() for r in combined]
        assert len(set(normalized)) == len(combined), key
    mixed = {r['id'] for r in rows if int(r['id'][-3:]) % 2 == 0}
    mixed |= {i['id'] for i in read_jsonl(PRIOR / 'export_index.jsonl') if i['mixed_format'] == 'context'}
    OUT.mkdir(parents=True, exist_ok=True)
    exports(OUT, rows, contexts, mixed)
    exports(OUT / 'cumulative', combined, contexts, mixed)
    check_grid_edges()
    validation = {'batch': validate(OUT), 'cumulative': validate(OUT / 'cumulative'),
                  'grid_edge_checks': 'passed', 'semantic_review': 'not_independent',
                  'context_length_note': '字符数不是token数；不得无条件截断完整链路。'}
    write_json(OUT / 'validation.json', validation)
    report = {'batch': 'batch_007', 'new_qa': len(rows), 'prior_qa': len(prior_rows),
              'cumulative_qa': len(combined), 'topic_counts': {t: sum(r['topic_id'] == t for r in rows) for t in sorted({r['topic_id'] for r in rows})},
              'negative_example_role': 'adversarial questions with bounded/correct answers; no wrong answer used as target',
              'source_isolation': 'passed', 'training_run': False, 'expert_review': 'not_performed',
              'derived_from_baseline_error_categories': ['size_or_unit_merge', 'procedure_truncation', 'unknown_vs_defined', 'source_conflict', 'unsupported_guarantee', 'boolean_grouping']}
    write_json(OUT / 'quality_report.json', report)
    (OUT / 'README.md').write_text('# 第七批：边界纠错补强候选\n\n本批10条，累计160条。样本全部复用已核验训练来源上下文和证据，不包含FAN/CAAP留出文本或答案。所谓负例是用户问题中的诱导性错误说法，assistant目标仍是有证据边界的正确回答；未训练、未专家审核。\n', encoding='utf-8')
    print(json.dumps({'validation': validation, 'report': report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
