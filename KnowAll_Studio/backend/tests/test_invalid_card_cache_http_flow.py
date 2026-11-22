"""Real signed localhost API + native model HTTP adapters + SQL/FSRS; AI simulated."""
import json,os,hashlib,copy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI,Request
from app.core import memory
from app.core.api_scheduler.client import UnifiedAPIClient
from app.core.agents.base import AgentRegistry
from app.core.agents.flashcard_agent import FlashcardAgent
from app.models import Deck,Flashcard,ReviewSchedule,KnowledgeCoverage,APICache,APIQuota,APICallLog
from tests.test_direct_generation_transaction_flow import direct,contract,flow,payload,size
from tests.test_generation_cache_isolation_flow import cache_flow,rows
from tests.test_pipeline_lifecycle_flow import localhost
from tests.test_historical_summary_generation_flow import historical,nodes
from tests.test_invalid_card_cache_flow import GOOD

@pytest.fixture
async def provider(direct,cache_flow,monkeypatch,request):
    from app.core.api_scheduler.adapters import openai,anthropic,http_utils
    import app.core.api_scheduler as api
    started=datetime.now().astimezone().isoformat();state=SimpleNamespace(content='{"cards":null}')
    app=FastAPI();calls=[];samples=[];blocked=[];unified=UnifiedAPIClient()
    @app.post('/{path:path}')
    async def simulated(req:Request,path:str):
        body=await req.json();calls.append({'path':path,'payload':copy.deepcopy(body),'simulation':True})
        if path=='anthropic/messages':
            return {'model':body['model']+'-reported','content':[{'type':'text','text':state.content}],
                    'usage':{'input_tokens':7,'output_tokens':11},'stop_reason':'end_turn'}
        assert path=='openai/chat/completions'
        return {'model':body['model']+'-reported','choices':[{'message':{'content':state.content},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':7,'completion_tokens':11}}
    monkeypatch.setattr(api,'api_client',unified);monkeypatch.setattr(memory,'api_client',unified)
    generator=memory.FlashcardGenerator();monkeypatch.setattr(memory.card_generator,'generate',generator.generate)
    async with localhost(app) as client:
        origin=str(client.base_url).rstrip('/')
        def only_local(url,headers,body,timeout):
            if not url.startswith(origin+'/'):
                blocked.append(url);raise RuntimeError('non-local AI blocked')
            return http_utils.sync_post(url,headers,body,timeout)
        monkeypatch.setattr(openai,'sync_post',only_local);monkeypatch.setattr(anthropic,'sync_post',only_local)
        for user in ('alice','bob'):
            for kind in ('openai','anthropic'):
                unified.configure_adapter(kind+'_'+user,'explicit-simulated-no-real-key',origin+'/'+kind,'simulated-card-model')
        try:
            yield SimpleNamespace(state=state,calls=calls,samples=samples,api=unified)
        finally:
            folder=Path(os.environ['KNOWALL_INVALID_CARD_EVIDENCE_DIR']);folder.mkdir(parents=True,exist_ok=True)
            root=Path(__file__).resolve().parents[1]
            value={'Test':request.node.nodeid,'Start':started,'End':datetime.now().astimezone().isoformat(),
                   'ProviderOrigin':origin,'ExternalAI':'explicit simulated localhost HTTP only',
                   'Calls':calls,'BackendSamples':samples,'BlockedEndpoints':blocked,
                   'SourceHashes':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in (
                       'app/core/api_scheduler/client.py','app/core/memory/__init__.py','app/api/flashcards.py',
                       'app/core/agents/flashcard_agent.py','app/core/agents/summary_nodes.py')}}
            (folder/('real-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

@pytest.mark.parametrize('kind',['openai','anthropic'])
async def test_direct_invalid_then_recovery_cache_and_other_account(direct,cache_flow,provider,kind):
    body=payload(count=2,model=kind)
    async with localhost(direct.app) as client:
        for phase,user in [('failure','alice'),('recovery','alice'),('cache','alice'),('other-account','bob')]:
            if phase!='failure':provider.state.content=json.dumps([GOOD,{**GOOD,'front':'第二个明确模拟问题'}])
            r=await client.post('/api/v1/flashcards/generate',headers=getattr(direct,user),json=body)
            provider.samples.append({'phase':phase,'account':user,'status':r.status_code,'body':r.json()})
            assert r.status_code==(500 if phase=='failure' else 200),r.text
            if phase=='failure':
                assert await rows(cache_flow,APICache)==[]
                for model in (Deck,Flashcard,ReviewSchedule):assert await size(direct,model)==0
            else:assert r.json()['generated_count']==2
    assert len(provider.calls)==3
    assert await size(direct,Flashcard)==await size(direct,ReviewSchedule)==6
    assert {q.user_id:q.used_today for q in await rows(cache_flow,APIQuota)}=={'alice':36,'bob':18}
    logs=await rows(cache_flow,APICallLog);assert [l.success for l in logs]==[False,True,True]
    assert all(l.tokens_input==7 and l.tokens_output==11 for l in logs)
    assert len(await rows(cache_flow,APICache))==2

@pytest.mark.parametrize('stream',[False,True],ids=['rest','sse'])
async def test_agent_historical_bad_output_recovers_without_business_residue(direct,cache_flow,provider,monkeypatch,stream):
    await historical(direct)
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:[FlashcardAgent()])
    path='/api/v1/agents/orchestrate'+('/stream' if stream else '')
    config={'model':'openai','card_count':3,'card_types':['qa'],'deck_name':'恢复模拟','enable_quality_review':False}
    async with localhost(direct.app) as client:
        for phase in ('failure','recovery','cache'):
            if phase!='failure':provider.state.content=json.dumps([GOOD,{**GOOD,'front':'第二个明确模拟问题'}])
            r=await client.post(path,headers=direct.alice,json={'summary_id':'summary','document_id':'source','agents':['flashcard'],'config':config})
            assert r.status_code==200,r.text
            values=[json.loads(l[6:]) for l in r.text.splitlines() if l.startswith('data: ')] if stream else []
            result=values[-1]['results']['flashcard'] if stream else r.json()['results']['flashcard']
            provider.samples.append({'phase':phase,'status':r.status_code,'result':result,'raw':r.text})
            assert result['status']==('error' if phase=='failure' else 'success'),result
            if phase=='failure':
                assert await rows(cache_flow,APICache)==[] and await nodes(direct)==[]
                for model in (Deck,Flashcard,ReviewSchedule,KnowledgeCoverage):assert await size(direct,model)==0
            else:assert result['result']['total_cards']==3
            assert len(provider.calls)==(1 if phase=='failure' else 4), 'repeat of all three topic batches must hit cache'
    # Give all three historical topic groups one card, independent of row order.
    assert len(provider.calls)==4 and len(await nodes(direct))==3
    assert await size(direct,Flashcard)==await size(direct,ReviewSchedule)==await size(direct,KnowledgeCoverage)==6
    assert [l.success for l in await rows(cache_flow,APICallLog)]==[False,True,True,True]
    assert (await rows(cache_flow,APIQuota))[0].used_today==72
