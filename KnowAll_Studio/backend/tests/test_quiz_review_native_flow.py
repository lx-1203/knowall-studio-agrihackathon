"""True local HTTP/JWT/SQLite/OpenAI-compatible adapter, explicitly simulated AI."""
import copy,hashlib,json,os
from datetime import datetime
from pathlib import Path
import pytest
from fastapi import FastAPI,Request
from sqlalchemy import select
from app.api import questions
from app.core import quiz
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from app.database import Base,get_db
from app.models import QuestionBank,ErrorLog,APICache,APICallLog,APIQuota
from tests.test_generation_cache_isolation_flow import cache_flow,flow,rows
from tests.test_quiz_review_cache_flow import QUESTION,REVIEW
from tests.test_reading_native_stream_http_flow import server

@pytest.fixture
def evidence(request,tmp_path):
    root=Path(__file__).resolve().parents[1]
    paths=['app/api/questions.py','app/core/quiz/__init__.py','app/core/api_scheduler/client.py',
           'app/core/api_scheduler/cache.py','app/core/api_scheduler/adapters/openai.py',
           'app/core/auth.py','app/database.py','app/models/__init__.py']
    hashes=lambda:{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
    data={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),
          'AI':'explicit localhost simulation, no live provider','SourceBefore':hashes(),
          'ProviderHTTP':[],'BackendHTTP':[],'TempDB':str(tmp_path/'study.sqlite')}
    yield data
    data['End']=datetime.now().astimezone().isoformat();data['SourceAfter']=hashes()
    out=Path(os.environ.get('KNOWALL_QUIZ_REVIEW_EVIDENCE_DIR',str(tmp_path/'network')))
    out.mkdir(parents=True,exist_ok=True)
    (out/(request.node.name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')

@pytest.mark.parametrize('mode',['bad-review','bad-revision','reject','partial'])
async def test_native_variants_review_cache_recovery(cache_flow,monkeypatch,evidence,mode):
    async with cache_flow.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
    async with cache_flow.factory() as db:
        db.add(QuestionBank(id='original',question_type='fill_blank',question_text='原题元素___和___',correct_answer='氢;氧',blanks=QUESTION['blanks']))
        await db.flush();db.add(ErrorLog(id='error',question_id='original',user_id='alice'));await db.commit()
    phase={'bad':True};provider=FastAPI()
    @provider.post('/v1/chat/completions')
    async def explicit_simulation(req:Request):
        assert req.headers['authorization']=='Bearer explicit-simulated-no-real-key'
        payload=await req.json();messages=payload['messages']
        is_review='题目列表（JSON格式）' in messages[-1]['content']
        is_revision='审核意见' in messages[0]['content']
        if is_revision:
            body={'revised_questions':[{**QUESTION,'blanks':{} if phase['bad'] else [{'index':1,'answer':'钠'},{'index':2,'answer':'氯'}], 'answer':'钠;氯'}]}
        elif is_review:
            decision='unknown' if mode=='bad-review' and phase['bad'] else 'revise' if mode=='bad-revision' else 'reject' if mode in ('reject','partial') else 'pass'
            body={'reviews':[{**REVIEW,'decision':decision}]}
            if mode=='partial':body['reviews'].append({**REVIEW,'question_id':'q_2'})
        else:
            body={'questions':[QUESTION]}
            if mode=='partial':body['questions'].append({**QUESTION,'id':'q_2','question_text':'第二变式元素___和___'})
        content=json.dumps(body,ensure_ascii=False)
        evidence['ProviderHTTP'].append({'URL':str(req.url),'Payload':payload,'Content':content,'Simulation':True,'Status':200})
        return {'model':'explicit-local-simulated','choices':[{'message':{'content':content},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':7,'completion_tokens':11}}
    backend=FastAPI();backend.include_router(questions.router)
    async def db():
        async with cache_flow.factory() as session:yield session
    backend.dependency_overrides[get_db]=db
    native=cache_flow.api.generate
    async def no_retry(**kwargs):
        kwargs['config'].max_retries=0
        return await native(**kwargs)
    monkeypatch.setattr(quiz,'api_client',type('Client',(),{'generate':staticmethod(no_retry)})())
    async with server(provider) as simulated:
        adapter=OpenAICompatAdapter('explicit-simulated-no-real-key',str(simulated.base_url).rstrip('/')+'/v1','explicit-local-simulated')
        cache_flow.api._adapters.clear();cache_flow.api._adapters['deepseek-chat_alice']=adapter
        async with server(backend) as client:
            async def post(headers):
                r=await client.post('/api/v1/quiz/errors/error/variants?count=2' if mode=='partial' else '/api/v1/quiz/errors/error/variants?count=1',headers=headers)
                evidence['BackendHTTP'].append({'URL':str(r.request.url),'Method':'POST','Status':r.status_code,'Body':r.text})
                return r
            r=await post(cache_flow.bob);assert r.status_code==404 and evidence['ProviderHTTP']==[]
            first=await post(cache_flow.alice)
            assert first.status_code==(502 if mode=='reject' else 200),first.text
            expected_initial=3 if mode=='bad-revision' else 2
            assert len(evidence['ProviderHTTP'])==expected_initial
            caches=await rows(cache_flow,APICache)
            assert len(caches)==(1 if mode=='bad-review' else 2)
            if mode in ('bad-review','bad-revision'):
                assert (await rows(cache_flow,APICallLog))[-1].success is False
                assert first.json()['questions'][0]['question_text']==QUESTION['question_text']
            if mode=='partial':assert first.json()['variant_count']==1 and first.json()['questions'][0]['question_text'].startswith('第二')
            phase['bad']=False
            second=await post(cache_flow.alice);third=await post(cache_flow.alice)
            assert second.status_code==third.status_code==(502 if mode=='reject' else 200)
            expected=expected_initial+(1 if mode in ('bad-review','bad-revision') else 0)
            assert len(evidence['ProviderHTTP'])==expected
            if mode=='bad-revision':
                async with cache_flow.factory() as session:
                    saved=(await session.scalars(select(QuestionBank).where(QuestionBank.parent_question_id=='original'))).all()
                    assert sum(q.correct_answer=='钠;氯' for q in saved)==2
                    assert all(isinstance(q.blanks,list) for q in saved)
            if mode=='reject':
                assert len(await rows(cache_flow,QuestionBank))==1
                assert (await rows(cache_flow,ErrorLog))[0].variant_generated is False
            quota=(await rows(cache_flow,APIQuota))[0];assert quota.user_id=='alice' and quota.used_today==expected*18
            evidence['SQL']={'CacheCount':len(await rows(cache_flow,APICache)),
                'QuotaUsed':quota.used_today,'Questions':len(await rows(cache_flow,QuestionBank)),
                'Logs':[{'Success':l.success,'Input':l.tokens_input,'Output':l.tokens_output} for l in await rows(cache_flow,APICallLog)]}
