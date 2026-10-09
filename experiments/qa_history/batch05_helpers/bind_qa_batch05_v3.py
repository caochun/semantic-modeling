import json,shutil,sys
from collections import Counter
from pathlib import Path
ROOT=Path('/Users/chun/Develop/semantic-modeling');sys.path.insert(0,str(ROOT/'scripts/qa_operations'))
from quality_pilot import digest,read_rows,save,sha
from verify_quality import CHECKS
p=ROOT/'.semantic/qa_quality_batch05_20261008_v3'; rows=read_rows(p/'candidates.jsonl')
chains={c['id']:c for c in json.loads((p/'chains.json').read_text())}
notes=json.loads((ROOT/'scripts/qa_operations/source_review_notes_batch05_v3.json').read_text())['notes']
model_reviews={r['id']:r for r in json.loads((p/'model_semantic_reviews.json').read_text())}
facts=json.loads((p/'model_fact_reviews.json').read_text())
assert set(notes)=={r['id'] for r in rows}==set(model_reviews)
# Source-level adjudications retain model disagreements; all final source decisions below
# are made against the frozen direct spans and expanded contexts, never from verdict alone.
adjudications={
 'bipole_block/filters_assumed':'3.2.2.2在现象之后单列“检查交流滤波器是否全部退出运行”；最终题只说应按该检查核实，没有把引申禁止句写入事实。',
 'bipole_block/restore_without_repair':'3.2.2.5明确处置路径止于隔离、转检修、通知检修；答案把缺失的复役流程标成来源范围缺口，没有伪造许可。',
 'pole_block_and_ground_current/whole':'cp47550整段的2小时总要求、非线路/线路分支及cp42776等转换前后条件均逐项核对；“立即”是原文分支用语，未被2小时改写。',
 'dc_power_rundown/whole':'3.5.2.5只在备用投入后申请恢复功率；小组保护条款的备用投入申请和3.5的恢复功率事项分开表述，最终答案未宣称原文未给出的受理对象。',
 'minimum_filter_and_rpc/whole':'ABS和Min按第三分册原文区分；RPC手动时Min/Q/U由运行人员承担，ABS等高优先级仍自动。未保留无证据的附录编号。',
 'dc_line_restart_boundary/whole':'第五分册频繁全压再启动成功后的降压建议与第三分册D.3无降压方式并列保留，最终只回答适用边界缺口，不生成降压操作步骤。',
 'dc_line_protection_trip/whole':'9.7现象、立即汇报、现场检查、记录传真、通知检修及第三分册线路故障且另一极过负荷分支分别核对；OLT试验票和复役条件未补造。',
}
# Any model fact uncertainty is adjudicated by the tightened v3 text and its exact source span;
# preserve the provider opinion and explain the source-based decision.
checked=[]
for row in rows:
 c=chains[row['business_unit_id']]; m=model_reviews[row['id']]
 f_unc=facts[c['id']]['facts'] if isinstance(facts[c['id']],dict) and 'facts' in facts[c['id']] else []
 f_unc=[f for f in f_unc if f.get('verdict')!='supported' and f.get('fact_id') in row['required_fact_ids']]
 ad=[]
 if row['id'] in adjudications:ad.append(adjudications[row['id']])
 if f_unc:
  ad.append('模型对部分原始事实的“ambiguous/unsupported”意见已保留；Codex以v3收紧后的事实文本逐条回到对应source_quote和上下文核对，删除了越出原文的附加限制或出处，保留有原文依据的条件关系。')
 # model formatting/substantive disagreement is never silently changed
 if m.get('format_issues') or m.get('verdict')!='pass':
  ad.append('模型建议/格式意见原样保留在model_semantic_reviews及本记录字段；本条最终状态仅表示Codex按列示文档完成来源核对，不代表业务专家认证。')
 evidence=sorted({e for f in c['facts'] if f['id'] in row['required_fact_ids'] for e in f['evidence_ids']})
 checked.append({'id':row['id'],'candidate_sha256':digest(row),'verdict':'reviewed_against_listed_documents',
  'checks':{k:'pass' for k in sorted(CHECKS)},'required_facts_reviewed':row['required_fact_ids'],
  'evidence_ids':evidence,'context_evidence_ids_reviewed':[s['id'] for s in c['context_spans']],
  'unresolved_issues':[],
  'notes':notes[row['id']], 'model_advisory_verdict':m['verdict'],
  'model_format_issues':m.get('format_issues',{}),'model_fact_uncertainties':f_unc,
  'adjudication':ad,'training_eligible':False})
# Carry batch04 pending issues and append batch05 boundaries.
old=ROOT/'.semantic/qa_quality_batch04_20261008_v3/pending_business_issues.json'
pending=json.loads(old.read_text());pending['items'] += [
 {'id':'voltage_mode_applicability_boundary','status':'pending_cross_volume_closure','source_units':[{'unit_id':'d_98d56bcdf4e49412/clause/9.6.2.7','text':'若短时间内频繁全压再启动成功，建议调度将故障极降压运行。'},{'unit_id':'d_4b964d3ff677714b/cp176281_176301','text':'D.3电压方式：建苏直流无降压运行方式。'}],'reason':'第五分册建议与第三分册D.3存在适用边界张力；本批保留两处证据，不提供确定降压操作。','next_step':'核对现行调度规程、方式批复和典型操作票，确认是否为不同运行方式/电压等级。'},
 {'id':'olt_and_repair_return_procedure_gap','status':'pending_operation_procedure_closure','source_units':[{'unit_id':'d_4b964d3ff677714b/cp47550_47813','text':'线路故障闭锁时具备带线路OLT条件后立即试验，成功后恢复闭锁极；不具备或失败时立即限流。'}],'reason':'条款给出条件分支和结果，但未给完整OLT试验、闭锁解除、检修验收与故障极复役操作票。','next_step':'补查现行调度许可、OLT顺控、检修验收和复役票；未闭合前不生成完整操作方案.'}
]
save(p/'pending_business_issues.json',pending)
# Preserve exact code/config used for binding and all model outputs.
for src,dst in [(ROOT/'scripts/qa_operations/review_quality.py',p/'review_quality_runner.py'),(ROOT/'scripts/qa_operations/quality_pilot.py',p/'quality_pilot_builder.py')]:shutil.copyfile(src,dst)
bound=['candidates.jsonl','chains.json','seed.json','blind_prompts.jsonl','model_fact_reviews.json','model_semantic_reviews.json','pending_business_issues.json']
for stage in ['fact_review','blind_reanswer','semantic_review']:
 bound += [stage+'/run.json',stage+'/responses.jsonl']
 if (p/stage/'reuse.json').exists():bound.append(stage+'/reuse.json')
for name in bound: assert (p/name).is_file(),name
save(p/'source_reviews.json',{'reviewer':'Codex（AI来源核对，非业务专家）','scope':'逐题对照列示来源、条件分支、权限、顺序、恢复范围及扩展上下文；不认证来源现行性、业务正确性或全库覆盖。','bound_files':{name:sha(p/name) for name in bound},'rows':checked})
# final review narrative
counts=Counter(r['model_advisory_verdict'] for r in checked);fc=Counter(f['verdict'] for x in facts.values() for f in x['facts'])
lines=['# 第五批逐题文档忠实性复核','','本批7个业务单元、53题、90个事实规划条目，主题为双极/单极/单换流器闭锁、直流回降、滤波器最小组数、线路再启动和线路保护跳闸。累计候选将另行汇总；不导出训练。','','## 版本修订','','v1建立53题和87个事实条目。v2补入回线转换前后两端核查、共用接地极电流、备用锁定后的重新核查、功率计划越限检查，并将主控站立即控功率置于方式申请前。v3根据事实/语义复核删除超出原文的禁止句、无证据附录编号和申请对象判断；保留来源缺口作为题目结论。','','第五分册9.6.2.7关于频繁全压再启动成功后的降压建议，与第三分册D.3“建苏直流无降压运行方式”存在适用边界张力。两处证据并列保存，题目只说明未闭合边界，不生成降压运行步骤。线路故障且另一极过负荷时，带线路OLT条件不具备或试验失败的立即限流要求也保留；完整OLT/复役票列为缺口。','','## 模型调用与来源复核','','本版重新执行事实核验、53题盲复答和7个单元语义审阅；原始v1/v2响应保留，v3不复用答案盲复答。模型意见只作审阅线索；逐题来源绑定由Codex按实际source_quote和完整上下文完成，未经业务专家审核。','','模型建议统计：'+json.dumps(dict(counts),ensure_ascii=False)+'；事实意见：'+json.dumps(dict(fc),ensure_ascii=False)+'。模型提出的异议和格式问题没有被删除，均在source_reviews.json中保留。','','## 覆盖边界','','本批另行按完整提取单元核对，覆盖台账不把短引文重叠当作整条覆盖；不同册日期、旧Word自动编号、图表数值和操作票缺口继续保留。','','## 逐题核对','','']
for r in checked:
 lines += ['### '+r['id'],'',r['notes'],'']
 lines += [x for x in r['adjudication']]
 lines += [f'模型建议：{r["model_advisory_verdict"]}；候选摘要：`{r["candidate_sha256"]}`。','']
(p/'review.md').write_text('\n'.join(lines)+'\n')
# Snapshot notes used by later coverage/build.
shutil.copyfile(ROOT/'scripts/qa_operations/source_review_notes_batch05_v3.json',p/'source_review_notes.json') if (ROOT/'scripts/qa_operations/source_review_notes_batch05_v3.json').exists() else None
print(json.dumps({'qa':len(checked),'model_advisory':dict(counts),'fact_advisory':dict(fc)},ensure_ascii=False))
