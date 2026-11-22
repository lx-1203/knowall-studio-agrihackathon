"""Actual API/Agents and disposable SQLite; card generator explicitly simulated."""
import asyncio
from types import SimpleNamespace
import pytest
from sqlalchemy import select,update,event
from sqlalchemy.ext.asyncio import AsyncSession
from app.api import flashcards
from app.models import Deck,Flashcard,ReviewSchedule,StudyPack,StudyPlan
from tests.test_agent_generation_contract_flow import contract,flow,size,options
from tests.test_deck_generation_concurrency_flow import deck_barrier
from app.core.agents.flashcard_agent import FlashcardAgent


@pytest.fixture
async def direct(contract):
    contract.app.include_router(flashcards.router)
    async def simulated(knowledge_text,card_type,count,model):
        return [{'front':f'明确模拟 {{{{c1::答案{i}}}}}' if card_type=='cloze' else f'明确模拟问题{i}',
                 'back':'明确模拟答案','card_type':card_type,'tags':[]} for i in range(count)]
    contract.cards.side_effect=simulated
    yield SimpleNamespace(**vars(contract),simulated=simulated)


def payload(**kwargs):return {'knowledge_text':'明确模拟知识点','count':3,'deck_name':'shared-name','card_type':'qa','model':'explicit-simulation',**kwargs}


async def post(direct,**kwargs):
    return await direct.client.post('/api/v1/flashcards/generate',headers=direct.alice,json=payload(**kwargs))


@pytest.mark.parametrize('fields',[{'knowledge_text':'  '},{'count':0},{'count':-1},{'count':1.5},{'count':True},{'card_type':'not-a-type'},{'deck_name':' '},{'model':' '}])
async def test_invalid_direct_input_rejected_before_ai_or_business_writes(direct,fields):
    response=await post(direct,**fields)
    assert response.status_code==422,response.text
    assert direct.cards.await_count==0
    assert await size(direct,Deck)==0


@pytest.mark.parametrize('type',['qa','cloze','compare'])
async def test_selected_type_model_count_and_real_fsrs_rows(direct,type):
    response=await post(direct,card_type=type)
    assert response.status_code==200,response.text
    data=response.json();assert data['generated_count']==3
    assert direct.cards.call_args.args==('明确模拟知识点',type,3,'explicit-simulation')
    async with direct.factory() as db:
        cards=(await db.scalars(select(Flashcard))).all()
        assert (await db.get(Deck,data['deck_id'])).card_count==3
    assert len(cards)==await size(direct,ReviewSchedule)==3
    assert {c.card_type for c in cards}=={type}
    assert len({c.id for c in cards})==3


@pytest.mark.parametrize('failure',['raise','empty','bad-second','bad-tags'])
async def test_invalid_provider_batch_does_not_leave_empty_deck_or_partial_cards(direct,failure):
    if failure=='raise':direct.cards.side_effect=RuntimeError('explicit simulated AI failure')
    else:
        direct.cards.side_effect=None
        good=(await direct.simulated('', 'qa',1,''))[0]
        direct.cards.return_value={'empty':[], 'bad-second':[good,{'front':{},'back':'bad'}], 'bad-tags':[{**good,'tags':'invalid'}]}[failure]
    response=await post(direct)
    assert response.status_code==500,response.text
    for model in (Deck,Flashcard,ReviewSchedule):assert await size(direct,model)==0
    direct.cards.side_effect=direct.simulated
    assert (await post(direct)).status_code==200


async def test_provider_extra_cards_are_capped_at_requested_budget(direct):
    direct.cards.side_effect=None;direct.cards.return_value=await direct.simulated('','qa',5,'')
    response=await post(direct,count=2)
    assert response.status_code==200 and response.json()['generated_count']==2,response.text
    assert await size(direct,Flashcard)==2


@pytest.mark.parametrize('existing',[False,True],ids=['first-deck','existing-deck'])
async def test_actual_direct_concurrent_deck_and_counts(direct,monkeypatch,existing):
    if existing:
        async with direct.factory() as db:db.add(Deck(id='existing',name='shared-name',card_count=7));await db.commit()
    arrivals=deck_barrier(monkeypatch)
    responses=await asyncio.gather(post(direct),post(direct))
    assert all(r.status_code==200 for r in responses),[r.text for r in responses]
    assert len(set(arrivals))==2
    ids={r.json()['deck_id'] for r in responses};assert len(ids)==1
    assert await size(direct,Deck)==1
    async with direct.factory() as db:assert (await db.get(Deck,next(iter(ids)))).card_count==(7 if existing else 0)+6
    assert await size(direct,Flashcard)==await size(direct,ReviewSchedule)==6


async def test_direct_and_agent_first_same_name_converge(direct,monkeypatch):
    arrivals=deck_barrier(monkeypatch)
    response,result=await asyncio.gather(post(direct),FlashcardAgent().run('summary','source',**{**options('card',3),'card_types':['qa'],'deck_name':'shared-name','user_id':'alice'}))
    assert response.status_code==200 and result.status=='success',(response.text,result.error)
    assert len(set(arrivals))==2
    assert response.json()['deck_id']==result.result['deck_id']
    assert await size(direct,Deck)==1
    async with direct.factory() as db:assert (await db.get(Deck,result.result['deck_id'])).card_count==6


async def test_private_learning_pack_is_not_selected_by_direct_name(direct):
    async with direct.factory() as db:
        db.add(Deck(id='private',name='shared-name'));await db.flush()
        db.add(StudyPack(id='private-pack',user_id='bob',document_id='source',deck_id='private',title='Bob',source_hash='temporary'));await db.commit()
    response=await post(direct);assert response.status_code==200
    assert response.json()['deck_id']!='private'
    async with direct.factory() as db:assert (await db.get(Deck,'private')).card_count==0


@pytest.mark.parametrize('action',['failure','cancel'])
async def test_direct_count_failure_or_cancel_leaves_no_deck_and_can_retry(direct,monkeypatch,action):
    engine=direct.factory.kw['bind'];entered=asyncio.Event();original=direct.cards.side_effect
    def fail(conn,cursor,sql,params,ctx,many):
        if sql.startswith('UPDATE decks'):raise RuntimeError('explicit simulated count write failure')
    async def blocked(*args,**kwargs):
        entered.set();await asyncio.Event().wait()
    if action=='failure':event.listen(engine.sync_engine,'before_cursor_execute',fail)
    else:direct.cards.side_effect=blocked
    try:
        if action=='failure':
            response=await post(direct);assert response.status_code==500
        else:
            task=asyncio.create_task(post(direct))
            try:
                await asyncio.wait_for(entered.wait(),3);task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
            finally:
                if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
    finally:
        if action=='failure':event.remove(engine.sync_engine,'before_cursor_execute',fail)
        else:direct.cards.side_effect=original
    for model in (Deck,Flashcard,ReviewSchedule):assert await size(direct,model)==0
    assert (await post(direct)).status_code==200


async def test_direct_provider_actual_independent_write_has_no_lock(direct):
    async def simulated(*args):
        async with direct.factory() as db:
            await db.execute(update(StudyPlan).values(name='explicit simulated provider write'));await db.commit()
        return await direct.simulated(*args)
    direct.cards.side_effect=simulated
    assert (await post(direct)).status_code==200


@pytest.mark.parametrize('field,value',[('tags',False),('tags',0),('hints',False),('hints',0)])
async def test_false_optional_provider_fields_are_rejected_atomically(direct,field,value):
    good=(await direct.simulated('','qa',1,''))[0]
    direct.cards.side_effect=None;direct.cards.return_value=[{**good,field:value}]
    response=await post(direct)
    assert response.status_code==500,response.text
    for model in (Deck,Flashcard,ReviewSchedule):assert await size(direct,model)==0


@pytest.mark.parametrize('count',[101,'3'])
async def test_strict_budget_bounds_prevent_generation(direct,count):
    assert (await post(direct,count=count)).status_code==422
    assert direct.cards.await_count==0


async def test_optional_none_and_structured_prompt_tags_remain_supported(direct):
    good=(await direct.simulated('','qa',1,''))[0]
    direct.cards.side_effect=None
    direct.cards.return_value=[{**good,'hints':None,'tags':None},
        {**good,'hints':'提示','tags':{'domain_tags':['学科'],'memory_tags':['需记忆']}}]
    response=await post(direct);assert response.status_code==200,response.text
    async with direct.factory() as db:
        cards=(await db.scalars(select(Flashcard).order_by(Flashcard.created_at))).all()
    assert len(cards)==2
    assert cards[0].hints=='' and cards[0].tags==[]
    assert cards[1].tags=={'domain_tags':['学科'],'memory_tags':['需记忆']}


@pytest.mark.parametrize('existing',[False,True])
@pytest.mark.parametrize('action',['failure','cancel'])
async def test_late_count_write_failure_cancel_preserves_previous_state(direct,monkeypatch,existing,action):
    if existing:
        async with direct.factory() as db:
            db.add(Deck(id='existing',name='shared-name',card_count=7));await db.commit()
    original=AsyncSession.execute;entered=asyncio.Event()
    async def blocked(db,statement,*args,**kwargs):
        if str(statement).startswith('UPDATE decks'):
            entered.set()
            if action=='failure':raise RuntimeError('explicit late count failure')
            await asyncio.Event().wait()
        return await original(db,statement,*args,**kwargs)
    monkeypatch.setattr(AsyncSession,'execute',blocked)
    try:
        if action=='failure':assert (await post(direct)).status_code==500
        else:
            task=asyncio.create_task(post(direct))
            try:
                await asyncio.wait_for(entered.wait(),3);task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
            finally:
                if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
    finally:monkeypatch.setattr(AsyncSession,'execute',original)
    assert entered.is_set()
    assert await size(direct,Deck)==int(existing)
    assert await size(direct,Flashcard)==await size(direct,ReviewSchedule)==0
    if existing:
        async with direct.factory() as db:assert (await db.get(Deck,'existing')).card_count==7
    response=await post(direct);assert response.status_code==200,response.text
    async with direct.factory() as db:assert (await db.get(Deck,response.json()['deck_id'])).card_count==(10 if existing else 3)
