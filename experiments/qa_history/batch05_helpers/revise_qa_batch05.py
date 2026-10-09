import json,re
from pathlib import Path
ROOT=Path('/Users/chun/Develop/semantic-modeling');d=ROOT/'scripts/qa_operations'
seed=json.loads((d/'quality_seed_batch05.json').read_text());cs={c['id']:c for c in seed['chains']}
notes=json.loads((d/'source_review_notes_batch05.json').read_text());coverage=json.loads((d/'coverage_review_notes_batch05.json').read_text())
units={u['id']:u for u in json.loads((ROOT/'.semantic/qa_coverage_20261008_batch04/source_units.json').read_text())}

def question(cid,key):return next(q for q in cs[cid]['questions'] if q['key']==key)
def fact(cid,fid):return next(f for f in cs[cid]['facts'] if f['id']==fid)
def add(cid,fid,uid,text,note):
 c=cs[cid];u=units[uid]
 c['facts'].append({'id':fid,'quote':re.sub(r'\s+','',u['text'])[:44],'text':text,'source_unit_id':uid})
 if uid not in c['source_unit_ids']:
  c['source_unit_ids'].append(uid);c['source_spans'] += [{k:s[k] for k in ['source_id','block_id','start','end']} for s in u['spans']]
 coverage['notes'].setdefault(uid,[]).append(note)

def order(cid,ids):
 c=cs[cid];by={f['id']:f for f in c['facts']};assert set(ids)==set(by)
 c['facts']=[by[i] for i in ids];question(cid,'whole')['required']=ids

# Strip an appendix label that is not grounded in the supplied heading context.
fact('minimum_filter_and_rpc','abs_behavior')['text']=fact('minimum_filter_and_rpc','abs_behavior')['text'].replace('第三分册附录J','第三分册')

# Explicit guard: emergency actions cannot be delayed by the printed list order.
for cid in ['pole_block_and_ground_current','single_converter_block']:
 c=cs[cid];ids=[f['id'] for f in c['facts']];ids.remove('master_emergency');ids.insert(4,'master_emergency');order(cid,ids)
 c['order_edges']=[['master_emergency','overload_mode' if cid=='pole_block_and_ground_current' else 'overload_scope']]
 for q in c['questions']:
  if set(c['order_edges'][0]) <= set(q['required']):
   q['required'].remove('master_emergency');q['required'].insert(q['required'].index(c['order_edges'][0][1]),'master_emergency')
 question(cid,'whole')['lead']='现象与专项要求按适用状态核对，编号不表示现场逐步操作票。出现所列主控站过负荷状态时，立即控功率并立即汇报，不能等方式调整、录波整理或转检修完成。国调管辖系统事故立即首报概况，查明后尽快详细汇报。'
 notes['notes'][cid+'/whole']+=' v2把主控站立即控功率置于方式申请前，并明确编号不产生等待授权。'

# All conversion conditions in expanded source must remain visible with the recommendation.
add('pole_block_and_ground_current','metallic_conversion_checks','d_4b964d3ff677714b/cp42776_42910','大地回线与金属回线转换一般采用顺控，旁路接地刀闸采用步进。操作前两站确认转换回路设备正常、相关开关/刀闸/接地刀闸具备远方操作条件；操作后两站检查状态，转换中保持联系并互报操作情况。','完整保留一般顺控、旁路接地刀闸步进、两端前后核查和过程中联系。')
add('pole_block_and_ground_current','shared_ground_check','d_4b964d3ff677714b/cp42911_42940','开展大地金属回线转换前，应确认共用接地极电流不超限；不能将核实转换条件简化成只看开关位置。','共用接地极电流不超限、转换前时序及对象完整。')
add('pole_block_and_ground_current','metallic_conversion_failure','d_4b964d3ff677714b/cp43073_43165','单极大地回线与金属回线相互转换不成功时，由主控站协调对站返回原接线；不能返回时，可申请值班调度员将该极正常停运，待转换接线方式后恢复该极。本条与故障闭锁段的立即入地限流分别保留，返原方式不能代替或拖延限流要求。','转换失败、主控协调对站返原、不能返回申请正常停运、待转换后恢复四组命题完整。')
c=cs['pole_block_and_ground_current'];ids=[f['id'] for f in c['facts']]
for fid in ['metallic_conversion_checks','shared_ground_check','metallic_conversion_failure']:ids.remove(fid)
pos=ids.index('nonline_branch');ids[pos:pos]=['metallic_conversion_checks','shared_ground_check'];ids.insert(ids.index('nonline_branch')+1,'metallic_conversion_failure');order(c['id'],ids)
c['order_edges'] += [['metallic_conversion_checks','nonline_branch'],['shared_ground_check','nonline_branch']]
for key in ['whole','two_hours_not_wait','line_fault_olt','changed_failure_cause']:
 q=question(c['id'],key)
 if key!='whole':q['question']=q['question'].replace('非线路故障使一极闭锁，','一极因非线路故障闭锁、另一极过负荷，').replace('线路故障导致闭锁极停运，','一极因线路故障闭锁、另一极过负荷，').replace('后来核实是线路故障，','一极闭锁且另一极过负荷，后来核实是线路故障，')
 if key=='changed_failure_cause':
  q['history'][0]['content']='一极因非线路故障闭锁，另一极过负荷，已核实运行极具备包括共用接地极电流不超限在内的金属回线转换条件。'
  q['standalone_question']='一极闭锁且另一极过负荷，先被认定非线路故障且已核实全部转换条件，后来核实为线路故障且闭锁极无带线路OLT条件，能否沿用原建议？'
  q['lead']='原文的线路/非线路故障分支需要按最新信息重新选择，不能沿用原金属回线建议。本次不具备带线路OLT条件，立即把入地电流控制到3000A安全限值或以下。'
 notes['notes'][c['id']+'/'+key]+=' v2保留单极闭锁另极过负荷背景；完整题增加转换前共用电流、两站设备及远控检查和失败返回条件。'
question(c['id'],'whole')['lead']+=' 回线转换前必须核实全部转换条件，包括共用接地极电流不超限；不能用转换方案绕过立即限流要求。'

# Put filter prerequisites before instructions to insert and restore power.
c=cs['dc_power_rundown']
ids=[f['id'] for f in c['facts']];ids.remove('filter_branches');ids.insert(ids.index('small_group_request')+1,'filter_branches');order(c['id'],ids)
c['order_edges']=[['switch_available','filter_branches'],['available_time','filter_branches'],['manual_forbidden_during_ramp','filter_branches'],['small_group_request','filter_branches']]
question(c['id'],'whole')['lead']='核查现象后按确认的回降原因选择分支；备用投入前先核可用条件、退出/放电时间、功率是否仍在升降及方式是否正在转换。小组保护需求不满足时先按6.15申请手动投入备用，投入成功后再申请恢复功率。国调管辖事故立即首报，查明后尽快详细汇报。'
for key in ['whole','recover_before_filter','backup_failed_followup']:
 q=question(c['id'],key)
 if key!='whole':
  req=q['required'];req.remove('filter_branches');req.append('filter_branches')
 q['tail']='恢复功率涉及当前方式与ABS等限制变化时，还须核查功率计划曲线预设值是否越限，及时申请调度调整；不能认为投入一组备用就保证原计划全都可用。'
 notes['notes'][c['id']+'/'+key]+=' v2把相关申请及前提置于恢复建议之前，补第三分册cp50255功率计划随限制变化检查。'
question(c['id'],'backup_failed_followup')['lead']='这组备用不再满足可用条件，应重新核查是否还有其他可用备用；确无可用备用时选择申请降低功率至报警复归分支。不能继续沿用当前这一组的投入建议。'
notes['notes'][c['id']+'/backup_failed_followup']+=' 这组锁定不证明全站无其他备用，显式区分重新核其他备用与确无备用分支。'

# Min-mode general behavior must be conditioned on the current mode.
question('minimum_filter_and_rpc','whole')['tail']='上述Min自动投入能力须结合当前模式：RPC手动时Min模块无效，由运行人员承担对应投切。手动投切仍受第三分册功率升降/方式转换期间禁止操作、完整可用条件、退出超过10min及放电时间限制，必要时按6.11申请调度，不能因切为手动而绕过。'
notes['notes']['minimum_filter_and_rpc/whole']+=' v2显式把Min通用能力放回当前RPC模式约束，手动操作仍服从可用性、时间、投切禁令和调度申请。'
question('minimum_filter_and_rpc','min_equals_abs')['tail']='Min能否自动补投还要核对RPC模式，手动模式下该模块无效。'
question('minimum_filter_and_rpc','manual_changed')['lead']='当前RPC手动时Min模块无效，不能继续期待它自动补投；相关最小滤波器需求的投切由运行人员手动承担，高优先级ABS等仍有效。'

# Scope the ground-current branch and its immediate timing visibly in line-trip questions.
for key in ['whole','olt_failure','return_to_service_ticket']:
 q=question('dc_line_protection_trip',key)
 if key=='olt_failure':q['question']=q['question'].replace('线路故障闭锁后，','一极因线路故障闭锁且另一极过负荷，')
 if key=='whole':q['lead']='核查现象并立即汇报后，按实际状态处理。涉及单极闭锁另极过负荷的入地电流分支时，立即要求不能等录波整理或检修完成才执行。国调管辖系统事故立即首报概况，查明后尽快详细汇报。'
 notes['notes']['dc_line_protection_trip/'+key]+=' v2显式限定引用的单极闭锁另极过负荷背景，保留立即动作及试验成功才恢复。'
fact('dc_line_protection_trip','line_fault_ground')['text']='在第三分册所述一极闭锁、另一极过负荷的入地电流处理背景下，因线路故障闭锁时，核实闭锁极具备带线路OLT条件后立即试验，成功后恢复闭锁极；条件不具备或失败时，立即把入地电流控制到3000A安全限值或以下。该段未给完整试验操作票。'
question('dc_line_protection_trip','whole')['required'].remove('line_fault_ground');question('dc_line_protection_trip','whole')['required'].insert(5,'line_fault_ground')
# No arbitrary total ordering for protections/checks; only an explicit emergency precedence.
cs['dc_line_protection_trip']['order_edges']=[['line_fault_ground','records']]

seed['version']='20261008-quality-batch05-v2'
(d/'quality_seed_batch05_v2.json').write_text(json.dumps(seed,ensure_ascii=False,indent=2)+'\n')
(d/'source_review_notes_batch05_v2.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2)+'\n')
(d/'coverage_review_notes_batch05_v2.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2)+'\n')
print({'qa':sum(len(c['questions']) for c in cs.values()),'facts':sum(len(c['facts']) for c in cs.values())})
