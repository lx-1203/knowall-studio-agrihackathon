"""Real adapters, SQLite and HTTP; AI provider is an explicitly simulated localhost service."""
import asyncio
import copy
import json
import hashlib
import os
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI,Request,HTTPException
from app.core.api_scheduler.client import UnifiedAPIClient,GenerationConfig,RequestContext,TaskType
from app.core.api_scheduler.adapters.base import AdapterConfig
from app.models import APICache,APICallLog,KnowledgeTree,KnowledgeEdge
from app.database import Base
from tests.test_generation_cache_isolation_flow import cache_flow,rows
from tests.test_study_notification_flow import flow
from tests.test_pipeline_lifecycle_flow import localhost


@pytest.fixture
async def routing(cache_flow,monkeypatch,request):
    import app.core.api_scheduler as api
    from app.core.api_scheduler.adapters import openai,anthropic,http_utils
    async with cache_flow.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,tables=[KnowledgeTree.__table__,KnowledgeEdge.__table__])
    app=FastAPI();requests=[];blocked=[]
    state=SimpleNamespace(fail=False,response_model=None)
    @app.post('/{path:path}')
    async def simulated_provider(request:Request,path:str):
        payload=await request.json()
        marker=request.headers.get('x-api-key') or request.headers.get('authorization','').removeprefix('Bearer ')
        requests.append({'path':path,'payload':copy.deepcopy(payload),'simulation':True,
                         'credential_marker':marker if marker.startswith('explicit-simulated') else ''})
        allowed={'openai/chat/completions','anthropic/messages','deepseek/v1/chat/completions','deepseek/anthropic/messages'}
        if path not in allowed:
            raise HTTPException(404,'explicit simulated protocol mismatch')
        if state.fail:
            raise HTTPException(503,'explicit simulated AI service failure')
        model=payload['model']
        messages=str(payload.get('messages',[]))+str(payload.get('system',''))
        if 'BOIS' in messages:
            body=json.dumps({'restructured_tree':{'nodes':[{'id':'sim-root','label':'模拟-'+model,
                'level':1,'children':[{'id':'sim-child','label':'模拟子节点','level':2,'children':[]}]}]}})
        else:
            body=json.dumps([{'word':'simulated-'+model,'definition':'明确模拟模型词汇','example_sentence':'simulated'}])
        actual=model+'-reported' if state.response_model is None else state.response_model
        if path.endswith('/messages'):
            return {'model':actual,'content':[{'type':'text','text':body}],
                    'usage':{'input_tokens':7,'output_tokens':11},'stop_reason':'end_turn'}
        return {'model':actual,'choices':[{'message':{'content':body},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':7,'completion_tokens':11}}
    unified=UnifiedAPIClient()
    monkeypatch.setattr(api,'api_client',unified)
    started=datetime.now().astimezone().isoformat()
    async with localhost(app) as provider:
        origin=str(provider.base_url).rstrip('/')
        def only_local(url,headers,payload,timeout):
            if urlparse(url).hostname!='127.0.0.1' or not url.startswith(origin+'/'):
                blocked.append(url)
                raise RuntimeError('blocked non-local model endpoint in isolated test')
            return http_utils.sync_post(url,headers,payload,timeout)
        monkeypatch.setattr(openai,'sync_post',only_local)
        monkeypatch.setattr(anthropic,'sync_post',only_local)
        try:
            yield SimpleNamespace(**{k:v for k,v in vars(cache_flow).items() if k!='api'},api=unified,
                                  origin=origin,requests=requests,blocked=blocked,state=state)
        finally:
            folder=Path(os.environ.get('KNOWALL_MODEL_EVIDENCE_DIR',str(Path(__file__).resolve().parents[2]/'docs/test-round-06b8')))
            phase=os.environ.get('KNOWALL_MODEL_EVIDENCE_PHASE','network')
            target=folder/('provider-'+phase);target.mkdir(parents=True,exist_ok=True)
            value={'Test':request.node.nodeid,'Start':started,'End':datetime.now().astimezone().isoformat(),
                'Origin':origin,'Requests':requests,'BlockedEndpoints':blocked,'ExternalAI':'explicit simulated localhost HTTP provider',
                'ClientSHA256':hashlib.sha256((Path(__file__).resolve().parents[1]/'app/core/api_scheduler/client.py').read_bytes()).hexdigest()}
            (target/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(
                json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def args(model,user='alice',content='explicit simulation'):
    return dict(task_type=TaskType.CHAT,messages=[{'role':'system','content':'explicit simulated tutor'},
        {'role':'user','content':content}],prompt_template_id='model-routing-simulation',
        generation_content=content,config=GenerationConfig(model=model,max_retries=0,timeout=5),
        context=RequestContext(user_id=user))


MATRIX=[
 ('openai','openai/selected','openai','registered','selected'),
 ('openai_alice','openai/selected','openai','registered','selected'),
 ('anthropic','anthropic/selected','anthropic','registered','selected'),
 ('anthropic_alice','anthropic/selected','anthropic','registered','selected'),
 ('claude-opus-4-6_alice','claude-opus-4-6','anthropic','claude-opus-4-6','claude-opus-4-6'),
 ('deepseek','deepseek/selected','deepseek/v1','registered','selected'),
 ('deepseek_alice','deepseek/selected','deepseek/v1','registered','selected'),
 ('deepseek_alice','deepseek/selected','deepseek/anthropic','registered','selected'),
 ('deepseek-reasoner_alice','deepseek-reasoner','deepseek/anthropic','deepseek-reasoner','deepseek-reasoner'),
 ('gpt-4o-mini_alice','gpt-4o-mini','openai','gpt-4o-mini','gpt-4o-mini'),
 ('custom_alice','custom/selected','openai','registered','selected'),
 ('ollama','ollama/selected','openai','registered','selected'),
 ('openai','openai','openai','registered','registered'),
]


@pytest.mark.parametrize('key,ref,endpoint,registered,selected',MATRIX)
async def test_selected_model_protocol_and_actual_reported_model(routing,key,ref,endpoint,registered,selected):
    routing.api.configure_adapter(key,'explicit-simulated-key',routing.origin+'/'+endpoint,registered)
    original=routing.api._adapters[key]
    before=original.model_name
    first=await routing.api.generate(**args(ref))
    assert routing.requests[-1]['payload']['model']==selected
    expected_path=endpoint+('/messages' if endpoint in ('anthropic','deepseek/anthropic') else '/chat/completions')
    assert routing.requests[-1]['path']==expected_path
    assert original.model_name==before==registered
    assert first.model_used==selected+'-reported'
    cached=await routing.api.generate(**args(ref))
    assert cached.from_cache and cached.model_used==selected+'-reported(cache)'
    assert len(routing.requests)==1 and not routing.blocked
    assert (await rows(routing,APICache))[0].model_used==selected+'-reported'
    assert (await rows(routing,APICallLog))[0].model_name==selected+'-reported'


@pytest.mark.parametrize('key,provider,path',[('openai_alice','openai','openai'),('anthropic_alice','anthropic','anthropic')])
async def test_concurrent_request_bound_models_do_not_mutate_registered_adapter(routing,key,provider,path):
    routing.api.configure_adapter(key,'explicit-simulated-key',routing.origin+'/'+path,'registered')
    one=routing.api.get_adapter(provider+'/first',user_id='alice')
    two=routing.api.get_adapter(provider+'/second',user_id='alice')
    results=await asyncio.gather(one.chat_completion([{'role':'user','content':'first'}],AdapterConfig(timeout=5)),
                                 two.chat_completion([{'role':'user','content':'second'}],AdapterConfig(timeout=5)))
    assert {r.model for r in results}=={'first-reported','second-reported'}
    assert {r['payload']['model'] for r in routing.requests}=={'first','second'}
    assert routing.api._adapters[key].model_name=='registered'
    assert one is not two and one is not routing.api._adapters[key]


async def test_bare_unknown_reference_preserves_registered_adapter_compatibility(routing):
    routing.api.configure_adapter('openai','explicit-simulated-key',routing.origin+'/openai','registered')
    result=await routing.api.generate(**args('unconfigured'))
    assert routing.requests[0]['payload']['model']=='registered'
    assert result.model_used=='registered-reported'


async def test_empty_reported_model_uses_reference_as_fallback(routing):
    routing.state.response_model=''
    routing.api.configure_adapter('openai','explicit-simulated-key',routing.origin+'/openai','registered')
    result=await routing.api.generate(**args('openai/selected'))
    assert result.model_used=='openai/selected'
    assert (await rows(routing,APICache))[0].model_used=='openai/selected'


async def test_user_scoped_adapter_selected_without_affecting_global(routing):
    for key,model in [('openai','global'),('openai_alice','alice-default'),('openai_bob','bob-default')]:
        routing.api.configure_adapter(key,'explicit-simulated-'+key,routing.origin+'/openai',model)
    a=await routing.api.generate(**args('openai/selected','alice','alice content'))
    b=await routing.api.generate(**args('openai/another','bob','bob content'))
    assert a.model_used=='selected-reported' and b.model_used=='another-reported'
    assert routing.api._adapters['openai'].model_name=='global'
    assert routing.api._adapters['openai_alice'].model_name=='alice-default'
    assert routing.api._adapters['openai_bob'].model_name=='bob-default'
    assert [call['credential_marker'] for call in routing.requests]==[
        'explicit-simulated-openai_alice','explicit-simulated-openai_bob']


async def test_pre_routing_fix_version_two_cache_is_not_reused(routing):
    routing.api.configure_adapter('openai','explicit-simulated-key',routing.origin+'/openai','registered')
    request=args('openai/selected');config=request['config'];context=request['context']
    legacy={'version':2,'messages':request['messages'],'content':request['generation_content'],
        'template':request['prompt_template_id'],'task':request['task_type'].value,'model':config.model,
        'fallback':config.fallback_models,'sampling':[config.temperature,config.top_p,config.max_tokens],
        'scope':[context.user_id,context.project_id,context.api_key_level]}
    key=hashlib.sha256(json.dumps(legacy,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    async with routing.factory() as db:
        db.add(APICache(cache_key=key,response_content='wrong previously routed model',model_used='openai/selected'))
        await db.commit()
    result=await routing.api.generate(**request)
    assert not result.from_cache and result.model_used=='selected-reported'
    assert len(routing.requests)==1
    assert len(await rows(routing,APICache))==2
