"""Actual question Agent/SQLite; model callbacks write to independent DB session."""
import asyncio
import pytest
from sqlalchemy import update,select,event
from app.models import StudyPlan,QuestionBank,KnowledgeCoverage,KnowledgePointNode
from app.core.agents.question_bank_agent import QuestionBankAgent
from tests.test_agent_generation_contract_flow import contract,flow,options,size
from tests.test_historical_summary_generation_flow import historical,nodes,MD


async def run_question(contract,**kwargs):
    return await QuestionBankAgent().run('summary','source',**{**options('question',8),**kwargs})


@pytest.mark.parametrize('history',[False,True],ids=['existing','historical'])
async def test_all_question_batches_allow_actual_independent_usage_write(contract,history):
    if history:await historical(contract)
    original=contract.quiz.side_effect;calls=[]
    async def simulated(**kwargs):
        async with contract.factory() as db:
            await db.execute(update(StudyPlan).values(name='explicit simulated provider usage'));await db.commit()
        calls.append(True)
        return await original(**kwargs)
    contract.quiz.side_effect=simulated
    result=await run_question(contract)
    assert result.status=='success',result.error
    assert result.result['total_questions']==8
    assert len(calls)>=3
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==8
    assert len(await nodes(contract))==(3 if history else 4)


async def test_relations_and_cross_topic_usage_writes_precede_business_persistence(contract,monkeypatch):
    from app.core.quiz import quiz_generator
    from app.core.quiz.relation_extractor import relation_extractor
    await historical(contract,markdown='# 主章\n主章解释\n## 节1\n解释一\n### 点1\n解释1\n## 节2\n解释二\n### 点2\n解释2')
    trace=[];ordinary=contract.quiz.side_effect
    async def usage(kind):
        async with contract.factory() as db:
            await db.execute(update(StudyPlan).values(name='explicit simulation '+kind));await db.commit()
        trace.append(kind)
    async def relation(*args,**kwargs):await usage('relation');return ([],[])
    contract.relations.side_effect=relation
    monkeypatch.setattr(relation_extractor,'get_cross_topic_pairs',lambda relations:[{'source':'节1','target':'节2','type':'related_to'}])
    async def normal(**kwargs):await usage('normal');return await ordinary(**kwargs)
    contract.quiz.side_effect=normal
    async def cross(*args,**kwargs):
        await usage('cross')
        return [{'question_text':'明确模拟跨主题题','answer':'明确模拟答案','question_type':'short_answer'}]
    monkeypatch.setattr(quiz_generator,'generate_cross_topic',cross)
    result=await run_question(contract,enable_kg_relations=True)
    assert result.status=='success',result.error
    assert result.result['total_questions']==8
    assert trace[0]=='relation' and trace[-1]=='cross',trace
    async with contract.factory() as db:
        questions=(await db.scalars(select(QuestionBank))).all();coverage=(await db.scalars(select(KnowledgeCoverage))).all()
    assert len(questions)==8 and len(coverage)==9
    assert {c.resource_id for c in coverage}=={q.id for q in questions}
    assert sum(not c.is_primary for c in coverage)==1


@pytest.mark.parametrize('action',['cancel','storage'])
async def test_question_late_failure_or_cancel_rolls_back_nodes_content_coverage(contract,monkeypatch,action):
    await historical(contract)
    entered=asyncio.Event();engine=contract.factory.kw['bind'];original=contract.quiz.side_effect
    async def blocked(**kwargs):entered.set();await asyncio.Event().wait()
    def fail(conn,cursor,sql,params,ctx,many):
        if sql.startswith('INSERT INTO knowledge_coverage'):raise RuntimeError('explicit simulated coverage write failure')
    if action=='cancel':contract.quiz.side_effect=blocked
    else:event.listen(engine.sync_engine,'before_cursor_execute',fail)
    try:
        if action=='cancel':
            task=asyncio.create_task(run_question(contract));await asyncio.wait_for(entered.wait(),3);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        else:
            result=await run_question(contract);assert result.status=='error'
    finally:
        if action=='storage':event.remove(engine.sync_engine,'before_cursor_execute',fail)
        contract.quiz.side_effect=original
    assert not await nodes(contract)
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==0
    assert (await run_question(contract)).status=='success'


async def test_partial_valid_question_batches_keep_original_degradation(contract):
    original=contract.quiz.side_effect;calls=[]
    async def partial(**kwargs):
        calls.append(True)
        if len(calls)>1:raise RuntimeError('explicit simulated later batch failure')
        return await original(**kwargs)
    contract.quiz.side_effect=partial
    result=await run_question(contract)
    assert result.status=='success',result.error
    assert result.result['total_questions']==3
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==3
