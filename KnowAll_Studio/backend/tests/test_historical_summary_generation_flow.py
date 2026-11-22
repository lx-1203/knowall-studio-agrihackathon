"""Real disposable SQL/FK/HTTP recovery; every external AI response is simulated."""
import asyncio
import json
import subprocess
import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete,select,update,event

from app.models import KnowledgePointNode,KnowledgeSummary,QuestionBank,Flashcard,KnowledgeCoverage,Deck,ReviewSchedule
from app.core.agents.base import AgentRegistry
from app.core.agents.question_bank_agent import QuestionBankAgent
from app.core.agents.flashcard_agent import FlashcardAgent
from tests.test_agent_generation_contract_flow import contract,flow,run,options,size
from tests.test_pipeline_lifecycle_flow import localhost

MD='# 历史总纲\n总纲说明\n## 模拟知识重点\n知识点解释与答案\n### 子知识\n子知识详细说明'
EVIDENCE=Path(os.environ.get('KNOWALL_HISTORICAL_EVIDENCE_DIR',str(Path(__file__).resolve().parents[2]/'docs/test-round-06b6')))


async def historical(contract,summary='summary',markdown=MD):
    async with contract.factory() as db:
        await db.execute(delete(KnowledgePointNode).where(KnowledgePointNode.summary_id==summary))
        await db.execute(update(KnowledgeSummary).where(KnowledgeSummary.id==summary).values(content_md=markdown,node_count=0,level_stats={}))
        await db.commit()


async def nodes(contract,summary='summary'):
    async with contract.factory() as db:
        return (await db.scalars(select(KnowledgePointNode).where(KnowledgePointNode.summary_id==summary))).all()


async def assert_durable(contract,kind,result,number=3,summary='summary'):
    assert result.status=='success',result.error
    rows=await nodes(contract,summary)
    assert rows and len(rows)==3
    ids={r.id for r in rows}
    assert len(ids)==3 and all(r.document_id=='source' for r in rows)
    assert all(r.parent_id is None or r.parent_id in ids for r in rows)
    async with contract.factory() as db:
        meta=await db.get(KnowledgeSummary,summary)
        assert meta.node_count==3 and meta.level_stats=={'L1':1,'L2':1,'L3':1}
        resources=result.result['question_ids' if kind=='question' else 'card_ids']
        stored=(await db.scalars(select(QuestionBank if kind=='question' else Flashcard).where(
            (QuestionBank.id if kind=='question' else Flashcard.id).in_(resources)))).all()
        coverage=(await db.scalars(select(KnowledgeCoverage).where(KnowledgeCoverage.resource_id.in_(resources)))).all()
    assert len(stored)==len(coverage)==number
    assert all(c.knowledge_point_id in ids for c in coverage)
    if kind=='card':
        assert await size(contract,ReviewSchedule)>=number
    return ids


@pytest.mark.parametrize('kind',['question','card'])
async def test_markdown_only_summary_restores_real_content_and_coverage(contract,kind):
    await historical(contract)
    await assert_durable(contract,kind,await run(contract,kind))


@pytest.mark.parametrize('kind',['question','card'])
async def test_repeated_generation_reuses_nodes_and_preserves_content(contract,kind):
    await historical(contract)
    first=await assert_durable(contract,kind,await run(contract,kind))
    second=await assert_durable(contract,kind,await run(contract,kind))
    assert first==second
    assert await size(contract,QuestionBank if kind=='question' else Flashcard)==6


@pytest.mark.parametrize('kind',['question','card'])
async def test_same_document_different_summaries_do_not_collide(contract,kind):
    await historical(contract)
    async with contract.factory() as db:
        db.add(KnowledgeSummary(id='summary2',document_id='source',content_md=MD))
        await db.commit()
    first=await assert_durable(contract,kind,await run(contract,kind))
    agent=QuestionBankAgent() if kind=='question' else FlashcardAgent()
    second=await assert_durable(contract,kind,await agent.run('summary2','source',**options(kind,3)),summary='summary2')
    assert first.isdisjoint(second)


@pytest.mark.parametrize('kind',['question','card'])
async def test_only_l1_summary_still_generates_and_links_real_node(contract,kind):
    await historical(contract,markdown='# 只有一章\n有实际内容和答案')
    result=await run(contract,kind)
    assert result.status=='success',result.error
    rows=await nodes(contract)
    assert len(rows)==1 and rows[0].level==1
    assert await size(contract,KnowledgeCoverage)==3


@pytest.mark.parametrize('kind',['question','card'])
async def test_existing_nodes_are_not_reparsed_or_overwritten(contract,kind):
    before={r.id:(r.title,r.explanation) for r in await nodes(contract)}
    result=await run(contract,kind)
    assert result.status=='success',result.error
    after={r.id:(r.title,r.explanation) for r in await nodes(contract)}
    assert after==before


@pytest.mark.parametrize('kind',['question','card'])
@pytest.mark.parametrize('failure',['empty','raise'])
async def test_model_failure_rolls_back_restored_nodes_then_retries(contract,kind,failure):
    await historical(contract)
    provider=contract.quiz if kind=='question' else contract.cards
    original=provider.side_effect
    provider.side_effect=RuntimeError('simulated failure') if failure=='raise' else None
    provider.return_value=[]
    result=await run(contract,kind)
    assert result.status=='error'
    assert not await nodes(contract)
    for model in [QuestionBank,Flashcard,KnowledgeCoverage,Deck,ReviewSchedule]:
        assert await size(contract,model)==0
    provider.side_effect=original
    await assert_durable(contract,kind,await run(contract,kind))


@pytest.mark.parametrize('kind',['question','card'])
async def test_storage_failure_rolls_back_entire_recovery_transaction(contract,kind):
    await historical(contract)
    engine=contract.factory.kw['bind']
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO knowledge_coverage'):
            raise RuntimeError('simulated coverage storage failure')
    event.listen(engine.sync_engine,'before_cursor_execute',fail)
    try:
        result=await run(contract,kind)
    finally:
        event.remove(engine.sync_engine,'before_cursor_execute',fail)
    assert result.status=='error'
    assert not await nodes(contract)
    assert await size(contract,QuestionBank)==await size(contract,Flashcard)==await size(contract,Deck)==0
    await assert_durable(contract,kind,await run(contract,kind))


async def test_null_optional_question_containers_normalize_and_keep_coverage(contract):
    original=contract.quiz.side_effect
    async def nullable(**kwargs):
        return [{**q,'tags':None,'options':None,'review_scores':None} for q in await original(**kwargs)]
    contract.quiz.side_effect=nullable
    result=await run(contract,'question')
    assert result.status=='success',result.error
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==3
    async with contract.factory() as db:
        rows=(await db.scalars(select(QuestionBank))).all()
        assert all(r.tags==[] and r.options==[] and r.review_scores=={} for r in rows)


@pytest.mark.parametrize('field,value',[('question_text',None),('question_text',''),('question_text','  '),
    ('answer',None),('answer',''),('answer','  '),('tags',1),('options','bad'),('review_scores',[])])
async def test_invalid_question_fields_cannot_produce_fake_success(contract,field,value):
    original=contract.quiz.side_effect
    async def invalid(**kwargs):
        return [{**q,field:value} for q in await original(**kwargs)]
    contract.quiz.side_effect=invalid
    result=await run(contract,'question')
    assert result.status=='error',result
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==0


async def test_mixed_question_batch_has_no_partially_saved_prefix(contract):
    original=contract.quiz.side_effect
    async def mixed(**kwargs):
        qs=await original(**kwargs)
        qs[-1]['answer']=None
        return qs
    contract.quiz.side_effect=mixed
    result=await run(contract,'question')
    assert result.status=='error'
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==0


async def test_concurrent_first_restore_two_agents_has_single_durable_node_set(contract):
    await historical(contract)
    results=await asyncio.gather(run(contract,'question'),run(contract,'card'))
    for kind,result in zip(['question','card'],results):
        await assert_durable(contract,kind,result)
    assert await size(contract,KnowledgeCoverage)==6


@pytest.mark.parametrize('stream',[False,True])
async def test_real_http_historical_restore_then_failure_and_recovery(contract,monkeypatch,stream):
    await historical(contract)
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:[QuestionBankAgent(),FlashcardAgent()])
    config=options('question',3)
    payload={'summary_id':'summary','document_id':'source','agents':['question_bank','flashcard'],'config':config}
    url='/api/v1/agents/orchestrate'+('/stream' if stream else '')
    evidence=[]
    async with localhost(contract.app) as client:
        for phase in ['normal','failure','recovery']:
            original=contract.quiz.side_effect
            if phase=='failure':
                contract.quiz.side_effect=RuntimeError('simulated HTTP provider failure')
            response=await client.post(url,json=payload,headers=contract.alice)
            assert response.status_code==200,response.text
            if stream:
                events=[json.loads(part.split('data: ',1)[1].strip()) for part in response.text.split('\n\n') if 'data: ' in part]
                data=next(e for e in events if 'results' in e)
            else:
                data=response.json()
            evidence.append({'phase':phase,'results':data['results']})
            assert data['results']['flashcard']['status']=='success',data
            assert data['results']['question_bank']['status']==('error' if phase=='failure' else 'success'),data
            assert len(await nodes(contract))==3
            contract.quiz.side_effect=original
        response=await client.get('/api/v1/quiz/questions',headers=contract.alice)
        assert response.status_code==200 and len(response.json())==6,response.text
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    (EVIDENCE/('real-http-sse.json' if stream else 'real-http-rest.json')).write_text(json.dumps({
        'external_ai':'simulated','phases':evidence,'nodes':3,'questions':await size(contract,QuestionBank),
        'cards':await size(contract,Flashcard)},ensure_ascii=False,indent=2),encoding='utf-8')


async def test_restored_nodes_and_coverage_survive_independent_process(contract):
    await historical(contract)
    await assert_durable(contract,'question',await run(contract,'question'))
    database=str(contract.factory.kw['bind'].url.database)
    script="import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); print(json.dumps({t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['knowledge_point_nodes','question_bank','knowledge_coverage']}))"
    completed=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',script,database],capture_output=True,text=True,timeout=15)
    assert completed.returncode==0,completed.stderr
    assert json.loads(completed.stdout)=={'knowledge_point_nodes':3,'question_bank':3,'knowledge_coverage':3}


@pytest.mark.parametrize('kind',['question','card'])
async def test_markdown_without_headings_is_error_with_no_restoration(contract,kind):
    await historical(contract,markdown='只有无标题的正文，不存在可提取知识点。')
    result=await run(contract,kind)
    assert result.status=='error'
    assert not await nodes(contract)
    assert await size(contract,QuestionBank)==await size(contract,Flashcard)==await size(contract,Deck)==0
    assert contract.quiz.await_count==contract.cards.await_count==0


@pytest.mark.parametrize('kind',['question','card'])
async def test_existing_l1_nodes_are_reused_without_markdown_replacement(contract,kind):
    async with contract.factory() as db:
        await db.execute(update(KnowledgePointNode).values(level=1))
        await db.commit()
    before={r.id for r in await nodes(contract)}
    result=await run(contract,kind)
    assert result.status=='success',result.error
    assert {r.id for r in await nodes(contract)}==before
    assert await size(contract,KnowledgeCoverage)==3


@pytest.mark.parametrize('kind',['question','card'])
async def test_restoration_uses_actual_summary_source_over_stale_caller_id(contract,kind):
    await historical(contract)
    agent=QuestionBankAgent() if kind=='question' else FlashcardAgent()
    result=await agent.run('summary','nonexistent-stale-document',**options(kind,3))
    await assert_durable(contract,kind,result)


@pytest.mark.parametrize('answer',[0,False,['A','C'],'B'])
async def test_correct_answer_and_type_alias_preserve_nonempty_legacy_values(contract,answer):
    contract.quiz.side_effect=None
    contract.quiz.return_value=[{'id':'model-placeholder','question_text':'真实别名模拟题目',
        'correct_answer':answer,'type':'short_answer','tags':None,'options':None,'review_scores':None}]
    result=await run(contract,'question')
    assert result.status=='success',result.error
    async with contract.factory() as db:
        row=await db.get(QuestionBank,result.result['question_ids'][0])
        assert row.correct_answer==str(answer) and row.question_type=='short_answer'
    assert await size(contract,KnowledgeCoverage)==1


async def test_cross_topic_invalid_batch_does_not_leave_content_or_secondary_coverage(contract,monkeypatch):
    from app.core.quiz import quiz_generator
    from app.core.quiz.relation_extractor import relation_extractor
    monkeypatch.setattr(relation_extractor,'get_cross_topic_pairs',lambda relations:[{
        'source':'模拟知识0','target':'模拟知识1','type':'related_to'}])
    cross=AsyncMock(return_value=[{'question_text':'有效跨题正文','answer':'答案'},
        {'question_text':'坏跨题正文','answer':None}])
    monkeypatch.setattr(quiz_generator,'generate_cross_topic',cross)
    result=await run(contract,'question',5,enable_kg_relations=True)
    assert result.status=='success' and result.result['total_questions']==4,result
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==4
    assert cross.await_count==1
    async with contract.factory() as db:
        assert not (await db.scalars(select(KnowledgeCoverage).where(KnowledgeCoverage.is_primary.is_(False)))).all()


async def test_forced_simultaneous_first_reads_recover_actual_unique_conflict(contract,monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    await historical(contract)
    original_execute=AsyncSession.execute
    original_rollback=AsyncSession.rollback
    gate=asyncio.Event()
    arrivals=[]
    rollbacks=[]
    async def synchronized_read(session,statement,*args,**kwargs):
        result=await original_execute(session,statement,*args,**kwargs)
        sql=str(statement)
        if sql.startswith('SELECT knowledge_point_nodes.') and len(arrivals)<2:
            arrivals.append(id(session))
            if len(arrivals)==2:
                gate.set()
            await asyncio.wait_for(gate.wait(),5)
        return result
    async def observed_rollback(session):
        rollbacks.append(id(session))
        return await original_rollback(session)
    monkeypatch.setattr(AsyncSession,'execute',synchronized_read)
    monkeypatch.setattr(AsyncSession,'rollback',observed_rollback)
    results=await asyncio.gather(run(contract,'question'),run(contract,'card'))
    assert len(set(arrivals))==2 and len(rollbacks)==1
    for kind,result in zip(['question','card'],results):
        await assert_durable(contract,kind,result)
    assert await size(contract,KnowledgeCoverage)==6


async def test_failed_first_generator_does_not_block_other_agent_restoration(contract):
    await historical(contract)
    contract.quiz.side_effect=RuntimeError('simulated first generator failure')
    question,card=await asyncio.gather(run(contract,'question'),run(contract,'card'))
    assert question.status=='error'
    await assert_durable(contract,'card',card)
    assert await size(contract,QuestionBank)==0 and await size(contract,KnowledgeCoverage)==3


async def test_cancelled_first_generator_rolls_back_restoration_and_can_retry(contract):
    await historical(contract)
    original=contract.quiz.side_effect
    entered=asyncio.Event()
    blocker=asyncio.Event()
    async def blocked(**kwargs):
        entered.set()
        await blocker.wait()
    contract.quiz.side_effect=blocked
    task=asyncio.create_task(run(contract,'question'))
    try:
        await asyncio.wait_for(entered.wait(),5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    assert not await nodes(contract)
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==0
    contract.quiz.side_effect=original
    await assert_durable(contract,'question',await run(contract,'question'))
