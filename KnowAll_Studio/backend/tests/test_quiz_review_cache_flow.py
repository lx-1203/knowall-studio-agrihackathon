"""Actual SQL cache/quota and quiz review; external AI adapter is simulated."""
import copy
import json
import pytest
from sqlalchemy import update
from app.core import quiz
from app.core.api_scheduler.client import _current_user_id
from app.models import APICache, APICallLog, APIQuota
from tests.test_generation_cache_isolation_flow import cache_flow, flow, rows

QUESTION={'id':'q_1','question_type':'fill_blank','question_text':'模拟元素___和___',
          'answer':'氢;氧','blanks':[{'index':1,'answer':'氢'},{'index':2,'answer':'氧'}]}
REVIEW={'question_id':'q_1','decision':'pass','scores':{'clarity':.9},'total_score':4.1,
        'issues':[],'suggestions':[]}
GOOD={'reviews':[REVIEW],'summary':{'passed':1,'revise':0,'rejected':0,'average_total':4.1}}
BAD=['bad json','null','[]','4','{}','{"reviews":[]}','{"reviews":{}}','{"reviews":null}',
     json.dumps({'reviews':[None]}),json.dumps({'reviews':['broken']}),
     json.dumps({'reviews':[{'question_id':'q_1'}]}),
     *[json.dumps({'reviews':[{**REVIEW,**change}]}) for change in [
         {'decision':'unknown'},{'decision':None},{'decision':[]},{'question_id':'foreign'},
         {'question_id':''},{'question_id':['q_1']},{'scores':[]},{'scores':{'clarity':True}},
         {'scores':{'clarity':float('nan')}},{'scores':{'clarity':1.1}},
         {'total_score':-1},{'total_score':float('inf')},{'total_score':'4.1'},{'total_score':True},
         {'issues':'bad'},{'suggestions':[1]}]],
     json.dumps({'reviews':[REVIEW,{**REVIEW,'decision':'reject'}]}),
     json.dumps({'reviews':[REVIEW,None]}),
     json.dumps({**GOOD,'summary':[]})]

@pytest.fixture
def reviewer(cache_flow,monkeypatch):
    _current_user_id.set('alice')
    native=cache_flow.api.generate
    async def no_retry(**kwargs):
        kwargs['config'].max_retries=0
        return await native(**kwargs)
    monkeypatch.setattr(quiz,'api_client',type('Client',(),{'generate':staticmethod(no_retry)})())
    return quiz.QuizGenerator()

async def review(g,questions=None):
    return await g._review_and_refine(copy.deepcopy(questions or [QUESTION]),'明确模拟知识',
             quiz.QuizGenerationConfig(question_type='fill_blank'), 'primary')

@pytest.mark.parametrize('bad',BAD)
async def test_invalid_review_preserves_questions_is_uncached_and_retry_recovers(reviewer,cache_flow,bad):
    adapter=cache_flow.adapters['primary_alice'];adapter.content=bad
    result=await review(reviewer)
    assert len(result)==1 and result[0]['answer']==QUESTION['answer']
    assert result[0]['reviewed'] is False
    assert await rows(cache_flow,APICache)==[]
    logs=await rows(cache_flow,APICallLog)
    assert len(logs)==1 and logs[0].success is False
    assert (logs[0].tokens_input,logs[0].tokens_output)==(7,11)
    adapter.content=json.dumps(GOOD)
    for _ in range(2):
        result=await review(reviewer)
        assert result[0]['reviewed'] is True and result[0]['review_decision']=='pass'
    assert len(adapter.calls)==2
    assert len(await rows(cache_flow,APICache))==1
    assert (await rows(cache_flow,APIQuota))[0].used_today==36

async def test_old_invalid_review_cache_is_evicted_and_paid_result_replaces_it(reviewer,cache_flow):
    adapter=cache_flow.adapters['primary_alice'];adapter.content=json.dumps(GOOD)
    assert (await review(reviewer))[0]['reviewed'] is True
    async with cache_flow.factory() as db:
        await db.execute(update(APICache).values(response_content=json.dumps({'reviews':[{'question_id':'q_1','decision':'typo'}]})))
        await db.commit()
    assert (await review(reviewer))[0]['reviewed'] is True
    assert len(adapter.calls)==2
    assert len(await rows(cache_flow,APICache))==1

async def test_valid_reject_and_partial_review_keep_existing_contract(reviewer,cache_flow):
    adapter=cache_flow.adapters['primary_alice']
    adapter.content=json.dumps({'reviews':[{**REVIEW,'decision':'reject'}]})
    result=await review(reviewer,[QUESTION,{**QUESTION,'id':'q_2'}])
    assert len(result)==1 and result[0]['reviewed'] is False
    assert result[0]['answer']==QUESTION['answer']
    assert len(await rows(cache_flow,APICache))==1

async def test_valid_revision_uses_new_rules_and_cache_on_repeat(reviewer,cache_flow):
    adapter=cache_flow.adapters['primary_alice']
    async def simulated(messages,config):
        adapter.calls.append({'messages':messages,'config':vars(config)})
        from app.core.api_scheduler.adapters.base import AdapterResponse
        revise='修改后的完整题目' in messages[-1]['content'] or '审核意见' in messages[0]['content']
        body={'revised_questions':[{**QUESTION,'answer':'钠;氯','blanks':[{'index':1,'answer':'钠'},{'index':2,'answer':'氯'}]}]} if revise else {'reviews':[{**REVIEW,'decision':'revise'}]}
        return AdapterResponse(json.dumps(body),json.dumps(body),'explicit-simulated',tokens_input=7,tokens_output=11)
    adapter.chat_completion=simulated
    result=await review(reviewer)
    assert result[0]['answer']=='钠;氯' and result[0]['review_decision']=='revised'
    assert result[0]['blanks'][0]['answer']=='钠'
    assert (await review(reviewer))[0]['answer']=='钠;氯'
    assert len(adapter.calls)==2 and len(await rows(cache_flow,APICache))==2

async def test_input_question_ids_without_unique_valid_strings_do_not_conflate_reviews(reviewer,cache_flow):
    adapter=cache_flow.adapters['primary_alice']
    adapter.content=json.dumps({'reviews':[{**REVIEW,'decision':'reject'}]})
    result=await review(reviewer,[QUESTION,{**QUESTION,'question_text':'不同原题___','id':'q_1'}])
    assert len(result)==2 and all(q['reviewed'] is False for q in result)
    assert await rows(cache_flow,APICache)==[]

@pytest.mark.parametrize('qid',[None,{},' '])
async def test_invalid_input_ids_skip_paid_review_and_keep_original(reviewer,cache_flow,qid):
    result=await review(reviewer,[{**QUESTION,'id':qid}])
    assert len(result)==1 and result[0]['reviewed'] is False
    assert cache_flow.adapters['primary_alice'].calls==[]
    assert await rows(cache_flow,APICache)==await rows(cache_flow,APICallLog)==[]

@pytest.mark.parametrize('summary',[{'passed':True},{'revise':-1},{'rejected':'1'},
                                  {'average_total':float('nan')},{'average_total':5.1}])
async def test_malformed_optional_summary_cannot_poison_paid_cache(reviewer,cache_flow,summary):
    cache_flow.adapters['primary_alice'].content=json.dumps({**GOOD,'summary':summary})
    result=await review(reviewer)
    assert len(result)==1 and result[0]['reviewed'] is False
    assert await rows(cache_flow,APICache)==[]
    assert (await rows(cache_flow,APICallLog))[0].success is False

async def test_bad_primary_review_falls_back_and_accounts_for_both_paid_attempts(reviewer,cache_flow,monkeypatch):
    native=cache_flow.api.generate
    async def fallback(**kwargs):
        kwargs['config'].max_retries=0;kwargs['config'].fallback_models=['alternate']
        return await native(**kwargs)
    monkeypatch.setattr(quiz,'api_client',type('Client',(),{'generate':staticmethod(fallback)})())
    cache_flow.adapters['primary_alice'].content=json.dumps({'reviews':[{**REVIEW,'decision':'typo'}]})
    cache_flow.adapters['alternate'].content=json.dumps(GOOD)
    assert (await review(reviewer))[0]['reviewed'] is True
    assert (await review(reviewer))[0]['reviewed'] is True
    logs=await rows(cache_flow,APICallLog)
    assert [l.success for l in logs]==[False,True]
    assert (await rows(cache_flow,APIQuota))[0].used_today==36
    assert len(cache_flow.adapters['primary_alice'].calls)==len(cache_flow.adapters['alternate'].calls)==1

async def test_review_cache_and_bad_output_recovery_are_private_to_account(reviewer,cache_flow):
    cache_flow.adapters['primary_alice'].content=json.dumps(GOOD)
    assert (await review(reviewer))[0]['reviewed'] is True
    _current_user_id.set('bob');cache_flow.adapters['primary_bob'].content='bad JSON'
    assert (await review(reviewer))[0]['reviewed'] is False
    cache_flow.adapters['primary_bob'].content=json.dumps(GOOD)
    assert (await review(reviewer))[0]['reviewed'] is True
    _current_user_id.set('alice');assert (await review(reviewer))[0]['reviewed'] is True
    assert len(cache_flow.adapters['primary_alice'].calls)==1 and len(cache_flow.adapters['primary_bob'].calls)==2
    assert {q.user_id:q.used_today for q in await rows(cache_flow,APIQuota)}=={'alice':18,'bob':36}
