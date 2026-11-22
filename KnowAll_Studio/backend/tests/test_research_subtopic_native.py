"""Real temporary SQLite/JWT/TCP routes; model and searches are in-process mocks."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from app.config import settings
from app.core.api_scheduler.client import _current_user_id
from tests.test_generation_identity_flow import flow, identity
from tests.test_research_progress_stream_http_flow import server


@pytest.mark.parametrize('mode',['bad-items','bad-title','empty-rephrase','valid','provider-error'])
async def test_real_research_schema_fallback_state_owner_and_next_task(identity,monkeypatch,record_property,mode):
    monkeypatch.setattr(settings,'desktop_require_login',True)
    entered,release=asyncio.Event(),asyncio.Event()
    model_calls,tool_calls,requests=[],[],[]
    first=[True]
    original_tool=identity.search.side_effect
    async def model(prompt,**kwargs):
        phase='rephrase' if '细化为' in prompt else 'decompose' if '分解为' in prompt else 'report'
        model_calls.append({'phase':phase,'owner':_current_user_id.get()})
        if phase=='rephrase':
            return '" '+"''"+' "' if first[0] and mode=='empty-rephrase' else '明确模拟细化主题'
        if phase=='decompose':
            if first[0]:
                entered.set()
                await release.wait()
                if mode=='provider-error':raise RuntimeError('明确模拟provider失败')
                if mode=='bad-items':return '[null]'
                if mode=='bad-title':return '[{"title":null,"description":[]}]'
                if mode=='valid':return '[{"title":" 子课题A ","description":" 描述A "},{"title":"子课题B"},null]'
            return '[{"title":"恢复子课题","description":"恢复描述"}]'
        return '# 明确模拟报告\n研究发现。[来源1]'
    async def tool(name,**kwargs):
        tool_calls.append({'name':name,'query':kwargs['query'],'owner':_current_user_id.get()})
        return await original_tool(name,**kwargs)
    identity.model.side_effect=model
    identity.search.side_effect=tool
    app=identity.client._transport.app
    @app.middleware('http')
    async def observe(request,call_next):
        response=await call_next(request)
        requests.append({'method':request.method,'path':request.url.path,'status':response.status_code})
        return response
    try:
        async with server(app) as client:
            response=await client.post('/api/v1/research/start',headers=identity.headers['alice'],json={'topic':'原始合法主题','max_subtopics':2,'user_id':'bob'})
            assert response.status_code==200,response.text
            task_id=response.json()['task_id']
            await asyncio.wait_for(entered.wait(),3)
            path=f'/api/v1/research/{task_id}'
            assert (await client.get(path+'/status',headers=identity.headers['bob'])).status_code==404
            assert (await client.get(path+'/status')).status_code==401
            release.set()
            await asyncio.wait_for(asyncio.gather(*list(identity.agent._running)),5)
            response=await client.get(path+'/status',headers=identity.headers['alice'])
            assert response.status_code==200
            data=response.json()
            expected='error' if mode=='provider-error' else 'completed'
            assert data['status']==expected,data
            stream=await client.get(path+'/stream',headers=identity.headers['alice'])
            assert stream.status_code==200
            assert ('event: error' if expected=='error' else 'event: complete') in stream.text
            assert ('event: complete' if expected=='error' else 'event: error') not in stream.text
            if expected=='completed':
                assert data['report'] and data['progress']==1 and len(data['sources'])==1
                assert all(isinstance(st['title'],str) and st['title'].strip() and isinstance(st['description'],str) for st in data['sub_topics'])
                if mode in ('bad-items','bad-title'):
                    assert [(s['title'],s['description']) for s in data['sub_topics']]==[('明确模拟细化主题','明确模拟细化主题')]
                elif mode=='valid':
                    assert [(s['title'],s['description']) for s in data['sub_topics']]==[('子课题A','描述A'),('子课题B','')]
                else:
                    assert data['refined_topic']=='原始合法主题'
            else:
                assert data['error']=='明确模拟provider失败' and data['progress']<1 and not data['report']
            own=(await client.get('/api/v1/research/tasks',headers=identity.headers['alice'])).json()['tasks']
            assert [t['task_id'] for t in own]==[task_id]
            assert (await client.get('/api/v1/research/tasks',headers=identity.headers['bob'])).json()['tasks']==[]
            first[0]=False
            response=await client.post('/api/v1/research/start',headers=identity.headers['alice'],json={'topic':'恢复研究主题','max_subtopics':2})
            assert response.status_code==200
            next_id=response.json()['task_id']
            await asyncio.wait_for(asyncio.gather(*list(identity.agent._running)),5)
            response=await client.get(f'/api/v1/research/{next_id}/status',headers=identity.headers['alice'])
            assert response.status_code==200 and response.json()['status']=='completed',response.text
            stream=await client.get(f'/api/v1/research/{next_id}/stream',headers=identity.headers['alice'])
            assert stream.status_code==200 and 'event: complete' in stream.text
            assert _current_user_id.get()=='local_user'
            assert len(requests)==10
            assert all(c['owner']=='alice' for c in model_calls+tool_calls)
    finally:
        release.set()
        await identity.agent.close()
        path=Path(identity.factory.kw['bind'].url.database)
        backend=Path(__file__).resolve().parents[1]
        modules=['app/core/agents/research_agent.py','app/api/research.py','app/core/auth.py','app/core/generation_access.py','app/models/user.py','app/database.py']
        record_property('native',json.dumps({'mode':mode,'requests':requests,'modelCalls':model_calls,'toolCalls':tool_calls,'providerHTTP':0,'tempRoot':str(path.parent),'listeners':getattr(app.state,'research_stream_listener',{}),'sources':{f:hashlib.sha256((backend/f).read_bytes()).hexdigest() for f in modules}},ensure_ascii=False))
