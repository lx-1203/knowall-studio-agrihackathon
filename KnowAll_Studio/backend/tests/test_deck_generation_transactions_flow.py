"""Observe real write rollback and concurrent staged historical restoration."""
import asyncio
import pytest
from sqlalchemy import event,select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Deck,Flashcard,ReviewSchedule,KnowledgeCoverage,KnowledgeSummary
from tests.test_agent_generation_contract_flow import contract,flow,size,options
from tests.test_historical_summary_generation_flow import historical,nodes,MD
from tests.test_deck_generation_concurrency_flow import generate
from app.core.agents.flashcard_agent import FlashcardAgent


@pytest.mark.parametrize('restored',[False,True],ids=['existing-nodes','historical-nodes'])
@pytest.mark.parametrize('existing',[False,True],ids=['new-deck','existing-deck'])
@pytest.mark.parametrize('action',['failure','cancel'])
async def test_count_write_failure_or_cancel_rolls_back_whole_generation(contract,monkeypatch,restored,existing,action):
    if restored:await historical(contract)
    if existing:
        async with contract.factory() as db:
            db.add(Deck(id='existing',name='concurrent',card_count=11));await db.commit()
    before={row.id for row in await nodes(contract)}
    engine=contract.factory.kw['bind'];entered=asyncio.Event()
    def fail(conn,cursor,sql,parameters,context,many):
        if sql.startswith('UPDATE decks'):raise RuntimeError('explicit simulated deck count storage failure')
    execute=AsyncSession.execute
    async def block(session,statement,*args,**kwargs):
        if str(statement).startswith('UPDATE decks'):
            entered.set();await asyncio.Event().wait()
        return await execute(session,statement,*args,**kwargs)
    if action=='failure':event.listen(engine.sync_engine,'before_cursor_execute',fail)
    else:monkeypatch.setattr(AsyncSession,'execute',block)
    try:
        if action=='cancel':
            task=asyncio.create_task(generate(contract));await asyncio.wait_for(entered.wait(),5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        else:
            result=await generate(contract);assert result.status=='error' and 'count storage failure' in result.error
    finally:
        if action=='failure':event.remove(engine.sync_engine,'before_cursor_execute',fail)
        else:monkeypatch.setattr(AsyncSession,'execute',execute)
    assert {row.id for row in await nodes(contract)}==before
    for model in (Flashcard,ReviewSchedule,KnowledgeCoverage):assert await size(contract,model)==0
    assert await size(contract,Deck)==int(existing)
    if existing:
        async with contract.factory() as db:assert (await db.get(Deck,'existing')).card_count==11
    result=await generate(contract);assert result.status=='success',result.error
    async with contract.factory() as db:assert (await db.get(Deck,result.result['deck_id'])).card_count==(11 if existing else 0)+3


@pytest.mark.parametrize('different_summary',[False,True],ids=['same-summary','different-summary'])
async def test_simultaneous_staged_historical_summaries_keep_real_provenance(contract,different_summary):
    await historical(contract)
    if different_summary:
        async with contract.factory() as db:
            db.add(KnowledgeSummary(id='summary2',document_id='source',content_md=MD));await db.commit()
    gate=asyncio.Event();arrivals=[];original=contract.cards.side_effect
    async def simulated_ai(**kwargs):
        arrivals.append(id(asyncio.current_task()))
        if len(arrivals)==2:gate.set()
        await asyncio.wait_for(gate.wait(),5)
        return await original(**kwargs)
    contract.cards.side_effect=simulated_ai
    results=await asyncio.gather(generate(contract),FlashcardAgent().run('summary2' if different_summary else 'summary','source',**{**options('card',3),'deck_name':'concurrent','user_id':'alice'}))
    assert len(set(arrivals))==2 and all(r.status=='success' for r in results),[r.error for r in results]
    assert len({r.result['deck_id'] for r in results})==1
    all_nodes=await nodes(contract)
    if different_summary:
        other=await nodes(contract,'summary2');assert len(other)==3
        assert {n.id for n in all_nodes}.isdisjoint({n.id for n in other})
        all_nodes+=other
    assert len(all_nodes)==(6 if different_summary else 3)
    async with contract.factory() as db:
        cards=(await db.scalars(select(Flashcard))).all()
        coverage=(await db.scalars(select(KnowledgeCoverage))).all()
        assert (await db.get(Deck,results[0].result['deck_id'])).card_count==6
    assert len(cards)==len(coverage)==6
    assert {c.knowledge_point_id for c in cards}<={n.id for n in all_nodes}
    assert {c.resource_id for c in coverage}=={c.id for c in cards}
