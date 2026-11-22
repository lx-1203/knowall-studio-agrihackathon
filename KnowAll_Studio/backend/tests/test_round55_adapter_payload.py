"""Actual urllib adapter thread -> localhost provider -> owned chat/SQL or native scheduler."""
import json, os, hashlib
from pathlib import Path
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select
from app.api import chat
from app.models import Conversation, Message, APICache, APIQuota, APICallLog
from app.core.assistant import Assistant, api_client
from app.core.api_scheduler.client import AllModelsFailedError
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from app.core.api_scheduler.adapters.anthropic import AnthropicAdapter
from tests.test_round53_chat_tenancy import tenancy, PATH
from tests.test_generation_cache_isolation_flow import cache_flow, rows, request as arguments
from tests.test_study_notification_flow import flow
from tests.test_reading_native_stream_http_flow import server

def payload(protocol,kind):
    if protocol=='openai':
        value={'choices':[{'message':{'content':'完整回答'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':7,'completion_tokens':11}}
        if kind.startswith('error-'):value['error']={'empty':{},'null':None,'false':False}[kind[6:]]
        if kind=='tool':value['choices'][0]['message']['tool_calls']=[{'id':'tool'}]
        if kind=='function':value['choices'][0]['message']['function_call']={'name':'tool'}
        if kind=='choices-string':value['choices']='bad'
        if kind=='choice-null':value['choices']=[None]
        if kind=='content-number':value['choices'][0]['message']['content']=7
        if kind=='usage-null':value['usage']=None
        if kind=='usage-number':value['usage']=7
    else:
        value={'content':[{'type':'text','text':'完整回答'}],'stop_reason':'end_turn',
            'usage':{'input_tokens':7,'output_tokens':11}}
        if kind.startswith('error-'):value['error']={'empty':{},'null':None,'false':False}[kind[6:]]
        if kind in ('tool','server-tool'):value['content'].append({'type':'tool_use' if kind=='tool' else 'server_tool_use','id':'tool'})
        if kind=='blocks-string':value['content']='bad'
        if kind=='block-null':value['content'].append(None)
        if kind=='content-number':value['content'][0]['text']=7
        if kind=='stop-sequence':value['stop_reason']='stop_sequence'
        if kind=='multi-text':value['content']=[{'type':'text','text':'完整'},{'type':'text','text':'回答'}]
    return value

CASES=[('openai',kind) for kind in ['error-empty','error-null','error-false','tool','function','choices-string','choice-null','content-number','usage-number','good','usage-null']]
CASES += [('anthropic',kind) for kind in ['error-empty','error-null','error-false','tool','server-tool','blocks-string','block-null','content-number','good','stop-sequence','multi-text']]

@pytest.mark.parametrize('protocol,kind',CASES)
async def test_native_adapter_payload_cannot_commit_bad_chat(tenancy,monkeypatch,protocol,kind,request):
    t=tenancy;provider=FastAPI();calls=[]
    @provider.post('/v1/chat/completions')
    @provider.post('/v1/messages')
    async def simulation(req:Request):calls.append(await req.json());return payload(protocol,kind)
    monkeypatch.setattr(chat.ai_assistant,'chat',Assistant.chat.__get__(chat.ai_assistant))
    async with server(provider) as remote:
        cls=OpenAICompatAdapter if protocol=='openai' else AnthropicAdapter
        adapter=cls('explicit-simulation',base_url=str(remote.base_url).rstrip('/')+'/v1',model_name='test')
        monkeypatch.setattr(api_client,'get_adapter',lambda *a,**kw:adapter)
        response=await t.client.post(PATH+'/assistant',headers=t.headers('alice'),json={'message':'ordinary private question'})
    good=kind in ('good','usage-null','stop-sequence','multi-text')
    async with t.factory() as db:
        conversations=(await db.scalars(select(Conversation))).all();messages=(await db.scalars(select(Message))).all()
    out=Path(os.environ['KNOWALL_READING_NATIVE_EVIDENCE_DIR']);out.mkdir(parents=True,exist_ok=True)
    proof={'test':request.node.nodeid,'status':response.status_code,'body':response.text,'calls':len(calls),
        'conversation_rows':len(conversations),'message_rows':len(messages),'scope':'native HTTP adapter + real JWT/chat/SQL; provider payload simulated'}
    (out/('adapter-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf8')
    assert len(calls)==1
    if good:assert response.status_code==200 and response.json()['message']=='完整回答' and len(messages)==2
    else:assert response.status_code==500 and not conversations and not messages,response.text

@pytest.mark.parametrize('protocol',['openai','anthropic'])
async def test_invalid_tool_payload_keeps_usage_and_never_falls_back(cache_flow,protocol):
    f=cache_flow;provider=FastAPI();calls=[]
    @provider.post('/v1/chat/completions')
    @provider.post('/v1/messages')
    async def simulation(req:Request):calls.append(await req.json());return payload(protocol,'tool')
    async with server(provider) as remote:
        cls=OpenAICompatAdapter if protocol=='openai' else AnthropicAdapter
        adapter=cls('explicit-simulation',base_url=str(remote.base_url).rstrip('/')+'/v1',model_name='test')
        f.api._adapters['primary_alice']=adapter
        args=arguments();args['config'].fallback_models=['alternate'];args['config'].max_retries=3
        try:await f.api.generate(**args)
        except AllModelsFailedError:pass
        else:pytest.fail('tool-bearing text cached as success')
    logs=await rows(f,APICallLog);quota=await rows(f,APIQuota)
    assert len(calls)==1 and not await rows(f,APICache)
    assert not f.adapters['alternate'].calls and len(logs)==1 and not logs[0].success
    assert quota[0].used_today==18 and logs[0].tokens_input==7 and logs[0].tokens_output==11
