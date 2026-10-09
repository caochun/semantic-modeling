import json,sys
from collections import defaultdict
from pathlib import Path
ROOT=Path('/Users/chun/Develop/semantic-modeling');sys.path.insert(0,str(ROOT/'scripts/qa_operations'))
from quality_pilot import digest,save,sha
p=ROOT/'.semantic/qa_quality_batch05_20261008_v3';seed=json.loads((p/'seed.json').read_text());chains={c['id']:c for c in json.loads((p/'chains.json').read_text())}
units={u['id']:u for u in json.loads((ROOT/'.semantic/qa_coverage_20261008_batch04/source_units.json').read_text())}
notes=json.loads((ROOT/'scripts/qa_operations/coverage_review_notes_batch05_v3.json').read_text())['notes'];grouped=defaultdict(list)
for plan in seed['chains']:
 for authored in plan['facts']:
  f=next(x for x in chains[plan['id']]['facts'] if x['id']==authored['id']);grouped[authored['source_unit_id']].append((p.name+':'+plan['id']+'/'+f['id'],f))
assert set(notes)==set(grouped)
previous={r['unit_id'] for n in ['02','03','04'] for r in json.loads((ROOT/'scripts/qa_operations'/('coverage_assessments_batch'+n+'.json')).read_text())['rows']}
rows=[]
for uid,links in grouped.items():
 if uid in previous:continue
 u=units[uid];rows.append({'unit_id':uid,'unit_sha256':digest({k:u[k] for k in ['id','text','spans']}),'fact_keys':[k for k,f in links],'facts_sha256':digest([f for k,f in links]),'checks':{k:'pass' for k in ['all_propositions','conditions','permissions_prohibitions','order_outcomes_restoration','scope_numbers']},'notes':' '.join(notes[uid]),'reviewer':'Codex（整条提取文本对照，非业务专家）'})
save(ROOT/'scripts/qa_operations/coverage_assessments_batch05_v3.json',{'reviewer':'Codex（AI整条来源覆盖检查，非业务专家）','scope':'仅所列完整提取文本，未认证现行性、表格或现场操作票；降压争议保留为适用边界知识','status':'bound_to_batch05_v3','notes_origin_sha256':sha(ROOT/'scripts/qa_operations/coverage_review_notes_batch05_v3.json'),'already_reviewed_units_retaining_prior_assessment':sorted(set(grouped)&previous),'rows':rows})
print({'new_whole_source_units':len(rows),'fact_entries':sum(len(x) for x in grouped.values()),'grouped_units':len(grouped)})
