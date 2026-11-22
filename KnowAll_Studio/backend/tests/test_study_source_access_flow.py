"""Actual SQLite/JWT/HTTP source authorization; ordinary model output is mocked."""
import asyncio,hashlib,json,os,socket
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from sqlalchemy import select,func,delete,text

from app.api import study
from app.models import Document,DocumentChunk,KnowledgeSummary,KnowledgePointNode,StudyPlan,StudyGoal
from tests.test_study_notification_flow import flow


@pytest.fixture
async def sources(flow,monkeypatch,request):
    engine=flow.factory.kw['bind']
    async with engine.begin() as conn:
        await conn.run_sync(lambda c:DocumentChunk.__table__.create(c))
    async with flow.factory() as db:
        docs=[('alice-private','alice'),('bob-private','bob'),('local-private','local_user'),('legacy-public',None),('empty-private','bob')]
        for did,owner in docs:
            db.add(Document(id=did,filename=did+'.txt',file_type='txt',sha256=did,
                            metadata_={'knowledge_owner_id':owner} if owner else {}))
        await db.flush()
        for did,_ in docs:
            db.add(KnowledgeSummary(id=did+'-summary',document_id=did,content_md='# '+did+' secret outline'))
        await db.flush()
        for did,_ in docs:
            db.add(KnowledgePointNode(id=did+'-node',summary_id=did+'-summary',document_id=did,
                    level=2,sequence=0,title=did+' secret topic',explanation='private-node-body'))
            if did!='empty-private':
                # Insert in reverse order to prove order comes from chunk_index.
                db.add(DocumentChunk(id=did+'-two',doc_id=did,chunk_index=1,text_content=did+'-SECOND'))
                db.add(DocumentChunk(id=did+'-one',doc_id=did,chunk_index=0,text_content=did+'-FIRST'))
        await db.commit()
    project=Path(__file__).resolve().parents[2]
    loaded=['backend/app/api/study.py','backend/app/core/documents.py','backend/app/core/agents/study_plan_agent.py',
            'backend/app/core/auth.py','backend/app/models/__init__.py','backend/tests/test_study_notification_flow.py',
            'backend/tests/test_study_source_access_flow.py']
    proof={'test':request.node.nodeid,'start':datetime.now().astimezone().isoformat(),
            'source_hashes':{s:hashlib.sha256((project/s).read_bytes()).hexdigest() for s in loaded},
            'database':engine.url.database,'http':[],'key_loads':[],'external_ai_calls':0,
            'simulation':['assistant.chat AsyncMock for ordinary plan; enhanced runs real local scheduler',
                'shared fixture freezes study API clock only; source access uses real SQL and JWT']}
    original=study.load_user_api_keys
    async def keys(uid,db):
        proof['key_loads'].append(uid)
        await original(uid,db)
    monkeypatch.setattr(study,'load_user_api_keys',keys)
    monkeypatch.setattr(study.settings,'desktop_require_login',False)
    flow.model.return_value=json.dumps({'name':'SIMULATED source plan','goals':[{'title':'SIMULATED goal','priority':'medium'}]})
    try:
        yield SimpleNamespace(**vars(flow),proof=proof)
    finally:
        proof['mock_model_calls']=flow.model.await_count
        proof['mock_prompts']=[call.args[0] for call in flow.model.await_args_list]
        async with flow.factory() as db:
            rows=(await db.execute(select(StudyPlan))).scalars().all()
            proof['saved_plans']=[{'id':r.id,'user_id':r.user_id,'name':r.name,'knowledge_point_ids':r.knowledge_point_ids} for r in rows]
            proof['goal_count']=await db.scalar(select(func.count()).select_from(StudyGoal))
        proof['end']=datetime.now().astimezone().isoformat()
        output=os.environ.get('STUDY_SOURCE_PROOF_DIR')
        if output:
            dest=Path(output);dest.mkdir(parents=True,exist_ok=True)
            (dest/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')


def payload(kind,source):
    return {'document_id':source} if kind=='ordinary' else {'summary_id':source+'-summary','plan_type':'short','start_date':'2026-10-10'}


async def post(flow,kind,source,account='alice',client=None,extra=None):
    headers=getattr(flow,account) if account else {}
    body={**payload(kind,source),**(extra or {})}
    response=await (client or flow.client).post('/api/v1/study/'+('generate-plan' if kind=='ordinary' else 'generate-plan-enhanced'),headers=headers,json=body)
    flow.proof['http'].append({'transport':'native' if client else 'ASGI','kind':kind,'source':source,'account':account,
                              'status':response.status_code,'response':response.json()})
    return response


@pytest.mark.parametrize('kind',['ordinary','enhanced'])
@pytest.mark.parametrize('source,account,status',[
    ('bob-private','alice',403),('alice-private','bob',403),('local-private',None,403),
    ('empty-private','alice',403),('alice-private','alice',200),('legacy-public','bob',200),('legacy-public',None,200),
    ('missing','alice',404),
])
async def test_source_matrix_prevents_foreign_generation_and_preserves_legacy(sources,kind,source,account,status):
    response=await post(sources,kind,source,account)
    assert response.status_code==status,response.text
    allowed=status==200
    assert len(sources.proof['key_loads'])==int(allowed)
    assert sources.model.await_count==int(allowed and kind=='ordinary')
    async with sources.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPlan))==int(allowed)
        if allowed:
            row=await db.scalar(select(StudyPlan))
            assert row.user_id==(account or 'local_user')
            if kind=='enhanced':assert row.knowledge_point_ids==[source+'-node']
    if allowed and kind=='ordinary':
        prompt=sources.model.await_args.args[0]
        assert prompt.index(source+'-FIRST')<prompt.index(source+'-SECOND')
        assert sources.model.await_args.kwargs['user_id']==(account or 'local_user')


async def orphan(flow):
    # Legacy installations have FK enforcement off by default. Only this test
    # connection temporarily disables it to reproduce a pre-existing orphan.
    async with flow.factory() as db:
        await db.execute(text('PRAGMA foreign_keys=OFF'))
        await db.execute(delete(Document).where(Document.id=='bob-private'))
        await db.commit()
        await db.execute(text('PRAGMA foreign_keys=ON'))
    flow.proof['simulation'].append('legacy orphan introduced with temporary FK=OFF; normal fixture uses FK=ON')


async def test_orphan_summary_cannot_create_an_enhanced_plan(sources):
    await orphan(sources)
    response=await post(sources,'enhanced','bob-private')
    assert response.status_code==404,response.text
    assert not sources.proof['key_loads']
    async with sources.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPlan))==0


async def test_explicit_text_preserves_route_behavior_without_reading_unused_document(sources):
    response=await post(sources,'ordinary','bob-private',extra={'knowledge_text':'explicit-user-text'})
    assert response.status_code==200,response.text
    prompt=sources.model.await_args.args[0]
    assert 'explicit-user-text' in prompt and 'bob-private-FIRST' not in prompt


async def test_ordinary_prompt_cap_and_order_are_preserved(sources):
    async with sources.factory() as db:
        row=await db.get(DocumentChunk,'alice-private-one')
        row.text_content='A'*4100
        await db.commit()
    response=await post(sources,'ordinary','alice-private')
    assert response.status_code==200,response.text
    prompt=sources.model.await_args.args[0]
    assert 'A'*4000 in prompt and 'A'*4001 not in prompt and 'alice-private-SECOND' not in prompt


@pytest.fixture
async def native(sources):
    listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(sources.client._transport.app,log_level='error',lifespan='off'))
    task=asyncio.create_task(server.serve(sockets=[listener]))
    for _ in range(100):
        if server.started:break
        if task.done():await task
        await asyncio.sleep(.02)
    assert server.started
    sources.proof['port']=port
    try:
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=15) as client:
            yield client
    finally:
        server.should_exit=True
        try:await asyncio.wait_for(task,5)
        except TimeoutError:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        listener.close()


@pytest.mark.parametrize('kind',['ordinary','enhanced'])
@pytest.mark.parametrize('source,account,status',[('bob-private','alice',403),('local-private',None,403),('alice-private','alice',200),('legacy-public','bob',200)])
async def test_real_http_identity_source_gate_and_retry(sources,native,kind,source,account,status):
    result=await post(sources,kind,source,account,client=native)
    assert result.status_code==status,result.text
    before=len(sources.proof['key_loads'])
    assert before==int(status==200)
    # After rejection, the same server and signed owner can still create.
    if status!=200:
        result=await post(sources,kind,'alice-private','alice',client=native)
        assert result.status_code==200,result.text
    assert len(sources.proof['key_loads'])==1
    assert sources.model.await_count==int(kind=='ordinary')
    async with sources.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPlan))==1


async def test_real_http_orphan_summary_rejection(sources,native):
    await orphan(sources)
    response=await post(sources,'enhanced','bob-private',client=native)
    assert response.status_code==404,response.text
    assert not sources.proof['key_loads'] and not sources.model.await_count


async def multi_source(sources,secondary):
    async with sources.factory() as db:
        summary=await db.get(KnowledgeSummary,'legacy-public-summary')
        summary.document_ids=['legacy-public',secondary]
        await db.commit()


@pytest.mark.parametrize('secondary,account,status',[
    ('alice-private','alice',200),('bob-private','alice',403),
    ('bob-private','bob',200),('missing','alice',404),
])
async def test_multi_source_summary_requires_every_source(sources,secondary,account,status):
    await multi_source(sources,secondary)
    response=await post(sources,'enhanced','legacy-public',account)
    assert response.status_code==status,response.text
    assert len(sources.proof['key_loads'])==int(status==200)
    async with sources.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPlan))==int(status==200)


@pytest.mark.parametrize('secondary,status',[('bob-private',403),('missing',404)])
async def test_real_http_multi_source_rejection(sources,native,secondary,status):
    await multi_source(sources,secondary)
    response=await post(sources,'enhanced','legacy-public',client=native)
    assert response.status_code==status,response.text
    assert not sources.proof['key_loads'] and not sources.model.await_count
