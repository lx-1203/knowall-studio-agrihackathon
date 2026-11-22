"""Literal blank transport: real local scoring/JWT/SQL/HTTP, no external AI."""
import asyncio,hashlib,json,os,socket
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx,pytest,uvicorn
from sqlalchemy import select
from app.api import questions
from app.core.quiz import exam_engine
from app.models import AnswerRecord,ExamPaper
from tests.test_learning_pack_rubric_flow import flow,make,question

LITERALS=['alpha;beta','alpha；beta','1,000','一，二','line\nnext','say "yes"','["literal"]','path\\name',' ;,；， ','氧']

@pytest.fixture
def proof(request):
    project=Path(__file__).resolve().parents[2]
    scope=['backend/app/core/quiz/__init__.py','backend/app/core/learning_workbench.py','backend/app/core/study_pack_service.py',
           'backend/app/api/questions.py','backend/app/api/learning.py','backend/app/core/auth.py','backend/app/models/__init__.py',
           'backend/tests/test_learning_pack_rubric_flow.py','backend/tests/test_blank_answer_transport_flow.py']
    p={'test':request.node.nodeid,'start':datetime.now().astimezone().isoformat(),
       'source_hashes':{s:hashlib.sha256((project/s).read_bytes()).hexdigest() for s in scope},'external_ai_calls':0}
    yield p
    p['end']=datetime.now().astimezone().isoformat()
    dest=os.environ.get('BLANK_TRANSPORT_PROOF_DIR')
    if dest:
        d=Path(dest);d.mkdir(parents=True,exist_ok=True)
        (d/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')

def raw(values):
    return {**question(),'answer':';'.join(values),
            'blanks':[{'index':i+1,'answer':v} for i,v in enumerate(values)]}

@pytest.mark.parametrize('literal',LITERALS)
@pytest.mark.parametrize('kind',['local','enhanced'])
async def test_encoded_literal_preserves_each_blank(proof,literal,kind):
    values=[literal.strip(),'氧'];encoded=json.dumps(values,ensure_ascii=False)
    q=raw(values)
    if kind=='local':result=exam_engine._score_multi_blank(encoded,q['blanks'])
    else:
        full=await exam_engine.grade_enhanced({'questions':[{'id':'q',**q}]},{'q':encoded},enable_semantic=False)
        result={'score_ratio':full['details'][0]['partial_score'],'blank_details':full['details'][0]['blank_details']}
        assert full['score']==5 and full['semantic_graded_count']==0
    proof.update(input=encoded,expected=values,result=result)
    assert result['score_ratio']==1
    assert [b['user_answer'] for b in result['blank_details']]==values

@pytest.mark.parametrize('answer,values',[
    ('甲;乙',['甲','乙']),('甲；乙',['甲','乙']),('甲,乙',['甲','乙']),
    ('甲，乙',['甲','乙']),('甲\n乙',['甲','乙']),('甲',['甲']),
])
def test_legacy_delimiters_remain_supported(proof,answer,values):
    result=exam_engine._score_multi_blank(answer,raw(values)['blanks'])
    proof.update(input=answer,expected=values,result=result)
    assert result['score_ratio']==1

@pytest.mark.parametrize('bad',['[null,"氧"]','[true,"氧"]','[1,"氧"]','[{},"氧"]','[["甲"],"氧"]','["甲","氧", "extra"]','["甲"]','[]','["甲",'])
def test_unusable_structured_arrays_never_get_credit(proof,bad):
    result=exam_engine._score_multi_blank(bad,raw(['甲','氧'])['blanks'])
    proof.update(input=bad,result=result)
    assert result['score_ratio']==0 and not result['is_correct']

@pytest.mark.parametrize('values,expected',[(['','氧'],.5),(['甲',''],.5),(['',''],0)])
def test_encoded_empty_slots_keep_position(proof,values,expected):
    encoded=json.dumps(values,ensure_ascii=False)
    result=exam_engine._score_multi_blank(encoded,raw(['甲','氧'])['blanks'])
    proof.update(input=encoded,result=result)
    assert result['score_ratio']==expected
    assert [b['user_answer'] for b in result['blank_details']]==values

@pytest.fixture
async def native(flow,proof):
    flow.app.include_router(questions.router)
    listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(flow.app,log_level='error',lifespan='off',ws='none'))
    task=asyncio.create_task(server.serve(sockets=[listener]))
    proof.update(port=port,http=[],database=flow.factory.kw['bind'].url.database,
                 simulation=['generate_pack custom local generator provides declared question, external adapter forbidden'],simulated_generator_calls=1)
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():await task
                await asyncio.sleep(.01)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=15) as client:
            yield SimpleNamespace(**vars(flow),http=client)
        async with flow.factory() as db:
            rows=(await db.scalars(select(AnswerRecord))).all()
            proof['saved_answers']=[{'question_id':r.question_id,'user_id':r.user_id,'answer':r.user_answer,'is_correct':r.is_correct} for r in rows]
        proof['model_calls']=flow.model.await_count
        assert not flow.model.await_count
    finally:
        server.should_exit=True
        try:await asyncio.wait_for(task,5)
        except TimeoutError:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        listener.close()

@pytest.mark.parametrize('mode',['learning','exam'])
@pytest.mark.parametrize('values',[[s,'氧'] for s in LITERALS[:7] if s!='say "yes"'])
async def test_native_literal_answers_are_scored_and_saved(native,proof,mode,values):
    pack=await make(native,[raw(values)]);qid=pack['sections'][0]['questions'][0]['id']
    encoded=json.dumps(values,ensure_ascii=False)
    if mode=='exam':
        async with native.factory() as db:
            db.add(ExamPaper(id='literal-paper',title='literal fixture',question_ids=[qid]));await db.commit()
        url='/api/v1/quiz/exam/submit';payload={'paper_id':'literal-paper','answers':{qid:encoded},'enable_semantic':False}
    else:
        url=f'/api/v1/learning/packs/{pack["id"]}/answers';payload={'question_id':qid,'answer':encoded}
    response=await native.http.post(url,headers=native.alice,json=payload)
    proof['http'].append({'method':'POST','url':url,'request':payload,'status':response.status_code,'response':response.json()})
    assert response.status_code==200,response.text
    result=response.json();proof.update(input=encoded,expected=values)
    assert (result['score_ratio'] if mode=='learning' else result['details'][0]['partial_score'])==1
    if mode=='exam':assert result['score']==5
    async with native.factory() as db:
        r=await db.scalar(select(AnswerRecord))
        assert r.user_id=='alice' and r.user_answer==encoded and r.is_correct
