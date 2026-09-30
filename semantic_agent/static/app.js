const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let currentRun=null, pollTimer=null, eventSource=null, elapsedTimer=null;
let liveRun=null, eventCursor=0, traceIds=new Set(), viewVersion=0;
const labels = {running:'进行中',completed:'已完成',failed:'未完成',cancelled:'已停止',interrupted:'已中断',reviewed:'模型复核通过',candidate:'候选知识',withdrawn:'已撤回',indexed:'已提取',partial:'部分提取',needs_ocr:'待 OCR',error:'未提取'};

async function api(path, options={}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入后重试');
  return data;
}
function notice(message='') { $('#notice').textContent=message; $('#notice').classList.toggle('hidden', !message); }
function tab(name) {
  document.querySelectorAll('.tab').forEach(el=>el.classList.toggle('active',el.id===name));
  document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.tab===name));
  if(name==='knowledge') selectMemoryView(memoryView);
  if(name==='sources') refreshStatus();
}
function inline(text) {
  return esc(text)
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')
    .replace(/\[(p_[a-f0-9]+)\]/g,(_,id)=>`<button class="citation" data-passage="${id}" title="查看原文">据</button>`);
}
function markdown(text) {
  const lines=String(text).split('\n'); let out='', i=0;
  while(i<lines.length) {
    const line=lines[i];
    if(line.startsWith('```')) {let code=[];i++;while(i<lines.length&&!lines[i].startsWith('```'))code.push(lines[i++]);out+=`<pre>${esc(code.join('\n'))}</pre>`;i++;continue;}
    if(/^\s*\|/.test(line)&&i+1<lines.length&&/^\s*\|?[\s:|\-]+\|\s*$/.test(lines[i+1])) {
      const cells=s=>s.trim().replace(/^\||\|$/g,'').split('|');
      out+='<div style="overflow-x:auto"><table><thead><tr>'+cells(line).map(c=>`<th>${inline(c.trim())}</th>`).join('')+'</tr></thead><tbody>';i+=2;
      while(i<lines.length&&/^\s*\|/.test(lines[i]))out+='<tr>'+cells(lines[i++]).map(c=>`<td>${inline(c.trim())}</td>`).join('')+'</tr>';
      out+='</tbody></table></div>';continue;
    }
    if(/^#{1,6}\s/.test(line)){out+=`<h3>${inline(line.replace(/^#{1,6}\s+/,''))}</h3>`;i++;continue;}
    if(/^\s*[-*]\s+/.test(line)){out+='<ul>';while(i<lines.length&&/^\s*[-*]\s+/.test(lines[i]))out+=`<li>${inline(lines[i++].replace(/^\s*[-*]\s+/,''))}</li>`;out+='</ul>';continue;}
    if(/^\s*\d+[.)]\s+/.test(line)){out+='<ol>';while(i<lines.length&&/^\s*\d+[.)]\s+/.test(lines[i]))out+=`<li>${inline(lines[i++].replace(/^\s*\d+[.)]\s+/,''))}</li>`;out+='</ol>';continue;}
    if(line.trim())out+=`<p>${inline(line)}</p>`;
    i++;
  }
  return out;
}
function pill(status) {return `<span class="pill ${esc(status)}">${esc(labels[status]||status)}</span>`;}
function fragmentView(fragment) {
  if(!fragment)return '';
  const terms=fragment.terms||[], relations=fragment.relations||[];
  if(!terms.length&&!relations.length)return '';
  return '<details><summary>查看语义结构</summary>'+terms.map(t=>`<p><strong>${esc(t.name)}</strong>：${esc(t.definition)}</p>`).join('')+relations.map(r=>`<p class="semantic-relation"><b>${esc(r.subject)}</b><span>${esc(r.predicate)}</span><b>${esc(r.object)}</b></p>`).join('')+'</details>';
}
async function refreshStatus() {
  try {
    const s=await api('/api/status');
    $('#model-name').textContent=s.model;
    $('#knowledge-count').textContent=s.stats.knowledge;
    const count=s.stats.documents.reduce((n,row)=>n+row.count,0);
    $('#corpus-summary').textContent=count?`${count} 份资料 · ${s.stats.passages.toLocaleString()} 个片段可检索`:'尚未建立索引，请前往“资料与覆盖”';
    $('#source-stats').textContent=`${count} 份资料，${s.stats.passages.toLocaleString()} 个文本片段；${s.stats.knowledge} 条知识。`;
    $('#index-button').disabled=s.indexing;
    $('#source-issues').innerHTML=s.issues.length?s.issues.map(item=>`<div class="issue">${pill(item.status)}${esc(item.path)}<p>${esc(item.detail)}</p></div>`).join(''):'<div class="empty">暂无提取问题记录。图像内容仍需要单独核读。</div>';
    if(!s.api_configured)notice('尚未配置模型接口，请设置 .env 后重启服务。');
  } catch(e) {notice(e.message);}
}
async function refreshHistory() {
  const runs=await api('/api/runs');
  const questions=runs.filter(r=>r.kind==='analysis');
  $('#history').innerHTML=questions.length?questions.map(r=>`<button data-run="${esc(r.id)}" title="${esc(r.question)}">${r.status==='running'?'◌ ':''}${esc(r.question)}</button>`).join(''):'<p>从第一个问题开始。</p>';
  return runs;
}
function stopStream() {
  eventSource?.close(); eventSource=null;
  clearTimeout(pollTimer);clearInterval(elapsedTimer);
}
function showPhase(message) {
  $('#live-phase').textContent=message;
}
function updateElapsed() {
  if(!liveRun||liveRun.status!=='running')return;
  const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(liveRun.created_at))/1000));
  $('#elapsed').textContent=seconds<60?`已用时 ${seconds} 秒`:`已用时 ${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
}
function runHeader(run) {
  $('#run-title').textContent=run.kind==='index'?'更新资料索引':'本次分析';
  $('#run-status').className='pill '+run.status;$('#run-status').textContent=labels[run.status]||run.status;
  $('#cancel-button').classList.toggle('hidden',run.status!=='running'||run.kind!=='analysis');
  $('#cancel-button').disabled=false;
  $('#ask-button').disabled=run.status==='running';
  $('#live-status').classList.toggle('hidden',run.status!=='running');
  $('#answer').setAttribute('aria-busy',String(run.status==='running'));
  updateElapsed();
}
function appendTrace(item) {
  if(traceIds.has(item.seq))return;
  traceIds.add(item.seq);
  const list=$('#trace'), follow=list.scrollHeight-list.scrollTop-list.clientHeight<48;
  list.insertAdjacentHTML('beforeend',`<div class="trace-item"><time>${esc(new Date(item.time).toLocaleTimeString('zh-CN',{hour12:false}))}</time><div>${esc(item.message)}</div>${item.data?`<details><summary>查看检索与证据记录</summary><pre>${esc(JSON.stringify(item.data,null,2))}</pre></details>`:''}</div>`);
  $('#step-count').textContent=`${traceIds.size} 条记录`;
  if(follow)list.scrollTop=list.scrollHeight;
  if(liveRun?.status==='running') {
    showPhase(item.message);
    if(item.kind==='model'||item.kind==='review') {
      $('#live-progress').textContent='';$('#live-progress').classList.add('hidden');
    }
    if(item.kind==='review'&&liveRun.stream?.answer)$('#answer-state').textContent='草稿已生成 · 正在核对证据';
  }
}
function showStream(kind, data) {
  if(!liveRun||liveRun.status!=='running')return;
  liveRun.stream[kind]=data;
  if(kind==='progress') {
    $('#live-progress').textContent=data.text;
    $('#live-progress').classList.remove('hidden');
  } else if(kind==='answer') {
    $('#live-progress').classList.add('hidden');
    $('#answer').innerHTML=markdown(data.text);
    $('#answer').classList.add('streaming');
    $('#answer-state').textContent=data.stage==='review'?'复核修订中 · 尚未完成':'草稿生成中 · 待复核';
    showPhase(data.stage==='review'?'正在输出按证据复核后的回答':'正在生成回答草稿');
  }
}
function renderResult(run) {
  loadLocalModel(run);
  $('#answer').classList.remove('streaming');
  $('#live-progress').classList.add('hidden');
  if(run.result&&run.kind==='analysis') {
    const r=run.result;
    $('#answer').innerHTML=markdown(r.answer);
    $('#answer-state').textContent=r.answer_review?.status==='reviewed'?'回答已复核':'回答待核读';
    $('#usage').textContent=r.usage?.total_tokens?`${r.usage.total_tokens.toLocaleString()} tokens`:'';
    if(r.unresolved_questions.length)$('#unknowns').innerHTML='<strong>仍需补充的信息</strong><ul>'+r.unresolved_questions.map(q=>`<li>${esc(q)}</li>`).join('')+'</ul>';
    $('#answer-sources').innerHTML='<span class="small-label">引用来源</span>'+r.sources.map(s=>`<button class="source-link" data-passage="${esc(s.id)}">${esc(s.path.split('/').pop())}<small>${esc(s.locator)}</small></button>`).join('');
    $('#reuse-count').textContent=`当时复用 ${r.reused_knowledge_ids.length} 条已有知识`;
    const historyNote=run.knowledge_cleared?'<div class="empty">知识库已清空。以下仅为当时的积累记录，不参与后续问题的自动复用。</div>':'';
    $('#changes').innerHTML=historyNote+(r.knowledge_changes.length?r.knowledge_changes.map(k=>`<div class="knowledge-card">${pill(k.status)}<h3>${esc(k.title)}</h3><p>${esc(k.review?.reason||'任务模型条目已完成独立复核')}</p>${k.review?.quote_check_errors?.length?`<p>${esc(k.review.quote_check_errors.join('；'))}</p>`:''}${run.knowledge_cleared?'':`<button class="subtle" data-open-knowledge="${esc(k.id)}">查看完整知识与依据</button>`}</div>`).join(''):'<div class="empty">本轮没有新增知识。已存在或证据不足的内容无需重复沉淀。</div>');
    if(r.review_warning)notice(r.review_warning);
  } else if(run.result&&run.kind==='index') {
    $('#answer-state').textContent='';
    $('#answer').innerHTML=`<h3>资料索引已更新</h3><p>处理了 ${run.result.files} 个文件，共 ${run.result.passages.toLocaleString()} 个可检索片段。</p><p>现在可以在上方输入业务问题。</p>`;
    $('#changes').textContent='索引更新完成，尚未进行知识积累。';
  } else {
    const partial=liveRun?.stream?.answer;
    $('#answer-state').textContent=partial?'未完成 · 以下为未复核的部分内容':'';
    $('#answer').innerHTML=`<p class="notice">${esc(run.error||'本次任务没有完成。')}</p>`+(partial?markdown(partial.text):'');
    $('#changes').textContent='本次分析未完成，没有形成完整的知识积累结果。';
  }
}
async function loadRun(id) {
  stopStream();currentRun=id;const version=++viewVersion;
  traceIds=new Set();liveRun=null;notice();modelRequest++;
  $('#local-model-panel').classList.add('hidden');$('#local-model').textContent='';$('#local-model-state').textContent='';
  $('#run-area').classList.remove('hidden');tab('workspace');
  for(const selector of ['#usage','#unknowns','#changes','#answer-sources','#reuse-count','#trace','#step-count','#answer-state','#live-progress'])$(selector).textContent='';
  $('#live-progress').classList.add('hidden');$('#live-status').classList.remove('hidden');
  $('#answer').classList.remove('streaming');
  $('#answer').innerHTML='<div class="waiting"><span class="pulse"></span>正在读取本次分析…</div>';
  showPhase('正在连接…');$('#elapsed').textContent='';
  try {
    const run=await api('/api/runs/'+id);
    if(version!==viewVersion)return;
    liveRun=run;liveRun.stream=run.stream||{};eventCursor=run.event_cursor||0;
    runHeader(run);run.trace.forEach(appendTrace);
    if(run.status==='running') {
      loadLocalModel(run);
      $('#answer').innerHTML='<div class="waiting"><span class="pulse"></span>正在查找依据，进展会实时显示。</div>';
      $('#changes').innerHTML='<p class="muted">知识复核和保存情况会显示在工作过程中。</p>';
      for(const kind of ['progress','answer'])if(run.stream[kind])showStream(kind,run.stream[kind]);
      elapsedTimer=setInterval(updateElapsed,1000);
      connectStream(id,version);
    } else renderResult(run);
  } catch(e) {
    if(version!==viewVersion)return;
    notice(e.message);showPhase('连接暂时中断，正在重试…');
    pollTimer=setTimeout(()=>loadRun(id),4000);
  }
}
function connectStream(id,version) {
  if(typeof EventSource==='undefined') {
    showPhase('当前浏览器通过定时刷新显示进展');
    pollTimer=setTimeout(()=>loadRun(id),1500);return;
  }
  const stream=new EventSource(`/api/runs/${encodeURIComponent(id)}/events?after=${eventCursor}`);
  eventSource=stream;
  const valid=()=>version===viewVersion&&eventSource===stream;
  for(const kind of ['trace','progress','answer','task_model'])stream.addEventListener(kind,event=>{
    if(!valid())return;
    const seq=Number(event.lastEventId);
    if(seq<=eventCursor)return;
    eventCursor=seq;
    const data=JSON.parse(event.data);
    if(kind==='trace')appendTrace(data);
    else if(kind==='task_model') {
      modelRequest++;
      showPhase(data.phase==='inference'?'正在基于任务模型推理':'正在更新任务级语义模型');
      $('#answer-state').textContent=data.phase==='inference'?'模型推理中 · 正在验证':'任务模型构建中 · 正在补齐证据';
      if(data.model?.schema_version===2) {
        $('#local-model-panel').classList.remove('hidden');
        $('#local-model-state').innerHTML=taskModelStatus(data.model);
        $('#local-model').innerHTML=taskModelView(data.model);
      }
    } else showStream(kind,data);
  });
  stream.addEventListener('heartbeat',()=>{if(valid())updateElapsed();});
  stream.addEventListener('done',async event=>{
    if(!valid())return;
    const run=JSON.parse(event.data);
    run.stream=liveRun.stream;liveRun=run;
    stopStream();runHeader(run);renderResult(run);
    try {await refreshStatus();await refreshHistory();}catch(e){notice(e.message);}
  });
  stream.onopen=()=>{
    if(!valid())return;
    clearTimeout(pollTimer);
    if($('#live-phase').textContent.includes('重连'))showPhase('已重新连接，继续接收分析进展');
  };
  stream.onerror=()=>{
    if(!valid())return;
    showPhase('连接暂时中断，正在重连；已收到的内容已保留');
    clearTimeout(pollTimer);
    // EventSource resumes using Last-Event-ID. A snapshot also recovers from a
    // server restart, unsupported streaming proxy, or a lost terminal event.
    pollTimer=setTimeout(()=>{if(valid())loadRun(id);},5000);
  };
}
async function loadKnowledge() {
  if(memoryView==='links')return loadLinks();
  try {
    const items=await api('/api/knowledge?q='+encodeURIComponent($('#knowledge-search').value));
    $('#knowledge-list').innerHTML=items.length?items.map(k=>`<article class="knowledge-card" id="${esc(k.id)}">${pill(k.status)} ${!k.sources_current?'<span class="pill stale">来源版本已过期</span>':''}<span class="scope"> ${esc(k.kind)}</span><h3>${esc(k.title)}</h3><p>${esc(k.statement)}</p><p class="scope">适用范围：${esc(k.scope)}</p>${k.conditions.length?'<ul>'+k.conditions.map(c=>`<li>${esc(c)}</li>`).join('')+'</ul>':''}${fragmentView(k.model_fragment)}${usagesView(k.used_in)}<details><summary>依据与复核说明</summary><p>${esc(k.review?.reason||'任务模型条目已完成独立复核')}</p>${k.evidence.map(e=>`<button class="source-link" data-passage="${esc(e.passage_id)}">“${esc(e.quote)}”</button>`).join('')}</details><div class="actions"><button data-run="${esc(k.run_id)}">回到产生它的问题</button><button data-knowledge-action="${k.status==='withdrawn'?'restore':'withdraw'}" data-id="${esc(k.id)}">${k.status==='withdrawn'?'恢复为候选':'撤回'}</button></div></article>`).join(''):'<div class="empty">这里会保留问题解决过程中积累的知识。先提出一个业务问题。</div>';
  }catch(e){notice(e.message);}
}
async function evidence(pid) {
  try {
    const item=await api('/api/passages/'+encodeURIComponent(pid));
    $('#evidence-content').innerHTML=`<p class="source-path">${esc(item.path)}<br>${esc(item.locator)}</p>${!item.active?'<p class="notice">这是历史索引版本，已不用于自动复用。</p>':''}<a href="/api/documents/${encodeURIComponent(item.document_id)}/file" target="_blank" rel="noopener">打开原文件 ↗</a><pre>${esc(item.text)}</pre>`;
    $('#evidence-dialog').showModal();
  }catch(e){notice(e.message);}
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  try {
    if(button.dataset.tab)tab(button.dataset.tab);
    if(button.dataset.memoryView)await selectMemoryView(button.dataset.memoryView);
    if(button.dataset.example){$('#question').value=button.dataset.example;$('#question').focus();}
    if(button.dataset.passage)await evidence(button.dataset.passage);
    if(button.dataset.run)await loadRun(button.dataset.run);
    if(button.dataset.openKnowledge){memoryView='items';tab('knowledge');await loadKnowledge();document.getElementById(button.dataset.openKnowledge)?.scrollIntoView({behavior:'smooth',block:'center'});}
    if(button.dataset.knowledgeAction){await api(`/api/knowledge/${button.dataset.id}/${button.dataset.knowledgeAction}`,{method:'POST'});await loadKnowledge();await refreshStatus();}
  }catch(e){notice(e.message);}
});
$('#question-form').addEventListener('submit',async event=>{
  event.preventDefault();notice();const question=$('#question').value.trim();
  if(question.length<5){notice('请补充一个具体的业务问题。');return;}
  $('#ask-button').disabled=true;
  try {const r=await api('/api/runs',{method:'POST',body:JSON.stringify({question})});await refreshHistory();await loadRun(r.id);}catch(e){notice(e.message);$('#ask-button').disabled=false;}
});
$('#index-button').addEventListener('click',async()=>{try{notice();const r=await api('/api/index',{method:'POST',body:'{}'});await loadRun(r.id);}catch(e){tab('workspace');notice(e.message);}});
$('#cancel-button').addEventListener('click',async()=>{try{await api(`/api/runs/${currentRun}/cancel`,{method:'POST'});$('#cancel-button').disabled=true;showPhase('正在停止分析…');}catch(e){notice(e.message);}});
$('#refresh-knowledge').addEventListener('click',loadKnowledge);
$('#knowledge-search').addEventListener('input',()=>{clearTimeout(window.searchTimer);window.searchTimer=setTimeout(loadKnowledge,250);});
$('#close-dialog').addEventListener('click',()=>$('#evidence-dialog').close());
(async()=>{await refreshStatus();const runs=await refreshHistory();const active=runs.find(r=>r.status==='running');if(active)await loadRun(active.id);})();
