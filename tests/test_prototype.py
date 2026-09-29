import asyncio
import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from semantic_agent.agent import ModelingAgent, validate_evidence
from semantic_agent.app import create_app
from semantic_agent.config import Settings
from semantic_agent.ingest import index_corpus
from semantic_agent.store import Store


@pytest.fixture
def workspace(tmp_path):
    docs=tmp_path / "rawdoc"
    docs.mkdir()
    (docs / "姑苏站规程.md").write_text("姑苏站控制系统为双重化冗余系统。\n发生系统切换时，只能切换至正处于备用状态的系统。\n发现监测数据不刷新或异常跳变时，应排除监测装置故障。",encoding="utf-8")
    settings=Settings(tmp_path,docs,tmp_path/"data","https://example.invalid/v1","secret-test-key","test-model","high",10,False,"")
    store=Store(settings.data_dir)
    index_corpus(settings,store)
    return settings,store


def test_chinese_retrieval_and_evidence_locations(workspace):
    settings,store=workspace
    result=store.search("系统切换")
    assert result and result[0]["path"]=="姑苏站规程.md"
    passage=store.passage(result[0]["id"])
    assert "提取行" in passage["locator"]
    assert "备用状态" in passage["text"]


def test_only_allowed_corpus_files_indexed(workspace):
    settings,store=workspace
    (settings.doc_dir / ".env").write_text("SECRET=do-not-index")
    (settings.doc_dir / "~$lock.docx").write_text("temporary")
    external=settings.root / "private.md"
    external.write_text("outside-secret")
    (settings.doc_dir / "escaped.md").symlink_to(external)
    result=index_corpus(settings,store)
    assert result["files"]==1
    assert not store.search("outside-secret")


def candidate(pid):
    return {"kind":"状态规则","title":"系统切换目标条件","statement":"控制系统切换的目标系统必须处于备用状态。", "scope":"姑苏站控制系统，测试规程", "conditions":["发生系统切换时"], "evidence":[{"passage_id":pid,"quote":"发生系统切换时，只能切换至正处于备用状态的系统。"}]}


def test_unread_and_fabricated_quotes_rejected(workspace):
    settings,store=workspace
    p=store.passage(store.search("系统切换")[0]["id"])
    c=candidate(p["id"])
    assert validate_evidence(c,{})
    assert not validate_evidence(c,{p["id"]:p})
    c["evidence"][0]["quote"]="当前全站运行正常，无需任何核查。"
    assert validate_evidence(c,{p["id"]:p})


def test_versions_invalidate_memory_and_keep_old_evidence(workspace):
    settings,store=workspace
    pid=store.search("系统切换")[0]["id"]
    kid,_=store.save_knowledge(candidate(pid),"test-run","reviewed",{"reason":"支持"})
    assert store.knowledge("系统切换",reusable_only=True)
    (settings.doc_dir / "姑苏站规程.md").write_text("此为修改后的新版本。")
    assert "error" in ModelingAgent(settings,store).read_passage(pid)
    index_corpus(settings,store)
    assert not store.knowledge("系统切换",reusable_only=True)
    assert store.passage(pid) and not store.passage(pid)["active"]
    assert store.knowledge()[0]["id"]==kid


def test_duplicate_memory_and_withdrawal(workspace):
    settings,store=workspace
    c=candidate(store.search("系统切换")[0]["id"])
    kid,created=store.save_knowledge(c,"run1","reviewed",{})
    other,again=store.save_knowledge(c,"run2","reviewed",{})
    assert created and not again and kid==other
    store.set_knowledge_status(kid,"withdrawn")
    assert not store.knowledge(reusable_only=True)


class FakeModel:
    def __init__(self,pid,bad_quote=False,failed_review=False):
        self.pid,self.step,self.bad_quote,self.failed_review=pid,0,bad_quote,failed_review

    async def chat(self,messages,tools=None,review=False,on_delta=None):
        if review:
            if self.failed_review:
                from semantic_agent.llm import ModelError
                raise ModelError("test review failure")
            return {"content":json.dumps({"reviews":[{"index":0,"verdict":"supported","reason":"原文明确说明切换目标条件"}], "model_review":{"verdict":"supported","reason":"局部判断保留必要条件边界"},"link_reviews":[], "answer_review":{"issues":["补充判断范围"],"answer":"规程要求系统切换的目标系统处于备用状态，但这项规则不能独立说明当前设备是否正常。["+self.pid+"]","unresolved_questions":["缺少当时的主备状态"]}})},{}
        self.step+=1
        if self.step==1:
            name,args="read_passages",{"ids":[self.pid]}
        else:
            c=candidate(self.pid)
            if self.bad_quote:c["evidence"][0]["quote"]="设备一旦发生任何告警，必须立即停运整个直流系统。"
            name,args="submit_result",{"answer":"根据规程，发生系统切换时，目标系统必须处于备用状态。这只是判断切换条件的一部分，还需要当时的运行状态。["+self.pid+"]", "knowledge_updates":[c],"unresolved_questions":["缺少当时的主备状态"],"reused_knowledge_ids":[]}
            args["local_model"]={"objective":"判断系统切换目标的状态要求","scope":"姑苏站控制系统", "knowledge_uses":[{"ref":"new:0","role":"提供切换目标的必要状态约束"}],"links":[],"assumptions":[],"required_inputs":["切换前目标系统状态"],"boundaries":["不能仅由该条件判断全站正常"]}
        return {"role":"assistant","content":None,"tool_calls":[{"id":f"call_{self.step}","type":"function","function":{"name":name,"arguments":json.dumps(args,ensure_ascii=False)}}]}, {"total_tokens":100}


@pytest.mark.parametrize("bad_quote,failed_review,status",[(False,False,"reviewed"),(True,False,"candidate"),(False,True,"candidate")])
def test_agent_review_gate_and_persistence(workspace,bad_quote,failed_review,status):
    settings,store=workspace
    pid=store.search("系统切换")[0]["id"]
    rid=store.create_run("analysis","控制系统切换需要满足什么条件？")
    model=FakeModel(pid,bad_quote,failed_review)
    result=asyncio.run(ModelingAgent(settings,store,model).run(rid,"控制系统切换需要满足什么条件？",3))
    assert result and result["knowledge_changes"][0]["status"]==status
    assert store.get_run(rid)["status"]=="completed"
    assert bool(store.knowledge(reusable_only=True))==(status=="reviewed")
    assert result["unresolved_questions"]
    assert result["answer_review"]["status"]==("unreviewed" if failed_review else "reviewed")
    if not failed_review:assert "不能独立说明" in result["answer"]


def test_local_api_does_not_expose_credentials(workspace):
    settings,store=workspace
    with TestClient(create_app(settings)) as client:
        response=client.get("/api/status")
        assert response.status_code==200
        assert settings.api_key not in response.text and settings.api_url not in response.text
        assert client.get("/").status_code==200
        assert client.post("/api/index",headers={"Origin":"https://untrusted.example"}).status_code==403
        assert client.get("/api/documents/nonexistent/file").status_code==404


def test_full_endpoint_and_base_url(workspace):
    settings,_=workspace
    assert settings.endpoint=="https://example.invalid/v1/chat/completions"
    assert replace(settings,api_url="https://example.invalid").endpoint==settings.endpoint
    assert replace(settings,api_url=settings.endpoint).endpoint==settings.endpoint


def test_log_search_is_explicit_and_versioned_download_is_safe(workspace):
    settings,store=workspace
    (settings.doc_dir / "event.txt").write_text("2024-01-01 控制系统切换 日志实例")
    index_corpus(settings,store)
    assert all(not r["path"].endswith(".txt") for r in store.search("控制系统切换"))
    assert store.search("控制系统切换",source_type="logs")[0]["path"]=="event.txt"
    pid=store.search("系统切换")[0]["id"]
    did=store.passage(pid)["document_id"]
    (settings.doc_dir / "姑苏站规程.md").write_text("已修改为新版本")
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/documents/{did}/file").status_code==409


def test_semantic_fragment_and_candidate_promotion(workspace):
    settings,store=workspace
    c=candidate(store.search("系统切换")[0]["id"])
    c["model_fragment"]={"terms":[{"name":"切换目标","definition":"准备接替运行的控制系统"}],"relations":[{"subject":"切换目标","predicate":"要求状态","object":"备用"}]}
    kid,_=store.save_knowledge(c,"run1","candidate",{})
    store.save_knowledge(c,"run2","reviewed",{"reason":"已重新核对"})
    saved=store.knowledge()[0]
    assert saved["status"]=="reviewed" and saved["model_fragment"]["relations"]
    store.set_knowledge_status(kid,"withdrawn")
    store.save_knowledge(c,"run3","reviewed",{})
    assert store.knowledge_status(kid)=="withdrawn"


@pytest.mark.parametrize('path', [('answer',), ('answer_review', 'answer')])
@pytest.mark.parametrize('ascii_only', [True, False])
def test_partial_json_strings_survive_every_chunk_boundary(path, ascii_only):
    from semantic_agent.streaming import json_string_prefix
    answer='姑苏站："备用"\n反斜线 \\ 与 emoji 😀。[p_abc]'
    value={'answer':answer}
    if len(path)>1:
        value={'reviews':[{'reason':'含有假的 "answer": "不要显示"'}], 'answer_review':value}
    else:
        value={'unresolved_questions':['answer_review', {'answer':'不是答案'}], **value}
    encoded=json.dumps(value,ensure_ascii=ascii_only)
    last=''
    for end in range(len(encoded)+1):
        prefix=json_string_prefix(encoded[:end],path)
        if prefix is not None:
            assert answer.startswith(prefix)
            assert prefix.startswith(last)
            assert not any(0xD800<=ord(c)<=0xDFFF for c in prefix)
            last=prefix
    assert last==answer


def mock_provider(monkeypatch, chunks):
    import httpx
    import semantic_agent.llm as llm
    original=httpx.AsyncClient
    requests=[]

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in chunks:
                if isinstance(chunk, Exception):
                    raise chunk
                raw=('data: '+json.dumps(chunk,ensure_ascii=False)+'\r\n\r\n').encode()
                # Exercise HTTP boundaries inside both UTF-8 and SSE frames.
                for i in range(0,len(raw),7):
                    yield raw[i:i+7]
                    await asyncio.sleep(0)

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=Body())

    monkeypatch.setattr(llm.httpx,'AsyncClient',lambda **kwargs: original(transport=httpx.MockTransport(handle),**kwargs))
    return requests


def test_provider_stream_aggregates_tools_usage_and_keeps_reasoning_private(workspace,monkeypatch):
    from semantic_agent.llm import GLMClient
    settings,_=workspace
    def delta(value, finish=None):
        return {'choices':[{'delta':value,'finish_reason':finish}]}
    requests=mock_provider(monkeypatch,[
        delta({'reasoning_content':'PRIVATE_REASONING'}),
        delta({'content':'正在查询'}),delta({'content':'资料。'}),
        delta({'tool_calls':[{'index':0,'id':'call_1','function':{'name':'submit_', 'arguments':'{"ans'}}]}),
        delta({'tool_calls':[{'index':0,'function':{'name':'result', 'arguments':'wer":"中文"}'}}]}),
        delta({},'tool_calls'),{'choices':[], 'usage':{'total_tokens':123}}
    ])
    seen=[]
    async def on_delta(value):
        seen.append(json.loads(json.dumps(value)))
    message,usage=asyncio.run(GLMClient(settings).chat([],on_delta=on_delta))
    assert requests[0]['stream'] is True
    assert message['content']=='正在查询资料。'
    assert message['tool_calls'][0]['function']=={'name':'submit_result','arguments':'{"answer":"中文"}'}
    assert message['reasoning_content']=='PRIVATE_REASONING'
    assert 'PRIVATE_REASONING' not in json.dumps(seen)
    assert any(item['content']=='正在查询' for item in seen)
    assert usage['total_tokens']==123


@pytest.mark.parametrize('ending', ['eof', 'network', 'length'])
def test_incomplete_model_output_fails_without_restarting_generation(workspace,monkeypatch,ending):
    import httpx
    from semantic_agent.llm import GLMClient, ModelError
    settings,_=workspace
    chunks=[{'choices':[{'delta':{'content':'部分回答'}}]}]
    if ending=='network':chunks.append(httpx.ReadError('private upstream detail'))
    if ending=='length':chunks.append({'choices':[{'delta':{},'finish_reason':'length'}]})
    requests=mock_provider(monkeypatch,chunks)
    with pytest.raises(ModelError) as error:
        asyncio.run(GLMClient(settings).chat([]))
    assert len(requests)==1
    assert 'private upstream detail' not in str(error.value)


def test_reporter_streams_only_answer_field_and_refresh_snapshot(workspace):
    from semantic_agent.streaming import StreamReporter
    settings,store=workspace
    rid=store.create_run('analysis','测试流式草稿与复核')
    reporter=StreamReporter(store,rid,1)
    def draft(text):
        return {'reasoning_content':'PRIVATE', 'tool_calls':[{'function':{'name':'submit_result','arguments':text}}]}
    reporter.emit(draft('{"answer":"正在输出'),force=True)
    first=store.get_run(rid)
    assert first['stream']['answer']['text']=='正在输出'
    cursor=first['event_cursor']
    reporter.emit(draft('{"answer":"正在输出回答。","knowledge_updates":[]}'),force=True)
    review=StreamReporter(store,rid,'review',review=True)
    review.emit({'content':'{"reviews":[],"answer_review":{"answer":"修订后'},force=True)
    later=store.events(rid,cursor)
    assert [e['data']['text'] for e in later if e['kind']=='answer']==['正在输出回答。','修订后']
    assert store.get_run(rid)['stream']['answer']['stage']=='review'
    assert 'PRIVATE' not in json.dumps(store.get_run(rid))


def test_sse_replays_and_resumes_with_terminal_status(workspace):
    settings,store=workspace
    rid=store.create_run('analysis','测试断线恢复')
    store.trace(rid,'start','开始')
    store.event(rid,'answer',{'text':'第一段','stage':'draft'})
    first=store.events(rid)[-1]['seq']
    store.event(rid,'answer',{'text':'第一段和第二段','stage':'review'})
    store.finish_run(rid,'cancelled',error='已停止')
    with TestClient(create_app(settings)) as client:
        response=client.get(f'/api/runs/{rid}/events')
        assert response.headers['content-type'].startswith('text/event-stream')
        assert 'event: trace' in response.text and 'event: done' in response.text
        assert 'cancelled' in response.text
        resumed=client.get(f'/api/runs/{rid}/events?after=0',headers={'Last-Event-ID':str(first)})
        assert 'event: trace' not in resumed.text
        assert resumed.text.count('event: answer')==1
        assert '第一段和第二段' in resumed.text
        assert client.get(f'/api/runs/{rid}/events',headers={'Last-Event-ID':'oops'}).status_code==400
        assert client.get('/api/runs/missing/events').status_code==404


def test_clear_knowledge_preserves_corpus_and_labels_history(workspace):
    settings,store=workspace
    pid=store.search('系统切换')[0]['id']
    rid=store.create_run('analysis','历史分析')
    store.save_knowledge(candidate(pid),rid,'reviewed',{})
    before=store.stats()
    with pytest.raises(ValueError):store.clear_knowledge()
    store.finish_run(rid,'completed',{'answer':'历史回答'})
    with TestClient(create_app(settings)) as client:
        active=store.create_run('analysis','尚未完成')
        assert client.post('/api/knowledge/clear').status_code==409
        store.finish_run(active,'cancelled')
        assert client.post('/api/knowledge/clear').json()=={'cleared':1}
        assert client.get('/api/knowledge').json()==[]
        assert client.get(f'/api/runs/{rid}').json()['knowledge_cleared'] is True
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM knowledge_structure').fetchone()[0]==0
    assert store.stats()['passages']==before['passages']
    assert store.passage(pid) and store.search('系统切换')
    assert not store.knowledge(reusable_only=True)


def test_cancellation_stops_stream_and_does_not_save_knowledge(workspace):
    settings,store=workspace
    started=asyncio.Event()
    class WaitingModel:
        async def chat(self,messages,tools=None,**kwargs):
            await kwargs['on_delta']({'content':'正在查找依据'})
            started.set()
            await asyncio.Event().wait()
    async def run():
        rid=store.create_run('analysis','停止中的分析')
        task=asyncio.create_task(ModelingAgent(settings,store,WaitingModel()).run(rid,'停止中的分析'))
        await started.wait()
        assert store.get_run(rid)['stream']['progress']['text']=='正在查找依据'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        return rid
    rid=asyncio.run(run())
    assert store.get_run(rid)['status']=='cancelled'
    assert store.stats()['knowledge']==0
    with TestClient(create_app(settings)) as client:
        assert 'event: done' in client.get(f'/api/runs/{rid}/events').text


def test_plain_draft_stream_precedes_buffered_tool_arguments(workspace):
    from semantic_agent.streaming import StreamReporter
    _,store=workspace
    rid=store.create_run('analysis','正文应先于工具参数显示')
    reporter=StreamReporter(store,rid,1)
    reporter.emit({'content':'【回答'},force=True)
    assert not store.get_run(rid)['stream']
    reporter.emit({'content':'【回答草稿】已经读到'},force=True)
    assert store.get_run(rid)['stream']['answer']['text']=='已经读到'
    reporter.emit({'content':'【回答草稿】已经读到原文依据。', 'tool_calls':[{'function':{
        'name':'submit_result','arguments':'{"answer":"已经'}}]},force=True)
    # Tool serialization must not make the visible answer jump back to its start.
    assert store.get_run(rid)['stream']['answer']['text']=='已经读到原文依据。'


def test_duplicate_server_cannot_interrupt_active_workspace(workspace):
    settings,store=workspace
    with TestClient(create_app(settings)):
        rid=store.create_run('analysis','当前服务中的运行任务')
        with pytest.raises(RuntimeError,match='已有服务'):
            with TestClient(create_app(settings)):
                pass
        assert store.get_run(rid)['status']=='running'
        store.finish_run(rid,'cancelled')


def test_malformed_review_cannot_partially_promote_knowledge(workspace):
    settings,store=workspace
    pid=store.search('系统切换')[0]['id']
    class MalformedReview(FakeModel):
        async def chat(self,messages,tools=None,review=False,on_delta=None):
            if review:
                return {'content':json.dumps({'reviews':[{'index':0,'verdict':'supported','reason':'支持'}],
                    'answer_review':{'answer':None}})},{}
            return await super().chat(messages,tools,review,on_delta)
    rid=store.create_run('analysis','检查复核输出不完整的情况')
    result=asyncio.run(ModelingAgent(settings,store,MalformedReview(pid)).run(rid,'检查复核输出不完整的情况',3))
    assert result['answer_review']['status']=='unreviewed'
    assert result['knowledge_changes'][0]['status']=='candidate'
    assert not store.knowledge(reusable_only=True)
