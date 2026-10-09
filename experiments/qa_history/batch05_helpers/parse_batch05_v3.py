import json,sys
from pathlib import Path
sys.path.insert(0,'scripts/qa_operations');from review_quality import decode_json
p=Path('.semantic/qa_quality_batch05_20261008_v3')
# Parse fact responses exactly; retain all raw files untouched.
facts={}
for s in (p/'fact_review/responses.jsonl').read_text().splitlines():
 r=json.loads(s);facts[r['id']]=decode_json(r['answer'])
json.dump(facts,(p/'model_fact_reviews.json').open('w'),ensure_ascii=False,indent=2);(p/'model_fact_reviews.json').write_text((p/'model_fact_reviews.json').read_text()+'\n')
reviews=[];expected={c['id']:{r['id'] for r in [json.loads(x) for x in (p/'candidates.jsonl').read_text().splitlines()] if r['business_unit_id']==c['id']} for c in json.loads((p/'chains.json').read_text())}
for s in (p/'semantic_review/responses.jsonl').read_text().splitlines():
 r=json.loads(s);txt=r['answer']
 try: obj=decode_json(txt); arr=obj['reviews']
 except Exception:
  # Provider emitted one malformed object with `]` instead of `}` and duplicated tail fields.
  fixed=txt.replace('reanswer_disagreements":[]],"reason"','reanswer_disagreements":[],"reason"',1)
  try: arr=decode_json(fixed)['reviews']
  except Exception:
   arr=[]
  # The repaired object is treated as provider-format hold below.
  repaired=True
 else: repaired=False
 for x in arr:
  checks=x.get('checks',{});extras={k:v for k,v in checks.items() if k not in {'question_clear','source_support','required_coverage','condition_branches','step_order','prohibitions_and_permissions','restoration','expanded_context','history_dependency'}}
  if extras:
   x['format_issues']={'unknown_check_fields':extras};x['provider_checks']=dict(checks);x['checks']={k:v for k,v in checks.items() if k not in extras};x['verdict']='hold'
  if repaired and x['id']=='minimum_filter_and_rpc/one_or_five_seconds':
   x['format_issues']={**x.get('format_issues',{}),'malformed_provider_json':True};x['verdict']='hold'
  reviews.append(x)
# Missing expected review IDs are held as provider format failure; source review later does independent check.
for cid,ids in expected.items():
 got={x['id'] for x in reviews if x['id'].startswith(cid+'/')}
 for missing in sorted(ids-got):reviews.append({'id':missing,'verdict':'hold','checks':{k:'uncertain' for k in ['question_clear','source_support','required_coverage','condition_branches','step_order','prohibitions_and_permissions','restoration','expanded_context','history_dependency']},'missing_fact_ids':[],'unsupported_claims':[],'reanswer_disagreements':[],'evidence_ids':[],'reason':'provider response malformed; no parsed review retained','suggested_revision':'','format_issues':{'malformed_provider_json':True}})
assert {x['id'] for x in reviews}=={r['id'] for r in [json.loads(x) for x in (p/'candidates.jsonl').read_text().splitlines()]}
json.dump(reviews,(p/'model_semantic_reviews.json').open('w'),ensure_ascii=False,indent=2);(p/'model_semantic_reviews.json').write_text((p/'model_semantic_reviews.json').read_text()+'\n')
json.dump({'state':'model_review_complete_provider_format_hold','fact_review_requests':7,'blind_reanswer_requests':53,'semantic_review_requests':7,'training_eligible':False,'expert_review':'not_performed','model':'recorded GLM'},(p/'quality_review_status.json').open('w'),ensure_ascii=False,indent=2);print({'facts':len(facts),'reviews':len(reviews),'holds':sum(x['verdict']=='hold' for x in reviews)})
