"""Real Agent/SQLite/quiz contracts; external generation explicitly simulated."""
import copy
import hashlib
import json
import os
import socket
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

from app.core.agents.question_bank_agent import QuestionBankAgent
from app.core.quiz import QuizGenerator, exam_engine
from app.database import Base
from app.models import QuestionBank, KnowledgeCoverage, AnswerRecord, ExamPaper, ErrorLog
from tests.test_agent_generation_contract_flow import contract, flow, options, size
from tests.test_pipeline_lifecycle_flow import localhost

BLANKS=[{'index':1,'answer':'氢','hint':'第一元素'}, {'index':2,'answer':'氧','hint':'第二元素'}]
POINTS=[{'point':'可靠传输','keywords':['reliable'],'score':3}, {'point':'流量控制','keywords':['flow'],'score':1}]


@pytest.fixture(autouse=True)
async def exam_tables(contract):
    # The reused Agent fixture only creates generation tables; native quiz
    # grading also needs its paper, answer and wrong-answer tables.
    async with contract.factory.kw['bind'].begin() as connection:
        await connection.run_sync(Base.metadata.create_all,tables=[ExamPaper.__table__,AnswerRecord.__table__,ErrorLog.__table__])


def item(kind='fill_blank'):
    return {'question_type':kind, 'question_text':'填写两元素___和___' if kind=='fill_blank' else '解释传输机制',
            'answer':'氢;氧' if kind=='fill_blank' else '完整参考答案', 'options':[], 'tags':[],
            'blanks':copy.deepcopy(BLANKS) if kind=='fill_blank' else [],
            'scoring_points':copy.deepcopy(POINTS) if kind!='fill_blank' else []}


async def run_agent(c, kind='fill_blank', count=1):
    return await QuestionBankAgent().run('summary','source',**{**options('question',count),'question_types':[kind]})


@pytest.mark.parametrize('kind',['fill_blank','short_answer','term_definition'])
async def test_actual_agent_commit_preserves_rubrics_and_partial_grade(contract, monkeypatch, kind):
    contract.quiz.side_effect=None;contract.quiz.return_value=[item(kind)]
    monkeypatch.setattr(exam_engine,'grade_semantic',AsyncMock(side_effect=AssertionError('Real AI forbidden')))
    result=await run_agent(contract,kind)
    assert result.status=='success',result.error
    qid=result.result['question_ids'][0]
    async with contract.factory() as db:
        row=await db.get(QuestionBank,qid)
        assert row.blanks==item(kind)['blanks']
        assert row.scoring_points==item(kind)['scoring_points']
    response=await contract.client.post('/api/v1/quiz/exam/create',json={'question_ids':[qid]},headers=contract.alice)
    assert response.status_code==200,response.text
    paper=response.json();q=paper['questions'][0]
    assert q['blanks']==item(kind)['blanks'] and q['scoring_points']==item(kind)['scoring_points']
    response=await contract.client.post('/api/v1/quiz/exam/submit',headers=contract.alice,
        json={'paper_id':paper['paper_id'],'answers':{qid:'氢;错' if kind=='fill_blank' else 'reliable'}})
    assert response.status_code==200,response.text
    graded=response.json();ratio=.5 if kind=='fill_blank' else .75
    assert graded['details'][0]['partial_score']==ratio
    assert graded['score']==5*ratio and graded['percentage']==100*ratio
    assert graded['semantic_graded_count']==0
    assert await size(contract,AnswerRecord)==1


@pytest.mark.parametrize('field,value',[
    ('blanks',{}),('blanks',[{'index':'1','answer':'氢'}]),('blanks',[{'index':True,'answer':'氢'}]),
    ('blanks',[{'index':1,'answer':3}]),('blanks',[{'answer':'氢'}]),
    ('scoring_points',{}),('scoring_points',[{'point':'可靠','keywords':'reliable','score':3}]),
    ('scoring_points',[{'point':'可靠','keywords':[3],'score':3}]),
    ('scoring_points',[{'point':'可靠','keywords':['reliable'],'score':-1}]),
    ('scoring_points',[{'point':'可靠','keywords':['reliable'],'score':float('nan')}]),
])
async def test_bad_rubric_rejects_whole_model_batch_without_coverage(contract,field,value):
    good=item();bad=item();bad[field]=value
    contract.quiz.side_effect=None;contract.quiz.return_value=[good,bad]
    result=await run_agent(contract,count=2)
    assert result.status=='error'
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==0


@pytest.mark.parametrize('legacy',['absent','null'])
async def test_legacy_missing_or_null_optional_metadata_remains_usable(contract,legacy):
    q=item('short_answer')
    for key in ('blanks','scoring_points'):
        if legacy=='absent':q.pop(key)
        else:q[key]=None
    contract.quiz.side_effect=None;contract.quiz.return_value=[q]
    result=await run_agent(contract,'short_answer')
    assert result.status=='success',result.error
    async with contract.factory() as db:
        row=await db.get(QuestionBank,result.result['question_ids'][0])
        assert row.blanks==[] and row.scoring_points==[]


async def test_extensions_survive_validation_and_new_database_session(contract):
    q=item();q['blanks'][0]['source']='明确模拟扩展'
    contract.quiz.side_effect=None;contract.quiz.return_value=[q]
    result=await run_agent(contract)
    assert result.status=='success',result.error
    async with contract.factory() as db:
        row=await db.get(QuestionBank,result.result['question_ids'][0])
        assert row.blanks[0]['source']=='明确模拟扩展'


async def test_bad_first_batch_does_not_contaminate_later_valid_batches(contract):
    bad=item();bad['scoring_points']={}
    contract.quiz.side_effect=[[item(),bad,item()],[item(),item(),item()],[item()]]
    result=await run_agent(contract,count=7)
    assert result.status=='success',result.error
    assert result.result['total_questions']==4
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==4
    async with contract.factory() as db:
        rows=(await db.scalars(select(QuestionBank))).all()
        assert all(row.blanks==BLANKS for row in rows)


async def test_bad_second_batch_does_not_discard_earlier_valid_resources(contract):
    bad=item();bad['blanks']={}
    contract.quiz.side_effect=[[item(),item(),item()],[item(),bad,item()],[item()]]
    result=await run_agent(contract,count=7)
    assert result.status=='success',result.error
    assert result.result['total_questions']==4
    assert await size(contract,QuestionBank)==await size(contract,KnowledgeCoverage)==4


@pytest.mark.parametrize('kind',['fill_blank','short_answer','term_definition'])
async def test_real_generator_prompt_and_json_parser_to_database(contract,monkeypatch,kind):
    import app.core.quiz as quiz
    monkeypatch.setattr(quiz.quiz_generator,'generate_questions',QuizGenerator().generate_questions)
    generated=AsyncMock(return_value=SimpleNamespace(content='```json\n'+json.dumps({'questions':[item(kind)]},ensure_ascii=False)+'\n```'))
    monkeypatch.setattr(quiz.api_client,'generate',generated)
    result=await run_agent(contract,kind)
    assert result.status=='success',result.error
    assert generated.await_count==1
    call=generated.call_args.kwargs
    assert call['prompt_template_id'].startswith('quiz_gen.')
    assert '模拟知识' in str(call['messages'])
    async with contract.factory() as db:
        row=await db.get(QuestionBank,result.result['question_ids'][0])
        assert row.blanks==item(kind)['blanks'] and row.scoring_points==item(kind)['scoring_points']


@pytest.mark.parametrize('stream',[False,True])
@pytest.mark.parametrize('kind',['fill_blank','short_answer'])
async def test_native_http_orchestration_then_quiz_keeps_local_rubric(contract,monkeypatch,tmp_path,stream,kind):
    import app.core.quiz as quiz
    from fastapi import FastAPI
    monkeypatch.setattr(quiz.quiz_generator,'generate_questions',QuizGenerator().generate_questions)
    monkeypatch.setattr(exam_engine,'grade_semantic',AsyncMock(side_effect=AssertionError('Real AI forbidden')))
    provider=FastAPI();provider_calls=[]
    @provider.post('/explicitly-simulated-provider')
    async def simulated(body:dict):
        provider_calls.append(body)
        q=item(kind)
        if kind=='fill_blank':q.pop('answer')  # Native multi-blank template shape.
        return {'content':json.dumps({'questions':[q]},ensure_ascii=False)}
    trace=[]
    async with localhost(provider) as model:
        async def bridge(**kwargs):
            response=await model.post('/explicitly-simulated-provider',json={'messages':kwargs['messages'],'template':kwargs['prompt_template_id']})
            response.raise_for_status();return SimpleNamespace(content=response.json()['content'])
        monkeypatch.setattr(quiz.api_client,'generate',bridge)
        async with localhost(contract.app) as client:
            base_url=str(client.base_url)
            async def request(method,path,**kwargs):
                response=await client.request(method,path,headers=contract.alice,**kwargs)
                trace.append({'method':method,'path':path,'status':response.status_code,'body':response.text})
                assert response.status_code==200,response.text
                return response
            req={'summary_id':'summary','document_id':'source','agents':['question_bank'],
                 'config':{**options('question',1),'question_types':[kind]}}
            response=await request('POST','/api/v1/agents/orchestrate'+('/stream' if stream else ''),json=req)
            if stream:
                blocks=[block for block in response.text.split('\n\n') if block.strip()]
                assert any(block.startswith('event: agent_complete\n') for block in blocks),response.text
                events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                result=events[-1]['results']['question_bank']
            else:result=response.json()['results']['question_bank']
            assert result['status']=='success',result
            qid=result['result']['question_ids'][0]
            # Independent session re-reads the committed row before the HTTP exam.
            async with contract.factory() as db:
                row=await db.get(QuestionBank,qid)
                assert row.blanks==item(kind)['blanks'] and row.scoring_points==item(kind)['scoring_points']
            paper=(await request('POST','/api/v1/quiz/exam/create',json={'question_ids':[qid]})).json()
            graded=(await request('POST','/api/v1/quiz/exam/submit',json={'paper_id':paper['paper_id'],
                'answers':{qid:'氢;错' if kind=='fill_blank' else 'reliable'}})).json()
            assert graded['score']==(2.5 if kind=='fill_blank' else 3.75)
            assert graded['semantic_graded_count']==0
    with socket.socket() as sock:
        sock.settimeout(.5);assert sock.connect_ex(('127.0.0.1',int(base_url.rsplit(':',1)[-1].strip('/'))))!=0
    async with contract.factory() as db:
        assert await size(contract,AnswerRecord)==1
        assert (await db.get(QuestionBank,qid)).blanks==item(kind)['blanks']
    root=Path(__file__).resolve().parents[2]
    source=['backend/app/core/agents/question_bank_agent.py','backend/app/core/quiz/__init__.py','backend/app/core/quiz/metadata.py',
            'backend/app/api/questions.py','backend/app/api/agents.py','backend/app/models/__init__.py']
    evidence={'Time':datetime.now().astimezone().isoformat(),'Transport':'real localhost HTTP/SSE','Database':'temporary SQLite with FK ON',
              'Generation':'explicitly simulated provider HTTP at scheduler-client seam; native prompt/JSON parser',
              'RealAI':0,'MockProviderHTTP':len(provider_calls),'BusinessHTTP':len(trace),'Trace':trace,
              'SourceHashes':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in source},'ServerStopped':True,
              'TestSHA256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'DatabasePath':contract.factory.kw['bind'].url.database,'BackendURL':base_url,'ProviderURL':str(model.base_url)}
    path=Path(os.environ.get('ROUND09B2_EVIDENCE',str(tmp_path)))
    path.mkdir(parents=True,exist_ok=True)
    (path/f'real-native-{kind}-{int(stream)}.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')


@pytest.mark.parametrize('form',['absent','null'])
async def test_native_multiblank_prompt_shape_without_aggregate_answer(contract,monkeypatch,form):
    import app.core.quiz as quiz
    q=item()
    if form=='absent':q.pop('answer')
    else:q['answer']=None
    monkeypatch.setattr(quiz.quiz_generator,'generate_questions',QuizGenerator().generate_questions)
    generated=AsyncMock(return_value=SimpleNamespace(content=json.dumps({'questions':[q]},ensure_ascii=False)))
    monkeypatch.setattr(quiz.api_client,'generate',generated)
    result=await run_agent(contract)
    assert result.status=='success',result.error
    async with contract.factory() as db:
        row=await db.get(QuestionBank,result.result['question_ids'][0])
        assert row.correct_answer=='氢;氧' and row.blanks==BLANKS
    assert 'blanks' in str(generated.call_args.kwargs['messages'])


@pytest.mark.parametrize('kind,value',[('fill_blank',''),('fill_blank',None),('short_answer',None)])
async def test_missing_reference_without_valid_fill_rubric_stays_error(contract,kind,value):
    q=item(kind);q['answer']=value
    if value is None:q['blanks']=[]
    contract.quiz.side_effect=None;contract.quiz.return_value=[q]
    result=await run_agent(contract,kind)
    assert result.status=='error'
    assert await size(contract,QuestionBank)==0


@pytest.mark.parametrize('invalid',[False,True])
async def test_cross_topic_generation_uses_same_rubric_boundary(contract,monkeypatch,invalid):
    import app.core.quiz as quiz
    from app.core.quiz.relation_extractor import relation_extractor
    monkeypatch.setattr(relation_extractor,'get_cross_topic_pairs',lambda relations:[{'source':'模拟知识0','target':'模拟知识1','type':'related_to'}])
    contract.quiz.side_effect=lambda **kwargs:[item('short_answer') for _ in range(kwargs['count'])]
    cross=item('short_answer')
    if invalid:cross['scoring_points']={}
    monkeypatch.setattr(quiz.quiz_generator,'generate_cross_topic',AsyncMock(return_value=[cross]))
    result=await QuestionBankAgent().run('summary','source',**{**options('question',3),'enable_kg_relations':True})
    assert result.status=='success',result.error
    assert result.result['total_questions']==(2 if invalid else 3)
    async with contract.factory() as db:
        rows=(await db.scalars(select(QuestionBank))).all()
        coverage=(await db.scalars(select(KnowledgeCoverage))).all()
        assert len(coverage)==(2 if invalid else 4)
        assert all(row.scoring_points==POINTS for row in rows)
        assert {c.resource_id for c in coverage}=={q.id for q in rows}
