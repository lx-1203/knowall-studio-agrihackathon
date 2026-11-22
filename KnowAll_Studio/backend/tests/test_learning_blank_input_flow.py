"""Public input shape backed by real private SQLite; model explicitly simulated."""
import json
import pytest
from tests.test_learning_pack_rubric_flow import flow,make,question
from app.core import study_pack_service as service

@pytest.mark.parametrize('mode',['practice','smart'])
@pytest.mark.parametrize('count',[1,2,3,8])
async def test_session_exposes_count_without_rule_answers(flow,mode,count):
    raw=question();raw['blanks']=[{'index':i+1,'answer':'secret-'+str(i),'hint':'secret-hint'} for i in range(count)]
    raw['answer']=';'.join(b['answer'] for b in raw['blanks'])
    pack=await make(flow,[raw])
    async with flow.factory() as db:
        if mode=='smart':await service.submit_answer(db,pack['id'],'alice',pack['sections'][0]['questions'][0]['id'],'错误')
        data=await service.build_session(db,pack['id'],'alice',mode=mode)
    q=data['items'][0]['question']
    assert q['blank_count']==count
    assert not set(q).intersection({'blanks','answer','correct_answer','analysis','scoring_points'})
    assert 'secret-' not in json.dumps(q)

@pytest.mark.parametrize('kind',['fill_blank','short_answer'])
@pytest.mark.parametrize('mode',['practice','smart'])
async def test_legacy_or_other_kind_does_not_invent_blanks(flow,kind,mode):
    raw=question(kind);raw.pop('blanks',None)
    pack=await make(flow,[raw],question_types=[kind])
    async with flow.factory() as db:
        if mode=='smart':await service.submit_answer(db,pack['id'],'alice',pack['sections'][0]['questions'][0]['id'],'错误')
        data=await service.build_session(db,pack['id'],'alice',mode=mode)
    assert 'blank_count' not in data['items'][0]['question']

async def test_input_shape_owner_and_empty_first_slot(flow):
    pack=await make(flow);pid=pack['id'];qid=pack['sections'][0]['questions'][0]['id']
    forbidden=await flow.client.post(f'/api/v1/learning/packs/{pid}/session',headers=flow.bob,json={'mode':'practice'})
    assert forbidden.status_code==404
    response=await flow.client.post(f'/api/v1/learning/packs/{pid}/session',headers=flow.alice,json={'mode':'practice'})
    assert response.json()['items'][0]['question']['blank_count']==2
    answer=await flow.client.post(f'/api/v1/learning/packs/{pid}/answers',headers=flow.alice,json={'question_id':qid,'answer':';氧'})
    assert answer.status_code==200 and answer.json()['score_ratio']==.5
