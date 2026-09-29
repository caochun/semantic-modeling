// Problem-local views reference shared knowledge. Text is always escaped.
let memoryView='items', modelRequest=0;
function evidenceButtons(items) {
  return (items||[]).map(e=>`<button class="citation" data-passage="${esc(e.passage_id)}" title="查看联系的原文依据">据</button>`).join('');
}
function modelStatus(model) {
  const status=model.effective_status||model.status;
  const names={reviewed:'组合复核通过',candidate:'局部模型待核查',historical:'历史引用记录',unavailable:'依据已变化，需重新核查'};
  return `<span class="pill ${esc(status)}">${esc(names[status]||status)}</span>`;
}
function modelList(title,values) {
  return values?.length?`<div class="model-list"><h3>${esc(title)}</h3><ul>${values.map(v=>`<li>${esc(v)}</li>`).join('')}</ul></div>`:'';
}
function knowledgeNode(id,title,exists=true) {
  return exists?`<button class="relation-node" data-open-knowledge="${esc(id)}">${esc(title)}</button>`:`<span class="relation-node missing">${esc(title)} · 已清空</span>`;
}
function relationView(link,uses) {
  const source=uses.find(u=>u.knowledge_id===link.source_id),target=uses.find(u=>u.knowledge_id===link.target_id);
  const name=u=>u?.knowledge?.title||u?.snapshot?.title||'历史知识';
  return `<div class="model-relation"><div class="relation-path">${knowledgeNode(link.source_id,name(source),!!source?.knowledge)}<span class="relation-arrow">${esc(link.relation)} →</span>${knowledgeNode(link.target_id,name(target),!!target?.knowledge)}</div><p>${esc(link.explanation)} ${evidenceButtons(link.evidence)}</p><p class="scope">适用范围：${esc(link.scope)}</p>${modelList('成立条件',link.conditions)}<p class="muted">${link.status==='reviewed'?'联系复核通过':'联系待核查'}：${esc(link.review?.reason||'未取得复核结果')}</p>${link.review?.check_errors?.length?`<p class="notice">${esc(link.review.check_errors.join('；'))}</p>`:''}</div>`;
}
function renderLocalModel(model) {
  if(!model)return;
  $('#local-model-panel').classList.remove('hidden');
  $('#local-model-state').innerHTML=modelStatus(model);
  const uses=model.knowledge_uses||[];
  $('#local-model').innerHTML=`<p class="model-objective">${esc(model.objective)}</p><p class="scope">本轮范围：${esc(model.scope)}</p><p class="muted">${esc(model.review?.reason||'')}</p>${model.review?.check_errors?.length?`<p class="notice">${esc(model.review.check_errors.join('；'))}</p>`:''}<h3 class="model-section-title">本次判断如何使用知识</h3><div class="knowledge-grid">${uses.length?uses.map(u=>`<div class="knowledge-card"><span class="pill">${u.origin==='new'?'本轮新增':'引用已有知识'}</span>${!u.available?'<span class="pill candidate">当前不可自动复用</span>':''}<h3>${esc(u.knowledge?.title||u.snapshot?.title||u.knowledge_id)}</h3><p class="model-role">${esc(u.role)}</p><p>${esc(u.knowledge?.statement||u.snapshot?.statement||'')}</p><p class="scope">${esc(u.knowledge?.scope||u.snapshot?.scope||'')}</p>${u.knowledge?`<button class="subtle" data-open-knowledge="${esc(u.knowledge_id)}">查看共享知识与出处</button>`:''}</div>`).join(''):'<p class="muted">本轮没有保存或复用知识条目。</p>'}</div><h3 class="model-section-title">知识之间如何联系</h3>${model.links?.length?model.links.map(l=>relationView(l,uses)).join(''):'<p class="muted">没有经过明确提案的联系；共同出现在一个问题中不代表存在业务关系。</p>'}<div class="model-boundaries">${modelList('尚未证实的假设',model.assumptions)}${modelList('应用时还需要的输入',model.required_inputs)}${modelList('不能直接推出的结论 / 判断边界',model.boundaries)}</div>`;
}
async function loadLocalModel(run) {
  const version=++modelRequest;
  if(run.kind!=='analysis'||!run.result)return;
  $('#local-model-panel').classList.remove('hidden');
  $('#local-model-state').textContent='正在核对当前依据…';
  $('#local-model').textContent='正在读取本次局部模型和当前知识状态。';
  try {
    const model=await api(`/api/runs/${encodeURIComponent(run.id)}/model`);
    if(run.id===currentRun&&version===modelRequest)renderLocalModel(model);
  } catch(e) {
    if(run.id===currentRun&&version===modelRequest){
      $('#local-model-panel').classList.remove('hidden');
      $('#local-model-state').textContent='';
      $('#local-model').textContent=e.message;
    }
  }
}
async function loadLinks() {
  const query=$('#knowledge-search').value;
  try {
    const links=await api('/api/knowledge-links?q='+encodeURIComponent(query));
    if(query!==$('#knowledge-search').value)return;
    $('#knowledge-links').innerHTML='<p class="muted">联系经过至少两个不同问题的组合与联系复核，且依据仍有效，才进入共享复用。重复运行同一个问题不增加验证次数。</p>'+(links.length?links.map(link=>`<article class="knowledge-card shared-link"><span class="pill ${link.status==='shared'?'reviewed':'candidate'}">${link.status==='shared'?'可共享复用':link.status==='conflict'?'存在冲突，暂停共享':'尚未进入共享复用'}</span><span class="scope"> ${link.support_count} 个不同问题的有效验证</span><div class="relation-path">${knowledgeNode(link.source_id,link.source?.title||link.source_id,!!link.source)}<span class="relation-arrow">${esc(link.relation)} →</span>${knowledgeNode(link.target_id,link.target?.title||link.target_id,!!link.target)}</div><p>${esc(link.explanation)} ${evidenceButtons(link.evidence)}</p><p class="scope">适用范围：${esc(link.scope)}</p>${modelList('成立条件',link.conditions)}<details><summary>查看来自哪些问题的验证</summary>${link.validations.map(v=>`<div class="validation"><button class="source-link" data-run="${esc(v.run_id)}">${esc(v.question)}</button><p>${v.eligible?'本次验证有效':'本次验证不足或依据已失效'} · ${esc(v.review?.reason||'未完成复核')}</p>${v.review?.check_errors?.length?`<p class="notice">${esc(v.review.check_errors.join('；'))}</p>`:''}</div>`).join('')}</details></article>`).join(''):'<div class="empty">尚无知识联系。新的分析会记录知识在判断中的作用，并对必要的联系单独复核。</div>');
  }catch(e){$('#knowledge-links').textContent=e.message;}
}
async function selectMemoryView(view) {
  memoryView=view;
  $('#knowledge-list').classList.toggle('hidden',view!=='items');
  $('#knowledge-links').classList.toggle('hidden',view!=='links');
  $('#view-items').classList.toggle('selected',view==='items');
  $('#view-links').classList.toggle('selected',view==='links');
  if(view==='links')await loadLinks();else await loadKnowledge();
}
function usagesView(items) {
  if(!items?.length)return '';
  return `<details class="knowledge-usages"><summary>在 ${items.length} 个问题中的用途</summary>${items.map(u=>`<div class="validation"><button class="source-link" data-run="${esc(u.run_id)}">${esc(u.question)}</button><p>${esc(u.role)}</p></div>`).join('')}</details>`;
}
