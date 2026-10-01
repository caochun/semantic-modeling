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
function taskModelStatus(model) {
  const v=model.validation||{}, ok=v.valid_for_inference;
  const phase={planning:'任务规格',modeled:'模型已构建',inference:'已完成模型推理',answered:'已生成模型回答'}[model.phase]||model.phase;
  return `<span class="pill ${ok?'reviewed':'candidate'}">${esc(phase)} · v${esc(model.version)}</span>`;
}
function modelOverview(model,nodes,graph,rules,gaps) {
  const results=model.inference?.results||[];
  const edges=graph.edges||[], paths=graph.paths||[];
  const supported=results.filter(r=>r.status==='supported').length;
  const unknown=results.filter(r=>r.status==='unknown'||r.status==='insufficient').length;
  const nodeKind={entity:'实体',event:'事件',concept:'概念'};
  const conceptNodes=(model.concept_layer?.nodes||nodes.filter(n=>n.kind==='concept'));
  const instanceNodes=(model.instance_layer?.nodes||nodes.filter(n=>n.kind!=='concept'));
  const labelById=new Map((nodes||[]).map(n=>[n.id,n.label]));
  const nodeLabel=id=>esc(labelById.get(id)||id);
  const nodeCards=(nodes||[]).slice(0,12).map(n=>`<span class="graph-node ${esc(n.kind||'concept')}"><i>${esc(nodeKind[n.kind]||'节点')}</i>${esc(n.label)}</span>`).join('');
  const edgeRows=edges.slice(0,9).map(e=>`<div class="graph-edge"><span>${nodeLabel(e.source_id)}</span><b>${esc(e.predicate||e.relation||'关联')}</b><span>${nodeLabel(e.target_id)}</span></div>`).join('');
  return `<div class="model-overview">
    <div class="model-overview-head"><div><span class="section-kicker">TASK MODEL / ${esc(model.phase||'MODELED')}</span><p class="model-objective">${esc(model.task?.objective||'')}</p><p class="scope">${esc(model.task?.scope||'')}</p></div><div class="model-confidence"><span>模型状态</span>${taskModelStatus(model)}</div></div>
    <div class="model-metrics"><div><strong>${conceptNodes.length}</strong><span>概念节点</span></div><div><strong>${instanceNodes.length}</strong><span>实例节点</span></div><div><strong>${edges.length}</strong><span>实例关系</span></div><div><strong>${rules.length}</strong><span>条件规则</span></div><div><strong>${unknown}</strong><span>待补数据</span></div></div>
    <div class="graph-preview"><div class="graph-preview-head"><div><span class="section-kicker">SEMANTIC GRAPH</span><strong>当前问题的最小语义空间</strong></div><span class="graph-count">${paths.length} 条可重放路径</span></div><div class="graph-canvas"><div class="graph-node-cloud">${nodeCards||'<span class="muted">尚未形成节点</span>'}</div><div class="graph-edge-list">${edgeRows||'<span class="muted">尚未形成带证据关系</span>'}</div></div></div>
    <div class="model-boundary-strip"><span><b>开放缺口</b>${gaps.filter(g=>g.status!=='resolved').length} 项</span><span><b>验证状态</b>${model.inference?.validation?.valid?'推理重放通过':'等待复核'}</span><span><b>证据原则</b>每条关系均可回到原文</span></div>
  </div>`;
}
function taskModelView(model) {
  const task=model.task||{}, nodes=model.nodes||[], rules=model.rules||[], gaps=model.gaps||[];
  const conceptNodes=model.concept_layer?.nodes||nodes.filter(n=>n.kind==='concept');
  const instanceLayer=model.instance_layer||{};
  const instanceNodes=instanceLayer.nodes||nodes.filter(n=>n.kind!=='concept');
  const graph=model.graph||{}, labels=new Map(nodes.map(n=>[n.id,n.label]));
  const label=id=>esc(labels.get(id)||id);
  const status=s=>`<span class="pill ${s==='supported'?'reviewed':'candidate'}">${esc(({supported:'支持',unknown:'未知',conflict:'冲突',contradicted:'反证成立',incomplete:'搜索未完成',insufficient:'证据不足'})[s]||s)}</span>`;
  const atom=a=>`${label(a.subject)} — ${a.negative?'否定：':''}${esc(a.predicate)} → ${label(a.object)} <small>[${esc(a.context)}]</small>`;
  const section=(title,content)=>`<h3 class="model-section-title">${title}</h3>${content||'<p class="muted">尚未形成。</p>'}`;
  const grid=content=>content?`<div class="knowledge-grid">${content}</div>`:'';
  const mappings=model.mapping_layer||graph.mapping_layer||{};
  const links=[...(mappings.node_to_concept||[]),...(mappings.relation_to_concept||[])].map(l=>`<div class="knowledge-card">${status(l.status)}<p>${l.role==='node'?'实例对象 → 对象概念':'实例关系 → 关系概念'}：${label(l.element_id)} → ${label(l.concept_id)}</p>${evidenceButtons(l.evidence)}<p class="muted">${esc(l.review?.reason||'')}</p></div>`).join('');
  const edges=(graph.edges||[]).map(e=>`<div class="knowledge-card">${status(e.status)}<p class="scope">${esc(e.kind)} · ${esc(e.id)}</p><h3>${atom({subject:e.source_id,object:e.target_id,...e})}</h3><p>${esc(e.statement)}</p>${evidenceButtons(e.evidence)}<p class="muted">${esc(e.review?.reason||'')}</p></div>`).join('');
  const ruleCards=(instanceLayer.rules||rules).map(r=>`<div class="knowledge-card">${status(r.status)}<h3>${esc(r.statement)}</h3><p>${r.premises.map(atom).join('<br>且 ')}${r.guards?.length?'<br>比较条件：'+esc(r.guards.map(g=>`${g.left} ${g.op} ${g.right}`).join('；')):''}<br>⇒ ${atom(r.conclusion)}</p>${evidenceButtons(r.evidence)}<p class="muted">${esc(r.review?.reason||'')}</p></div>`).join('');
  const pathCards=(paths,proof=false)=>grid((paths||[]).map(p=>`<div class="knowledge-card">${status(p.status)}<p class="scope">${esc(p.id)}</p>${proof?'<p class="muted">合取前提的证明依赖（各前提均需成立）</p>':''}<ol>${p.steps.map(s=>`<li>${atom({subject:s.source_id,object:s.target_id,...s})} ${evidenceButtons(s.evidence)}</li>`).join('')}</ol></div>`).join(''));
  const results=(model.inference?.results||[]).map(r=>`<div class="knowledge-card">${status(r.status)}<h3>${esc(r.question)}</h3>${r.missing?.length?`<pre>${esc(r.missing.map(m=>typeof m==='string'?m:JSON.stringify(m,null,2)).join('\n'))}</pre>`:''}<p class="scope">${esc([...(r.model_ids||[]),...(r.path_ids||[]),...(r.proof_ids||[])].join('、'))}</p></div>`).join('');
  const open=gaps.filter(g=>g.status!=='resolved'), resolved=gaps.filter(g=>g.status==='resolved');
  return `${modelOverview(model,nodes,graph,rules,gaps)}<p class="muted model-note">这是本次问题的模型快照。节点名称是组织线索；事实、规则和概念映射分别复核。</p>
    ${model.source_validation?.current===false?'<p class="notice">部分原文已变化；此处保留历史模型，后续使用需重新建模。</p>':''}
    ${section('任务目标',modelList('',(task.goals||[]).map(g=>g.question)))}
    ${section('概念层',grid(conceptNodes.map(n=>`<div class="knowledge-card"><span class="pill">概念</span><h3>${esc(n.label)}</h3><p>${esc(n.description||n.definition)}</p><p class="scope">${esc(n.scope)}</p></div>`).join('')))}
    ${section('实例层：对象、事件与事实关系',grid(instanceNodes.map(n=>`<div class="knowledge-card"><span class="pill">${esc({entity:'实体',event:'事件'}[n.kind]||'实例')}</span><h3>${esc(n.label)}</h3><p>${esc(n.description||n.definition)}</p><p class="scope">${esc(n.scope)}</p></div>`).join('')+edges))}
    ${section('层间映射',grid(links))}
    ${section('条件规则',grid(ruleCards))}
    ${section('图关系路径',pathCards(graph.paths))}
    ${section('规则与事实的证明依赖',pathCards(graph.proof_paths||model.inference?.paths,true))}
    ${section('基于模型的查询结果',grid(results))}
    ${section('开放缺口',modelList('',open.map(g=>`${g.kind} · ${g.description}${g.query?'（检索：'+g.query+'）':''}`)))}
    ${resolved.length?`<details><summary>已补齐 ${resolved.length} 项缺口</summary>${modelList('',resolved.map(g=>g.description))}</details>`:''}
    ${modelList('适用边界',task.boundaries)}
    ${model.inference?`<p class="muted">推理重放：${model.inference.validation?.valid?'通过':'未通过或未完成'}。${esc(model.inference.validation?.note||'')}</p>`:''}
    <a href="/api/runs/${encodeURIComponent(currentRun)}/task-model" target="_blank">查看模型 JSON</a> · <a href="/api/runs/${encodeURIComponent(currentRun)}/task-model/versions" target="_blank">查看模型版本</a>`;
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
  if(run.kind!=='analysis')return;
  $('#local-model-panel').classList.remove('hidden');
  $('#local-model-state').textContent='正在核对当前依据…';
  $('#local-model').textContent='正在读取本次局部模型和当前知识状态。';
  try {
    let model;
    try { model=await api(`/api/runs/${encodeURIComponent(run.id)}/task-model`); }
    catch(e) { model=await api(`/api/runs/${encodeURIComponent(run.id)}/model`); }
    if(run.id===currentRun&&version===modelRequest) {
      if(model.schema_version===2) {
        $('#local-model-panel').classList.remove('hidden');
        $('#local-model-state').innerHTML=taskModelStatus(model);
        $('#local-model').innerHTML=taskModelView(model);
      } else renderLocalModel(model);
    }
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
