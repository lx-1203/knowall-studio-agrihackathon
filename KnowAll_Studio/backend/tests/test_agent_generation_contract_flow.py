"""Actual Agent transactions and disposable SQLite; external AI explicitly simulated."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func, update, event

from app.database import Base
from app.models import (DocumentChunk, KnowledgePointNode, KnowledgeCoverage, QuestionBank,
                        Deck, Flashcard, ReviewSchedule, KnowledgeTree, StudyPack)
from app.core.agents.base import AgentRegistry
from app.core.agents.question_bank_agent import QuestionBankAgent
from app.core.agents.flashcard_agent import FlashcardAgent
from app.core.agents.orchestrator import orchestrator
from app.core.api_scheduler import get_current_user_id
from tests.test_study_notification_flow import flow
from tests.test_pipeline_lifecycle_flow import localhost


@pytest.fixture
async def contract(flow, monkeypatch):
    from app.api import agents
    from app.core.quiz import quiz_generator
    from app.core.quiz.relation_extractor import relation_extractor
    from app.core.memory import card_generator
    engine=flow.factory.kw['bind']
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[DocumentChunk.__table__,QuestionBank.__table__,
            Deck.__table__,Flashcard.__table__,ReviewSchedule.__table__,KnowledgeCoverage.__table__,
            KnowledgeTree.__table__,StudyPack.__table__])
    async with flow.factory() as db:
        db.add_all([KnowledgePointNode(id=f'node{i}',summary_id='summary',document_id='source',parent_id='topic',
            level=2,sequence=i,title=f'模拟知识{i}',explanation='模拟知识点解释内容') for i in range(4)])
        await db.commit()
    seen=[]
    async def quiz(**kwargs):
        seen.append({'kind':'question','user':get_current_user_id(),**kwargs})
        return [{'id':f'q_{i+1}', 'question_text':f'模拟题目{i+1}', 'answer':'模拟答案',
            'question_type':kwargs['question_type'],'cognitive_level':kwargs['cognitive_level'],
            'difficulty_score':kwargs['difficulty_score'],'difficulty':kwargs['difficulty'], 'tags':[]} for i in range(kwargs['count'])]
    async def cards(**kwargs):
        seen.append({'kind':'card','user':get_current_user_id(),**kwargs})
        return [{'front':f'模拟填空 {{{{c1::答案{i}}}}}' if kwargs['card_type']=='cloze' else f'模拟卡片正面{i}',
                 'back':'模拟卡片答案','card_type':kwargs['card_type'],'knowledge_point_id':'node0'} for i in range(kwargs['count'])]
    qmock=AsyncMock(side_effect=quiz);cmock=AsyncMock(side_effect=cards)
    monkeypatch.setattr(quiz_generator,'generate_questions',qmock)
    monkeypatch.setattr(card_generator,'generate',cmock)
    relations=AsyncMock(return_value=([],[]))
    monkeypatch.setattr(relation_extractor,'extract',relations)
    review=AsyncMock(side_effect=lambda cards,model,stats:cards)
    monkeypatch.setattr(FlashcardAgent,'_quality_review_sample',review)
    monkeypatch.setattr(orchestrator,'_generate_coverage_report',AsyncMock(return_value={'simulated':True}))
    monkeypatch.setattr(QuestionBankAgent,'_sample_cognitive_level',lambda self,distribution:'L1_remember')
    app=flow.client._transport.app
    app.include_router(agents.router)
    from app.api import questions
    app.include_router(questions.router)
    yield SimpleNamespace(**vars(flow),app=app,quiz=qmock,cards=cmock,relations=relations,review=review,seen=seen)


def options(kind,number):
    return {'model':'simulated/custom','question_count':number,'question_types':['short_answer'],
            'card_count':number,'card_types':['cloze'],'deck_name':'模拟临时牌组',
            'enable_review':False,'enable_kg_relations':False,'enable_quality_review':False}


async def size(contract,model):
    async with contract.factory() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def run(contract,kind,number=3,form='nested',**extra):
    config={**options(kind,number),**extra}
    agent=QuestionBankAgent() if kind=='question' else FlashcardAgent()
    result=await agent.run('summary','source',**({'config':config} if form=='nested' else config))
    return result


@pytest.mark.parametrize('kind,number',[('question',7),('card',13)])
@pytest.mark.parametrize('form',['nested','expanded'])
async def test_selected_parameters_budget_and_persistence(contract,kind,number,form):
    result=await run(contract,kind,number,form)
    assert result.status=='success',result.error
    count_key='total_questions' if kind=='question' else 'total_cards'
    assert result.result[count_key]==number
    assert await size(contract,QuestionBank if kind=='question' else Flashcard)==number
    assert await size(contract,KnowledgeCoverage)==number
    assert all(item['model']=='simulated/custom' for item in contract.seen)
    assert contract.review.await_count==contract.relations.await_count==0
    for item in contract.seen:
        assert item.get('question_type',item.get('card_type'))==('short_answer' if kind=='question' else 'cloze')
        if kind=='question':
            assert item['enable_review'] is False
    if kind=='card':
        assert await size(contract,ReviewSchedule)==number
        async with contract.factory() as db:
            deck=await db.get(Deck,result.result['deck_id'])
            assert deck.name=='模拟临时牌组' and deck.card_count==number


async def test_question_ids_are_real_unique_rows_across_repeated_runs(contract):
    first=await run(contract,'question',3)
    second=await run(contract,'question',3)
    assert first.status==second.status=='success'
    ids=first.result['question_ids']+second.result['question_ids']
    assert len(set(ids))==6
    async with contract.factory() as db:
        rows=(await db.scalars(select(QuestionBank).where(QuestionBank.id.in_(ids)))).all()
        coverage=(await db.scalars(select(KnowledgeCoverage))).all()
    assert len(rows)==6 and len(coverage)==6
    assert {row.resource_id for row in coverage}==set(ids)
    assert all(row.correct_answer=='模拟答案' for row in rows)


async def test_single_card_type_is_honored_even_without_level_weight(contract):
    async with contract.factory() as db:
        await db.execute(update(KnowledgePointNode).values(level=1))
        await db.commit()
    config=options('card',3)
    config.pop('card_types')
    result=await FlashcardAgent().run('summary','source',**config,card_type='cloze')
    assert result.status=='success',result.error
    assert result.result['total_cards']==3
    assert result.result['card_types']=={'cloze':3}


@pytest.mark.parametrize('kind',['question','card'])
@pytest.mark.parametrize('failure',['empty','raise'])
async def test_zero_output_is_error_and_leaves_no_fake_success(contract,kind,failure):
    provider=contract.quiz if kind=='question' else contract.cards
    provider.side_effect=RuntimeError('simulated provider failure') if failure=='raise' else None
    provider.return_value=[]
    result=await run(contract,kind,3)
    assert result.status=='error'
    assert result.error
    for model in [QuestionBank,Flashcard,KnowledgeCoverage,ReviewSchedule,Deck]:
        assert await size(contract,model)==0


async def test_all_cards_filtered_is_error_with_no_empty_deck(contract):
    contract.cards.side_effect=None
    contract.cards.return_value=[{'front':'x','back':'bad'}]
    result=await run(contract,'card',3)
    assert result.status=='error'
    assert await size(contract,Deck)==await size(contract,Flashcard)==0


async def test_cards_without_model_supplied_ids_link_to_actual_assignment_nodes(contract):
    original=contract.cards.side_effect
    async def without_ids(**kwargs):
        cards=await original(**kwargs)
        return [{key:value for key,value in card.items() if key!='knowledge_point_id'} for card in cards]
    contract.cards.side_effect=without_ids
    result=await run(contract,'card',3)
    assert result.status=='success'
    assert result.result['coverage']['covered_kp']>=1
    assert await size(contract,KnowledgeCoverage)==3
    async with contract.factory() as db:
        rows=(await db.scalars(select(Flashcard))).all()
        assert all(row.knowledge_point_id in {'node0','node1','node2','node3'} for row in rows)


@pytest.mark.parametrize('groups,number',[(1,13),(1,31),(4,25),(4,3),(4,0),(0,5)])
def test_card_batch_assignments_use_complete_finite_budget(groups,number):
    topics={f't{i}':{'nodes':[{'level':1}], 'text':f'模拟{i}'} for i in range(groups)}
    assignments=FlashcardAgent()._assign_card_types(topics,number,['cloze'])
    assert sum(a['count'] for a in assignments)==(number if groups else 0)
    assert all(0<a['count']<=10 and a['card_type']=='cloze' for a in assignments)


@pytest.mark.parametrize('kind',['question','card'])
@pytest.mark.parametrize('mode',['short','extra'])
async def test_provider_short_and_extra_output_reports_actual_bounded_rows(contract,kind,mode):
    provider=contract.quiz if kind=='question' else contract.cards
    original=provider.side_effect
    async def changed(**kwargs):
        if mode=='extra':
            return await original(**{**kwargs,'count':kwargs['count']+2})
        return (await original(**kwargs))[:1]
    provider.side_effect=changed
    result=await run(contract,kind,7 if kind=='question' else 13)
    assert result.status=='success',result.error
    actual=result.result['total_questions' if kind=='question' else 'total_cards']
    assert actual==(7 if kind=='question' else 13) if mode=='extra' else actual==(3 if kind=='question' else 2)
    assert await size(contract,QuestionBank if kind=='question' else Flashcard)==actual
    assert await size(contract,KnowledgeCoverage)==actual
    assert provider.await_count==(3 if kind=='question' else 2), 'short output must not cause unbounded retries'


async def test_malformed_question_batch_does_not_commit_unreported_prefix(contract):
    original=contract.quiz.side_effect
    calls=0
    async def malformed(**kwargs):
        nonlocal calls
        calls+=1
        output=await original(**kwargs)
        return [output[0],None] if calls==1 else output
    contract.quiz.side_effect=malformed
    result=await run(contract,'question',7)
    assert result.status=='success'
    assert result.result['total_questions']==4
    assert await size(contract,QuestionBank)==4, 'invalid batch left an unreported staged question'
    assert await size(contract,KnowledgeCoverage)==4


@pytest.mark.parametrize('kind,table',[('question','knowledge_coverage'),('card','review_schedule')])
async def test_transaction_failure_rolls_back_content_and_mapping_then_retries(contract,kind,table):
    engine=contract.factory.kw['bind'].sync_engine
    def failure(conn,cursor,statement,params,context,many):
        if statement.lower().startswith('insert into '+table):
            raise RuntimeError('simulated transaction write failure')
    event.listen(engine,'before_cursor_execute',failure)
    try:
        result=await run(contract,kind,3)
        assert result.status=='error' and 'simulated transaction write failure' in result.error
    finally:
        event.remove(engine,'before_cursor_execute',failure)
    for model in [QuestionBank,Flashcard,KnowledgeCoverage,ReviewSchedule,Deck]:
        assert await size(contract,model)==0
    recovered=await run(contract,kind,3)
    assert recovered.status=='success'
    assert await size(contract,QuestionBank if kind=='question' else Flashcard)==3


async def test_cross_topic_budget_persists_one_question_and_both_nodes(contract,monkeypatch):
    from app.core.quiz import quiz_generator
    from app.core.quiz.relation_extractor import relation_extractor
    monkeypatch.setattr(relation_extractor,'get_cross_topic_pairs',lambda relations:[{'source':'模拟知识0','target':'模拟知识1','type':'related_to'}])
    cross=AsyncMock(return_value=[{'id':'cross_1','question_text':'模拟跨知识题','answer':'跨题答案','question_type':'short_answer'}]*3)
    monkeypatch.setattr(quiz_generator,'generate_cross_topic',cross)
    result=await run(contract,'question',5,enable_kg_relations=True)
    assert result.status=='success',result.error
    assert result.result['total_questions']==5
    assert await size(contract,QuestionBank)==5
    assert cross.await_count==1
    async with contract.factory() as db:
        secondary=(await db.scalars(select(KnowledgeCoverage).where(KnowledgeCoverage.is_primary==False))).all()
        assert len(secondary)==1
        primary=(await db.scalars(select(KnowledgeCoverage).where(KnowledgeCoverage.resource_id==secondary[0].resource_id))).all()
        assert {p.knowledge_point_id for p in primary}=={'node0','node1'}
        assert await db.get(QuestionBank,secondary[0].resource_id)


@pytest.mark.parametrize('kind',['question','card'])
async def test_missing_summary_never_generates_or_writes(contract,kind):
    agent=QuestionBankAgent() if kind=='question' else FlashcardAgent()
    result=await agent.run('missing','source',**options(kind,3))
    assert result.status=='error'
    assert contract.quiz.await_count==contract.cards.await_count==0
    assert await size(contract,Deck)==await size(contract,QuestionBank)==0


@pytest.mark.parametrize('stream',[False,True])
async def test_real_http_configuration_durable_readback_error_and_recovery(contract,monkeypatch,stream,tmp_path):
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:[QuestionBankAgent(),FlashcardAgent()])
    payload={'summary_id':'summary','document_id':'source','agents':['question_bank','flashcard'],
        'config':{**options('question',7),'card_count':13,'user_id':'forged-owner'}}
    samples=[]
    async def call(client,headers):
        response=await client.post('/api/v1/agents/orchestrate'+('/stream' if stream else ''),headers=headers,json=payload)
        assert response.status_code==200,response.text
        if stream:
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            result=events[-1]
            samples.append({'events':events})
        else:
            result=response.json();samples.append(result)
        return result['results']
    async with localhost(contract.app) as client:
        first=await call(client,contract.alice)
        assert first['question_bank']['result']['total_questions']==7
        assert first['flashcard']['result']['total_cards']==13
        rows=(await client.get('/api/v1/quiz/questions',headers=contract.alice)).json()
        assert len(rows)==7 and all(row['knowledge_points'] for row in rows)
        assert {r['id'] for r in rows}==set(first['question_bank']['result']['question_ids'])
        assert all(item['user']=='alice' and item['model']=='simulated/custom' for item in contract.seen)
        original=contract.quiz.side_effect
        contract.quiz.side_effect=RuntimeError('simulated network question failure')
        failure=await call(client,contract.alice)
        assert failure['question_bank']['status']=='error' and failure['flashcard']['status']=='success'
        contract.quiz.side_effect=original
        contract.seen.clear()
        last=await call(client,contract.bob)
        assert last['question_bank']['status']==last['flashcard']['status']=='success'
        assert all(item['user']=='bob' for item in contract.seen)
        assert await size(contract,QuestionBank)==14
        assert await size(contract,Flashcard)==39
        assert await size(contract,ReviewSchedule)==39
    override=os.environ.get('KNOWALL_AGENT_EVIDENCE_DIR')
    directory=Path(override) if override else tmp_path/'http-evidence'
    directory.mkdir(parents=True,exist_ok=True)
    output=directory/f'{"contract" if override else "real"}-http-{"sse" if stream else "rest"}.json'
    output.write_text(json.dumps({'transport':'real localhost HTTP/SSE','database':'disposable SQLite',
        'AI':'explicitly simulated quiz/card/quality/coverage','samples':samples,'question_rows':14,'card_rows':39},ensure_ascii=False,indent=2),encoding='utf-8')
