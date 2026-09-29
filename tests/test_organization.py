import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient

from semantic_agent.agent import ModelingAgent
from semantic_agent.app import create_app
from semantic_agent.models import ProblemModel, validate_model_refs
from test_prototype import workspace, candidate, FakeModel


def knowledge_pair(store):
    pid=store.search('系统切换')[0]['id']
    first=candidate(pid)
    first.update(title='双重化冗余控制系统',statement='姑苏站控制系统为双重化冗余系统。')
    first['evidence']=[{'passage_id':pid,'quote':'姑苏站控制系统为双重化冗余系统。'}]
    second=candidate(pid)
    return pid,first,second


def proposal(pid):
    return {'objective':'判断冗余控制系统切换目标的状态要求','scope':'姑苏站控制系统',
            'knowledge_uses':[{'ref':'new:0','role':'限定被讨论系统的冗余结构'},
                              {'ref':'new:1','role':'提供切换目标的必要状态约束'}],
            'links':[{'source_ref':'new:0','target_ref':'new:1','relation':'切换受状态规则约束',
                      'explanation':'目标状态规则适用于此处讨论的冗余控制系统切换。','scope':'姑苏站控制系统',
                      'conditions':['发生控制系统切换时'],
                      'evidence':[{'passage_id':pid,'quote':'发生系统切换时，只能切换至正处于备用状态的系统。'}]}],
            'assumptions':[],'required_inputs':['切换前的目标状态'],
            'boundaries':['满足目标状态条件不能证明全站正常，也不能说明切换原因。']}


def saved_model(store,question,*,model_verdict='supported',link_verdict='supported',scope=None):
    pid,first,second=knowledge_pair(store)
    rid=store.create_run('analysis',question)
    ids=[store.save_knowledge(c,rid,'reviewed',{'verdict':'supported'})[0] for c in (first,second)]
    model=proposal(pid)
    if scope:model['links'][0]['scope']=scope
    result=store.save_problem_model(rid,model,dict(zip(('new:0','new:1'),ids)),
        {'new:0':'new','new:1':'reused'}, {'verdict':model_verdict,'reason':'组合复核理由'},
        {0:{'verdict':link_verdict,'reason':'联系复核理由','check_errors':[]}})
    store.finish_run(rid,'completed',{'answer':'测试用历史答案','local_model':result})
    return rid,ids


def test_local_model_references_and_roles_use_shared_identity(workspace):
    _,store=workspace
    first,ids=saved_model(store,'系统切换需要哪些状态条件？')
    second,again=saved_model(store,'判断切换前需要读取哪些状态？')
    assert ids==again
    assert len(store.knowledge())==2
    assert {u['run_id'] for u in store.knowledge_usage(ids[0])}=={first,second}
    model=store.problem_model(second)
    assert model['effective_status']=='reviewed'
    assert model['knowledge_uses'][0]['knowledge']['id']==ids[0]
    assert model['required_inputs'] and model['boundaries']
    assert store.related_models(ids)


def test_relation_only_shared_after_distinct_questions_and_separate_review(workspace):
    _,store=workspace
    first,_=saved_model(store,'问题一：切换需要什么状态？')
    assert not store.shared_links(reusable_only=True)
    saved_model(store,' 问题一：切换需要什么状态？ ')
    assert store.shared_links()[0]['support_count']==1
    saved_model(store,'问题二：目标状态满足能否证明正常？',model_verdict='insufficient')
    saved_model(store,'问题三：切换限制有哪些？',link_verdict='insufficient')
    assert store.shared_links()[0]['support_count']==1
    saved_model(store,'问题四：还需要哪些事件输入？')
    shared=store.shared_links(reusable_only=True)
    assert len(shared)==1 and shared[0]['support_count']==2
    assert shared[0]['validations'][0]['run_id']==first
    assert any(not v['eligible'] for v in shared[0]['validations'])


def test_scope_conditions_and_direction_do_not_merge(workspace):
    _,store=workspace
    saved_model(store,'姑苏站切换目标条件是什么？')
    saved_model(store,'另一站的规则能否复用？',scope='另一站控制系统')
    assert len(store.shared_links())==2
    assert not store.shared_links(reusable_only=True)


def test_withdrawal_source_change_and_clear_disable_shared_links(workspace):
    settings,store=workspace
    store.doc_dir=settings.doc_dir
    first,ids=saved_model(store,'切换的目标状态要求？')
    saved_model(store,'切换规则如何解释？')
    assert store.shared_links(reusable_only=True)
    store.set_knowledge_status(ids[0],'withdrawn')
    assert not store.shared_links(reusable_only=True)
    assert store.problem_model(first)['effective_status']=='unavailable'
    store.set_knowledge_status(ids[0],'reviewed')
    (settings.doc_dir/'姑苏站规程.md').write_text('来源已经改变，尚未重新建立索引。')
    assert not store.shared_links(reusable_only=True)
    assert not store.knowledge(reusable_only=True)
    store.clear_knowledge()
    assert not store.shared_links() and not store.related_models(ids)
    historical=store.problem_model(first)
    assert historical['knowledge_uses'][0]['snapshot']['title']
    assert historical['knowledge_uses'][0]['knowledge'] is None
    assert store.stats()['passages']>0


def test_unrelated_withdrawn_premise_invalidates_combination(workspace):
    _,store=workspace
    pid,first,second=knowledge_pair(store)
    rid=store.create_run('analysis','组合包含第三个前提')
    third=copy.deepcopy(first);third['statement']='第三条测试前提，用于检查依赖失效。'
    ids=[store.save_knowledge(c,rid,'reviewed',{})[0] for c in [first,second,third]]
    model=proposal(pid);model['knowledge_uses'].append({'ref':'new:2','role':'提供第三个适用前提'})
    store.save_problem_model(rid,model,{f'new:{i}':kid for i,kid in enumerate(ids)},
                            {f'new:{i}':'new' for i in range(3)}, {'verdict':'supported'}, {0:{'verdict':'supported'}})
    store.finish_run(rid,'completed',{})
    saved_model(store,'第二次核查相同联系')
    assert store.shared_links(reusable_only=True)
    store.set_knowledge_status(ids[2],'withdrawn')
    assert not store.shared_links(reusable_only=True)


def test_model_ref_validation_and_historical_fallback(workspace):
    _,store=workspace
    pid,first,second=knowledge_pair(store)
    model=ProblemModel.model_validate(proposal(pid)).model_dump()
    assert not validate_model_refs(model,[first,second],[])
    model['links'][0]['target_ref']='k_invented'
    assert validate_model_refs(model,[first,second],[])
    model=proposal(pid);model['knowledge_uses'].pop()
    assert validate_model_refs(model,[first,second],[])
    rid=store.create_run('analysis','功能上线前的问题')
    kid,_=store.save_knowledge(first,rid,'reviewed',{})
    store.finish_run(rid,'completed',{'answer':'历史回答','knowledge_changes':[{'id':kid,'title':first['title']}],
                                     'reused_knowledge_ids':[],'unresolved_questions':['历史缺口']})
    result=store.problem_model(rid)
    assert result['status']=='historical' and result['links']==[]
    assert not store.shared_links()


class OrganizedModel:
    def __init__(self,pid,first,second,*,reused=None,links_supported=True,model_supported=True,bad_quote=False):
        self.pid,self.items,self.step=pid,[first,second],0
        self.reused=reused
        self.links_supported,self.model_supported,self.bad_quote=links_supported,model_supported,bad_quote
        self.recall_result=None

    async def chat(self,messages,tools=None,review=False,on_delta=None):
        if review:
            payload=json.loads(messages[-1]['content'])
            assert payload['local_model']['links']
            answer='本轮只判断系统切换的目标状态约束；满足这一条件不能独立证明全站正常。['+self.pid+']'
            return {'content':json.dumps({'reviews':[{'index':i,'verdict':'supported','reason':'知识原文支持'} for i in range(len(payload['candidates']))],
                    'model_review':{'verdict':'supported' if self.model_supported else 'insufficient','reason':'检查用途与组合边界'},
                    'link_reviews':[{'index':0,'verdict':'supported' if self.links_supported else 'insufficient','reason':'单独复核知识联系'}],
                    'answer_review':{'answer':answer,'issues':[],'unresolved_questions':['缺少当时状态']}})},{}
        self.step+=1
        if self.step==1 and self.reused:
            name,args='search_knowledge',{'ids':self.reused}
        elif self.step==1 or (self.reused and self.step==2):
            if self.reused:self.recall_result=json.loads(messages[-1]['content'])
            name,args='read_passages',{'ids':[self.pid]}
        else:
            model=proposal(self.pid)
            if self.reused:
                for i,u in enumerate(model['knowledge_uses']):u['ref']=self.reused[i]
                model['links'][0].update(source_ref=self.reused[0],target_ref=self.reused[1])
            if self.bad_quote:model['links'][0]['evidence'][0]['quote']='伪造的关系依据，不在任何已读原文中出现。'
            name,args='submit_result',{'answer':'按原文检查切换目标的状态约束，并保留判断所需的事件输入与结论边界。['+self.pid+']',
                      'knowledge_updates':[] if self.reused else self.items,'reused_knowledge_ids':self.reused or [],
                      'unresolved_questions':['缺少当时状态'],'local_model':model}
        return {'role':'assistant','tool_calls':[{'id':f'call_{self.step}','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]},{}


def test_two_agent_runs_reuse_knowledge_and_review_combination(workspace):
    settings,store=workspace
    pid,first,second=knowledge_pair(store)
    rid=store.create_run('analysis','切换目标状态有什么要求？')
    result=asyncio.run(ModelingAgent(settings,store,OrganizedModel(pid,first,second)).run(rid,'切换目标状态有什么要求？',4))
    assert result['local_model']['status']=='reviewed'
    ids=[k['id'] for k in result['knowledge_changes']]
    rid2=store.create_run('analysis','该状态约束还需要哪些输入才能应用？')
    model=OrganizedModel(pid,first,second,reused=ids)
    result2=asyncio.run(ModelingAgent(settings,store,model).run(rid2,'该状态约束还需要哪些输入才能应用？',4))
    assert result2['knowledge_changes']==[] and len(store.knowledge())==2
    assert model.recall_result['prior_models'][0]['run_id']==rid
    assert model.recall_result['shared_links']==[]
    assert len(store.shared_links(reusable_only=True))==1
    with TestClient(create_app(settings)) as client:
        response=client.get(f'/api/runs/{rid2}/model')
        assert response.json()['effective_status']=='reviewed'
        assert client.get('/api/knowledge-links').json()[0]['support_count']==2
        assert len(client.get('/api/knowledge').json()[0]['used_in'])==2
        assert client.get('/api/runs/missing/model').status_code==404
        assert client.post('/api/knowledge/clear',headers={'Origin':'https://untrusted.example'}).status_code==403


@pytest.mark.parametrize('bad_quote,links_supported,model_supported',[(True,True,True),(False,False,True),(False,True,False)])
def test_atomic_support_does_not_approve_invalid_combinations(workspace,bad_quote,links_supported,model_supported):
    settings,store=workspace
    pid,first,second=knowledge_pair(store)
    rid=store.create_run('analysis','检查错误组合不会升级')
    model=OrganizedModel(pid,first,second,bad_quote=bad_quote,links_supported=links_supported,model_supported=model_supported)
    result=asyncio.run(ModelingAgent(settings,store,model).run(rid,'检查错误组合不会升级',3))
    assert result and all(k['status']=='reviewed' for k in result['knowledge_changes'])
    assert result['local_model']['status']=='candidate'
    assert not store.shared_links(reusable_only=True)


def test_conflicting_relation_is_not_outvoted_by_positive_reviews(workspace):
    _,store=workspace
    saved_model(store,'问题一：定义如何解释规则？')
    saved_model(store,'问题二：还需要什么状态输入？')
    assert store.shared_links(reusable_only=True)
    saved_model(store,'问题三：发现解释存在冲突',link_verdict='conflict')
    assert store.shared_links()[0]['status']=='conflict'
    assert not store.shared_links(reusable_only=True)
    saved_model(store,'问题四：再次得到肯定意见')
    assert store.shared_links()[0]['support_count']==3
    assert not store.shared_links(reusable_only=True)


def test_tool_schema_exposes_nested_object_types_without_refs():
    from semantic_agent.agent import TOOLS
    schema=next(t['function']['parameters'] for t in TOOLS if t['function']['name']=='submit_result')
    assert '$ref' not in json.dumps(schema) and '$defs' not in schema
    local=schema['properties']['local_model']
    assert local['type']=='object'
    assert local['properties']['knowledge_uses']['items']['type']=='object'
    assert local['properties']['links']['items']['properties']['evidence']['items']['type']=='object'
    assert schema['properties']['knowledge_updates']['items']['properties']['title']['minLength']==2
