"""Real localhost/SQLite; all external generation explicitly simulated."""
import asyncio
import json
import hashlib
import os
from pathlib import Path
from datetime import datetime
from sqlalchemy import update,select,func
import pytest
from tests.test_agent_generation_contract_flow import contract,flow,options,size
from tests.test_pipeline_lifecycle_flow import localhost
from tests.test_historical_summary_generation_flow import historical,nodes
from tests.test_deck_generation_concurrency_flow import generate,deck_barrier
from app.models import StudyPlan,Flashcard,Deck,ReviewSchedule,KnowledgeCoverage
from app.core.agents.base import AgentRegistry
from app.core.agents.flashcard_agent import FlashcardAgent
from app.core.agents.study_plan_agent import StudyPlanAgent


@pytest.mark.parametrize('review',[False,True])
async def test_historical_summary_and_quality_review_do_not_lock_usage_writes(contract,review):
    await historical(contract)
    original=contract.cards.side_effect;writes=[]
    async def actual_usage():
        async with contract.factory() as db:
            await db.execute(update(StudyPlan).values(name='explicitly simulated AI quota/log write'))
            await db.commit()
        writes.append(True)
    async def simulated_ai(**kwargs):await actual_usage();return await original(**kwargs)
    async def simulated_review(cards,model,stats):await actual_usage();return cards
    contract.cards.side_effect=simulated_ai
    contract.review.side_effect=simulated_review
    result=await generate(contract,card_count=12 if review else 3,enable_quality_review=review)
    assert result.status=='success',result.error
    assert writes and len(await nodes(contract))==3
    assert result.result['total_cards']==(12 if review else 3)
    if review:assert contract.review.await_count==1


@pytest.mark.parametrize('stream',[False,True],ids=['rest','sse'])
async def test_real_http_concurrent_cards_repeat_and_signed_plan_owner(contract,monkeypatch,stream):
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:[FlashcardAgent() if name=='flashcard' else StudyPlanAgent() for name in names])
    # Synchronize real SELECT results, never replace SQL/Agent results.
    arrivals=deck_barrier(monkeypatch)
    started=datetime.now().astimezone().isoformat();samples=[]
    path='/api/v1/agents/orchestrate'+('/stream' if stream else '')
    payload={'summary_id':'summary','document_id':'source','agents':['flashcard'],
             'config':{**options('card',3),'deck_name':'http-concurrent','user_id':'forged'}}
    async with localhost(contract.app) as client:
        replies=await asyncio.gather(*(client.post(path,headers=contract.alice,json=payload) for _ in range(2)))
        replies.append(await client.post(path,headers=contract.alice,json=payload))
        ids=[]
        for response in replies:
            assert response.status_code==200,response.text
            data=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')] if stream else []
            value=data[-1]['results']['flashcard'] if stream else response.json()['results']['flashcard']
            assert value['status']=='success',value
            ids.append(value['result']['deck_id'])
            samples.append({'account':'alice','status':200,'result':value,'raw':response.text})
        assert len(set(arrivals))==2 and len(set(ids))==1
        plan_payload={'summary_id':'summary','document_id':'source','agents':['study_plan'],
            'config':{'user_id':'alice','study_plan':{'type':'short','daily_hours':1.5,'ebbinghaus_enabled':False},'start_date':'2026-10-10'}}
        response=await client.post(path,headers=contract.bob,json=plan_payload)
        assert response.status_code==200,response.text
        data=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')] if stream else []
        value=data[-1]['results']['study_plan'] if stream else response.json()['results']['study_plan']
        assert value['status']=='success',value
        plan_id=value['result']['plan_id']
        samples.append({'account':'bob','status':200,'result':value,'raw':response.text})
        foreign=await client.get('/api/v1/study/plans/'+plan_id,headers=contract.alice)
        own=await client.get('/api/v1/study/plans/'+plan_id,headers=contract.bob)
        listing=await client.get('/api/v1/study/plans',headers=contract.alice)
        assert foreign.status_code==404 and own.status_code==200 and listing.status_code==200
        assert listing.json()==[] and own.json()['daily_hours']==1.5
        samples.extend([{'account':'alice','status':foreign.status_code},{'account':'bob','status':own.status_code,'response':own.json()},{'account':'alice','status':listing.status_code,'response':listing.json()}])
        origin=str(client.base_url)
    async with contract.factory() as db:
        assert (await db.get(Deck,ids[0])).card_count==9
        assert (await db.get(StudyPlan,plan_id)).user_id=='bob'
    assert contract.cards.await_count==3
    for model in (Flashcard,ReviewSchedule,KnowledgeCoverage):assert await size(contract,model)==9
    out=Path(os.environ['KNOWALL_DECK_EVIDENCE_DIR']);out.mkdir(parents=True,exist_ok=True)
    hashes={name:hashlib.sha256((Path(__file__).resolve().parents[1]/name).read_bytes()).hexdigest() for name in ('app/core/agents/flashcard_agent.py','app/core/agents/study_plan_agent.py','app/core/agents/summary_nodes.py')}
    (out/('real-deck-sse.json' if stream else 'real-deck-rest.json')).write_text(json.dumps({
        'Start':started,'End':datetime.now().astimezone().isoformat(),'Origin':origin,'RequestCount':len(samples),
        'AI':'explicit simulated card generator/quality review and coverage; no real provider network',
        'SimulatedGeneratorCalls':contract.cards.await_count,'SourceHashes':hashes,'Samples':samples,
        'DeckCount':1,'Cards':9,'DeckCardCount':9,'PlanOwner':'bob'},ensure_ascii=False,indent=2),encoding='utf-8')
