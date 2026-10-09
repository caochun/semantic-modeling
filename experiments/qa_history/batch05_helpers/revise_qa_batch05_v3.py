import json
from pathlib import Path
ROOT=Path('/Users/chun/Develop/semantic-modeling');d=ROOT/'scripts/qa_operations';src=json.loads((d/'quality_seed_batch05_v2.json').read_text())
# Remove generic, out-of-scope initial-report lead added to every full chain.
for c in src['chains']:
 for q in c['questions']:
  for phrase in [' 国调管辖系统事故须立即首报概况，查明后尽快详细汇报，查因不能延迟初报。','国调管辖事故立即首报，查明后尽快详细汇报。']:
   q['lead']=q.get('lead','').replace(phrase,'').strip()
# Tighten facts to exact supported propositions.
for c in src['chains']:
 for f in c['facts']:
  if c['id']=='bipole_block' and f['id']=='verify_filters':
   f['text']='检查交流滤波器是否全部退出运行。'
  if c['id']=='bipole_block' and f['id']=='maintenance':
   f['text']='将双极（故障设备）隔离并转检修，通知检修人员处理。'
  if c['id']=='pole_block_and_ground_current' and f['id']=='metallic_conversion_failure':
   f['text']='单极大地回线与金属回线相互转换不成功时，由主控站协调对站返回原接线；不能返回时，可向值班调度员申请将该极正常停运，待转换接线方式后恢复该极。'
  if c['id']=='dc_power_rundown' and f['id']=='small_group_request':
   f['text']='若属于小组保护动作，应检查备用滤波器是否正常投入；滤波器需求不满足时，按6.15申请手动投入备用。3.5另规定备用投入后再向调度申请恢复输送功率。'
  if c['id']=='minimum_filter_and_rpc' and f['id']=='rpc_priorities':
   f['text']='无功手动模式仅高优先级控制策略有效，包括交流过电压、绝对最小滤波器、交流母线Umax/Umin和Qmax；Min filter及Q/U控制模块无效。RPC手动时，相关最小滤波器和Q/U投切由运行人员手动完成。'
  if c['id']=='minimum_filter_and_rpc' and f['id']=='replacement_times':
   f['text']='保护跳闸滤波器按需求由另一组替代：属于ABS时在1s内投入另一组，属于Min时在5s内投入另一组。'
  if c['id']=='dc_line_protection_trip' and f['id']=='onsite_isolation_filters':
   f['text']='现场检查故障极隔离情况及交流滤波器投退情况。'
# Keep scope-gap conclusions in lead/tail, where they are explicitly labeled as document scope.
for c in src['chains']:
 for q in c['questions']:
  if c['id']=='bipole_block' and q['key']=='filters_assumed':
   q['lead']='规程在现象之外另列了交流滤波器退出情况的检查，因此应按该检查要求核实。'
  if c['id']=='bipole_block' and q['key']=='restore_without_repair':
   q['lead']='本节列出的处置终点是隔离、转检修和通知检修；不能仅凭这几条补写故障设备恢复操作。'
  if c['id']=='dc_line_protection_trip' and q['key']=='signal_isolation_enough':
   q['lead']='本条明确要求现场检查，监控显示本身不能替代题目所问的现场检查。'
  if c['id']=='dc_power_rundown' and q['key']=='whole':
   q['lead']=q.get('lead','').replace('国调管辖事故立即首报，查明后尽快详细汇报。','')
# Make direct source wording for whole answer context, with explicit non-procedural scope.
src['version']='20261008-quality-batch05-v3'
(d/'quality_seed_batch05_v3.json').write_text(json.dumps(src,ensure_ascii=False,indent=2)+'\n')
print({'qa':sum(len(c['questions']) for c in src['chains']),'facts':sum(len(c['facts']) for c in src['chains'])})
