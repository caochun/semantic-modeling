import hashlib,json,shutil,zipfile
from pathlib import Path
ROOT=Path('/Users/chun/Develop/semantic-modeling')
BASE=ROOT/'.semantic/qa_quality_coverage_20261008_batch04_bundle.zip';DEST=ROOT/'.semantic/qa_quality_coverage_20261008_batch05_bundle.zip'
assert BASE.is_file() and not DEST.exists()
updates={}
for name in ['.semantic/qa_quality_batch05_20261008_v3','.semantic/qa_reviewed_20261008_batch05','.semantic/qa_print_20261008_batch05','.semantic/qa_coverage_20261008_batch05']:
 d=ROOT/name
 for p in d.rglob('*'):
  if p.is_file() and 'review.lock' not in p.name and '__pycache__' not in p.parts:updates[str(p.relative_to(ROOT))]=p
for p in (ROOT/'scripts/qa_operations').rglob('*'):
 if p.is_file() and '__pycache__' not in p.parts:updates[str(p.relative_to(ROOT))]=p
for name in ['QA_DATASET.md','docs/QA生成质量控制.md','docs/QA覆盖边界与业务信息盘点.md','docs/QA质量扩展批次记录.md','tests/test_qa_quality.py','tests/test_qa_coverage.py']:
 updates[name]=ROOT/name
def permitted(name):
 parts=Path(name).parts
 return not name.endswith('/') and '.env' not in parts and 'rawdoc' not in parts and '__pycache__' not in parts and not name.endswith('.pyc') and not name.endswith('/review.lock')
catalog=[];changed=[]
with zipfile.ZipFile(BASE) as prior,zipfile.ZipFile(DEST,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 names=set(prior.namelist())
 for name in sorted(names|set(updates)):
  if not permitted(name):continue
  data=updates[name].read_bytes() if name in updates else prior.read(name);z.writestr(name,data);catalog.append({'path':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
  if name in updates and name in names and prior.read(name)!=data:
   old='.semantic/qa_batch05_previous_snapshot/'+name;z.writestr(old,prior.read(name));catalog.append({'path':old,'sha256':hashlib.sha256(prior.read(name)).hexdigest(),'bytes':len(prior.read(name))});changed.append(name)
 z.writestr('qa_batch05_package_contents.json',json.dumps({'qa':180,'new_batch_qa':53,'business_units':27,'whole_source_units_reviewed':235,'previous_bundle':BASE.name,'updated_previous_entries_preserved':changed,'expert_review':'not_performed','training_export_created':False,'entries':catalog},ensure_ascii=False,indent=2)+'\n')
with zipfile.ZipFile(DEST) as z:
 assert z.testzip() is None;assert len(z.namelist())==len(set(z.namelist()))
 assert all(permitted(n) for n in z.namelist())
 for row in catalog:assert hashlib.sha256(z.read(row['path'])).hexdigest()==row['sha256']
 for name,p in updates.items():assert z.read(name)==p.read_bytes()
receipt={'path':str(DEST.relative_to(ROOT)),'bytes':DEST.stat().st_size,'sha256':hashlib.sha256(DEST.read_bytes()).hexdigest(),'entries':len(z.namelist()),'verified_current_files':len(updates),'qa':180,'new_batch_qa':53,'whole_source_units_reviewed':235,'raw_documents_included':False,'credentials_included':False,'new_training_export':False}
(ROOT/'.semantic/qa_quality_coverage_20261008_batch05_bundle_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n');print(json.dumps(receipt,ensure_ascii=False))
