"""Actual localhost API/Agents/SQLite; model generation explicitly simulated."""
import asyncio
import json
import hashlib
import os
from pathlib import Path
from datetime import datetime
import pytest
from sqlalchemy import select
from app.models import Deck,Flashcard,ReviewSchedule,StudyPack,QuestionBank,KnowledgeCoverage,APIQuota,APICache,APICallLog
from app.core.agents.base import AgentRegistry
from app.core.agents.question_bank_agent import QuestionBankAgent
from tests.test_direct_generation_transaction_flow import direct,contract,flow,payload,size,deck_barrier
from tests.test_question_generation_transaction_flow import run_question
from tests.test_historical_summary_generation_flow import historical,nodes
from tests.test_pipeline_lifecycle_flow import localhost
from tests.test_generation_cache_isolation_flow import cache_flow,rows


def evidence(name,started,origin,samples,**extra):
    out=Path(os.environ['KNOWALL_DIRECT_EVIDENCE_DIR']);out.mkdir(parents=True,exist_ok=True)
    hashes={p:hashlib.sha256((Path(__file__).resolve().parents[1]/p).read_bytes()).hexdigest() for p in (
        'app/api/flashcards.py','app/core/agents/question_bank_agent.py','app/core/agents/summary_nodes.py','app/core/api_scheduler/client.py')}
    (out/name).write_text(json.dumps({'Start':started,'End':datetime.now().astimezone().isoformat(),'Origin':origin,
        'ExternalAI':'explicit simulation, no real provider network','RequestCount':len(samples),
        'SourceHashes':hashes,'Samples':samples,**extra},ensure_ascii=False,indent=2),encoding='utf-8')


async def test_actual_http_direct_parallel_failure_retry_and_private_pack(direct,monkeypatch):
    started=datetime.now().astimezone().isoformat();samples=[]
    async with direct.factory() as db:
        db.add(Deck(id='private',name='shared-name'));await db.flush()
        db.add(StudyPack(id='private-pack',user_id='bob',document_id='source',deck_id='private',title='Bob',source_hash='temporary'));await db.commit()
    arrivals=deck_barrier(monkeypatch)
    async with localhost(direct.app) as client:
        responses=await asyncio.gather(*(client.post('/api/v1/flashcards/generate',headers=direct.alice,json=payload()) for _ in range(2)))
        for r in responses:
            assert r.status_code==200,r.text;samples.append({'path':'/flashcards/generate','account':'alice','status':r.status_code,'response':r.json()})
        ids={r.json()['deck_id'] for r in responses};assert len(ids)==1 and 'private' not in ids and len(set(arrivals))==2
        direct.cards.side_effect=RuntimeError('explicit simulated HTTP provider failure')
        failed=await client.post('/api/v1/flashcards/generate',headers=direct.alice,json=payload(deck_name='recovery'))
        assert failed.status_code==500
        samples.append({'path':'/flashcards/generate','account':'alice','status':500,'response':failed.json()})
        assert await size(direct,Deck)==2
        direct.cards.side_effect=direct.simulated
        retry=await client.post('/api/v1/flashcards/generate',headers=direct.alice,json=payload(deck_name='recovery'))
        assert retry.status_code==200 and retry.json()['generated_count']==3,retry.text
        samples.append({'path':'/flashcards/generate','account':'alice','status':200,'response':retry.json()})
        listing=await client.get('/api/v1/flashcards/decks',headers=direct.alice)
        assert listing.status_code==200,listing.text
        assert 'private' not in {d['id'] for d in listing.json()}
        samples.append({'path':'/flashcards/decks','account':'alice','status':200,'response':listing.json()})
        origin=str(client.base_url)
    async with direct.factory() as db:
        assert (await db.get(Deck,next(iter(ids)))).card_count==6
        assert (await db.get(Deck,'private')).card_count==0
    assert await size(direct,Flashcard)==await size(direct,ReviewSchedule)==9
    evidence('real-direct-http.json',started,origin,samples,Cards=9,PrivateDeckCount=0,SharedDeckCount=6)


@pytest.mark.parametrize('stream',[False,True],ids=['rest','sse'])
async def test_actual_http_historical_question_provider_usage_failure_recovery(contract,monkeypatch,stream):
    await historical(contract)
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:[QuestionBankAgent()])
    original=contract.quiz.side_effect;writes=[]
    from sqlalchemy import update
    from app.models import StudyPlan
    async def simulated(**kwargs):
        async with contract.factory() as db:
            await db.execute(update(StudyPlan).values(name='explicit simulated provider usage'));await db.commit()
        writes.append(True)
        return await original(**kwargs)
    contract.quiz.side_effect=simulated
    path='/api/v1/agents/orchestrate'+('/stream' if stream else '')
    config={'question_count':8,'question_types':['short_answer'],'enable_review':False,'enable_kg_relations':False,'model':'explicit-simulation','user_id':'forged'}
    started=datetime.now().astimezone().isoformat();samples=[]
    async with localhost(contract.app) as client:
        for phase in ('normal','failure','recovery'):
            contract.quiz.side_effect=RuntimeError('explicit simulated HTTP failure') if phase=='failure' else simulated
            r=await client.post(path,headers=contract.alice,json={'summary_id':'summary','document_id':'source','agents':['question_bank'],'config':config})
            assert r.status_code==200,r.text
            values=[json.loads(l[6:]) for l in r.text.splitlines() if l.startswith('data: ')] if stream else []
            result=values[-1]['results']['question_bank'] if stream else r.json()['results']['question_bank']
            assert result['status']==('error' if phase=='failure' else 'success'),result
            if phase!='failure':assert result['result']['total_questions']==8
            samples.append({'phase':phase,'account':'alice','status':r.status_code,'result':result,'raw':r.text})
        origin=str(client.base_url)
    assert len(await nodes(contract))==3
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==16
    assert len(writes)>=6
    evidence('real-question-'+('sse' if stream else 'rest')+'.json',started,origin,samples,QuestionCount=16,ProviderUsageWrites=len(writes))


async def test_actual_http_native_card_parser_client_cache_usage_signed_accounts(direct,cache_flow,monkeypatch):
    from app.core import memory
    generator=memory.FlashcardGenerator()
    monkeypatch.setattr(memory,'api_client',cache_flow.api)
    monkeypatch.setattr(memory.card_generator,'generate',generator.generate)
    body=json.dumps({'cards':[{'front':'明确模拟原生问题'+str(i),'back':'明确模拟答案','card_type':'qa',
        'tags':{'domain_tags':['模拟学科'],'memory_tags':['需记忆']}} for i in range(2)]},ensure_ascii=False)
    for adapter in cache_flow.adapters.values():adapter.content=body
    started=datetime.now().astimezone().isoformat();samples=[]
    async with localhost(direct.app) as client:
        for user in ('alice','alice','bob'):
            r=await client.post('/api/v1/flashcards/generate',headers=getattr(direct,user),json=payload(model='primary',count=2))
            assert r.status_code==200 and r.json()['generated_count']==2,r.text
            samples.append({'account':user,'status':r.status_code,'response':r.json()})
        origin=str(client.base_url)
    assert await size(direct,Flashcard)==await size(direct,ReviewSchedule)==6
    async with direct.factory() as db:
        assert (await db.get(Deck,samples[0]['response']['deck_id'])).card_count==6
        cards=(await db.scalars(select(Flashcard))).all()
    assert all(c.tags=={'domain_tags':['模拟学科'],'memory_tags':['需记忆']} for c in cards)
    quotas=await rows(cache_flow,APIQuota);logs=await rows(cache_flow,APICallLog);caches=await rows(cache_flow,APICache)
    assert len(logs)==len(caches)==2
    assert {q.user_id:q.used_today for q in quotas}=={'alice':18,'bob':18}
    assert len(cache_flow.adapters['primary_alice'].calls)==len(cache_flow.adapters['primary_bob'].calls)==1
    assert {row.model_name for row in logs}=={'primary_alice','primary_bob'}
    evidence('real-native-cards.json',started,origin,samples,NativeGenerator=True,SimulationBoundary='low-level Adapter only',
        Quota={q.user_id:q.used_today for q in quotas},Calls=2,CacheEntries=2,Cards=6)
