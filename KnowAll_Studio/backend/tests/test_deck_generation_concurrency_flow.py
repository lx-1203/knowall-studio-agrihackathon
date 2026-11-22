"""Real disposable SQLite/Agents/FSRS; external AI is explicitly simulated."""
import asyncio
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select,func,event,update
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import Base
from app.models import Deck,Flashcard,ReviewSchedule,KnowledgeCoverage,StudyPlan,StudyPack,KnowledgeTree
from app.core.agents.flashcard_agent import FlashcardAgent
from app.core.agents.study_plan_agent import StudyPlanAgent
from tests.test_agent_generation_contract_flow import contract,flow,options,size
from tests.test_pipeline_lifecycle_flow import localhost


async def generate(contract,name='concurrent',**kwargs):
    return await FlashcardAgent().run('summary','source',**{**options('card',3),'deck_name':name,'user_id':'alice',**kwargs})


def deck_barrier(monkeypatch):
    execute=AsyncSession.execute
    gate=asyncio.Event();arrivals=[]
    async def read(session,statement,*args,**kwargs):
        result=await execute(session,statement,*args,**kwargs)
        if str(statement).startswith('SELECT decks.') and len(arrivals)<2:
            arrivals.append(id(session))
            if len(arrivals)==2:gate.set()
            await asyncio.wait_for(gate.wait(),5)
        return result
    monkeypatch.setattr(AsyncSession,'execute',read)
    return arrivals


@pytest.mark.parametrize('initial',[None,0,7])
async def test_real_concurrent_existing_deck_preserves_count_and_all_rows(contract,monkeypatch,initial):
    async with contract.factory() as db:
        db.add(Deck(id='existing',name='concurrent',card_count=initial))
        await db.commit()
        if initial is None:
            await db.execute(update(Deck).where(Deck.id=='existing').values(card_count=None))
            await db.commit()
    arrivals=deck_barrier(monkeypatch)
    results=await asyncio.gather(generate(contract),generate(contract))
    assert all(r.status=='success' for r in results),[r.error for r in results]
    assert len(set(arrivals))==2
    assert {r.result['deck_id'] for r in results}=={'existing'}
    assert len({i for r in results for i in r.result['card_ids']})==6
    async with contract.factory() as db:
        assert (await db.get(Deck,'existing')).card_count==(initial or 0)+6
    for model in (Flashcard,ReviewSchedule,KnowledgeCoverage):assert await size(contract,model)==6


async def test_real_simultaneous_first_deck_converges_without_losing_cards(contract,monkeypatch):
    arrivals=deck_barrier(monkeypatch)
    results=await asyncio.gather(generate(contract),generate(contract))
    assert all(r.status=='success' for r in results),[r.error for r in results]
    assert len(set(arrivals))==2
    assert len({r.result['deck_id'] for r in results})==1
    assert await size(contract,Deck)==1
    async with contract.factory() as db:assert (await db.get(Deck,results[0].result['deck_id'])).card_count==6
    for model in (Flashcard,ReviewSchedule,KnowledgeCoverage):assert await size(contract,model)==6


async def test_legacy_duplicate_names_select_stable_existing_deck(contract):
    async with contract.factory() as db:
        db.add_all([Deck(id='a',name='concurrent'),Deck(id='b',name='concurrent')])
        await db.commit()
    first=await generate(contract);second=await generate(contract)
    assert first.status==second.status=='success',(first.error,second.error)
    assert first.result['deck_id']==second.result['deck_id']
    assert await size(contract,Deck)==2
    async with contract.factory() as db:assert (await db.get(Deck,first.result['deck_id'])).card_count==6


async def test_named_generation_cannot_append_to_other_account_learning_pack(contract):
    async with contract.factory.kw['bind'].begin() as conn:await conn.run_sync(Base.metadata.create_all,tables=[KnowledgeTree.__table__,StudyPack.__table__])
    async with contract.factory() as db:
        db.add(Deck(id='private',name='concurrent'))
        await db.flush()
        db.add(StudyPack(id='pack',user_id='bob',document_id='source',deck_id='private',title='Bob',source_hash='test'))
        await db.commit()
    result=await generate(contract)
    assert result.status=='success',result.error
    assert result.result['deck_id']!='private'
    async with contract.factory() as db:assert (await db.get(Deck,'private')).card_count==0


async def test_new_deck_does_not_lock_other_session_during_simulated_ai_write(contract):
    original=contract.cards.side_effect
    writes=[]
    async def simulated_ai(**kwargs):
        # Actual SQL on a DIFFERENT session, like the real provider's quota/log.
        async with contract.factory() as db:
            await db.execute(update(StudyPlan).values(name='explicit simulated AI usage'))
            await db.commit()
        writes.append(True)
        return await original(**kwargs)
    contract.cards.side_effect=simulated_ai
    result=await generate(contract)
    assert result.status=='success',result.error
    assert writes


@pytest.mark.parametrize('failure',['empty','raise','cancel','storage'])
async def test_failed_generation_rolls_back_new_deck_and_resources_then_retries(contract,failure):
    original=contract.cards.side_effect
    engine=contract.factory.kw['bind'];entered=asyncio.Event()
    async def blocked(**kwargs):entered.set();await asyncio.Event().wait()
    def fail(conn,cursor,sql,params,context,many):
        if sql.startswith('INSERT INTO knowledge_coverage'):raise RuntimeError('explicit simulated storage failure')
    if failure=='empty':contract.cards.side_effect=None;contract.cards.return_value=[]
    elif failure=='raise':contract.cards.side_effect=RuntimeError('explicit simulated AI failure')
    elif failure=='cancel':contract.cards.side_effect=blocked
    else:event.listen(engine.sync_engine,'before_cursor_execute',fail)
    try:
        if failure=='cancel':
            task=asyncio.create_task(generate(contract))
            await asyncio.wait_for(entered.wait(),3);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        else:
            result=await generate(contract)
            assert result.status=='error'
    finally:
        if failure=='storage':event.remove(engine.sync_engine,'before_cursor_execute',fail)
        contract.cards.side_effect=original
    for model in (Deck,Flashcard,ReviewSchedule,KnowledgeCoverage):assert await size(contract,model)==0
    assert (await generate(contract)).status=='success'


async def test_two_different_deck_names_keep_counts_separate(contract):
    results=await asyncio.gather(generate(contract,'one'),generate(contract,'two'))
    assert all(r.status=='success' for r in results),[r.error for r in results]
    assert len({r.result['deck_id'] for r in results})==2
    async with contract.factory() as db:assert sorted((await db.scalars(select(Deck.card_count))).all())==[3,3]


@pytest.mark.parametrize('form',['nested','expanded'])
@pytest.mark.parametrize('user',['alice','bob'])
async def test_study_plan_actual_config_identity_and_schedule(contract,form,user):
    config={'user_id':user,'start_date':'2026-10-10','study_plan':{'type':'short','daily_hours':1.5,'ebbinghaus_enabled':False}}
    result=await StudyPlanAgent().run('summary','source',**({'config':config} if form=='nested' else config))
    assert result.status=='success',result.error
    async with contract.factory() as db:
        plan=await db.get(StudyPlan,result.result['plan_id'])
        assert plan.user_id==user
        assert plan.daily_hours==1.5 and plan.plan_type=='short'
        assert plan.ebbinghaus_nodes==[]
        assert plan.short_term_plan==result.result['short_term_plan']


async def test_study_plan_outer_identity_overrides_nested_and_default_is_legacy(contract):
    a=await StudyPlanAgent().run('summary','source',user_id='alice',config={'user_id':'bob'})
    b=await StudyPlanAgent().run('summary','source',config={})
    assert a.status==b.status=='success'
    async with contract.factory() as db:
        assert (await db.get(StudyPlan,a.result['plan_id'])).user_id=='alice'
        assert (await db.get(StudyPlan,b.result['plan_id'])).user_id=='local_user'
