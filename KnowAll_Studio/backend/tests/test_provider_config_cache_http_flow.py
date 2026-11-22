"""Actual backend + native adapters/local simulated provider; no external AI."""
import json,os,hashlib
from pathlib import Path
from datetime import datetime
import pytest
from app.models import APICache,APIQuota,APICallLog
from tests.test_model_routing_flow import routing,cache_flow,flow,rows,args
from tests.test_pipeline_lifecycle_flow import localhost


def evidence(name,start,samples,provider,**extra):
    out=Path(os.environ['KNOWALL_CONFIG_EVIDENCE_DIR']);out.mkdir(parents=True,exist_ok=True)
    backend=Path(__file__).resolve().parents[1]
    (out/name).write_text(json.dumps({'Start':start,'End':datetime.now().astimezone().isoformat(),
        'BackendRequestCount':len(samples),'Samples':samples,'ProviderRequests':provider,
        'ProviderRequestCount':len(provider),'ExternalAI':'explicit simulated localhost provider; actual native adapters',
        'SourceHashes':{p:hashlib.sha256((backend/p).read_bytes()).hexdigest() for p in ('app/core/api_scheduler/client.py','app/core/auth.py','app/api/user.py')},**extra},ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.parametrize('field',['key','url','model','protocol'])
async def test_native_http_config_changes_miss_and_same_config_reload_hits(routing,field):
    start=datetime.now().astimezone().isoformat()
    provider='deepseek' if field=='protocol' else 'openai'
    old={'api_key':'explicit-simulated-key-A','base_url':routing.origin+('/deepseek/v1' if field=='protocol' else '/openai'),'model_name':'registered-A'}
    def configure(values):routing.api.configure_adapter(provider+'_alice',**values)
    configure(old)
    first=await routing.api.generate(**args(provider))
    assert (await routing.api.generate(**args(provider))).from_cache
    new={**old,{'key':'api_key','url':'base_url','model':'model_name','protocol':'base_url'}[field]:
        {'key':'explicit-simulated-key-B','url':routing.origin+'/deepseek/v1','model':'registered-B','protocol':routing.origin+'/deepseek/anthropic'}[field]}
    configure(new)
    second=await routing.api.generate(**args(provider));assert not second.from_cache
    configure(new)
    assert (await routing.api.generate(**args(provider))).from_cache
    assert len(routing.requests)==2 and not routing.blocked
    assert len(await rows(routing,APICache))==len(await rows(routing,APICallLog))==2
    assert (await rows(routing,APIQuota))[0].used_today==36
    evidence('native-config-'+field+'.json',start,[],routing.requests,
        ProviderOrigin=routing.origin,FirstModel=first.model_used,SecondModel=second.model_used,CacheEntries=2)


@pytest.mark.parametrize('stream',[False,True],ids=['rest','sse'])
async def test_actual_signed_key_rotation_then_generation_reloads_and_isolates_cache(routing,monkeypatch,stream):
    from app.api import agents,user
    from app.core.memory import coverage
    from app.core.api_scheduler.adapters import openai,http_utils
    monkeypatch.setattr(coverage,'async_session',routing.factory)
    app=routing.client._transport.app;app.include_router(agents.router);app.include_router(user.router)
    redirected=[]
    def simulated_default_endpoint(url,headers,payload,timeout):
        assert url=='https://api.openai.com/v1/chat/completions','unexpected endpoint blocked: '+url
        redirected.append({'Simulation':'default endpoint redirected to isolated localhost','model':payload['model']})
        return http_utils.sync_post(routing.origin+'/openai/chat/completions',headers,payload,timeout)
    monkeypatch.setattr(openai,'sync_post',simulated_default_endpoint)
    start=datetime.now().astimezone().isoformat();samples=[]
    path='/api/v1/agents/orchestrate'+('/stream' if stream else '')
    payload={'summary_id':'summary','document_id':'source','agents':['language'],'language_type':'english',
        'config':{'model':'openai/selected','user_id':'forged'}}
    async with localhost(app) as client:
        async def key(account,marker):
            response=await client.post('/api/v1/user/keys',headers=getattr(routing,account),json={'provider':'openai','api_key':'explicit-simulated-'+marker})
            assert response.status_code==201,response.text
            samples.append({'Action':'save-key','Account':account,'Status':response.status_code,'Response':response.json()})
        await key('alice','alice-A');await key('bob','bob-A')
        async def generate(account,expected):
            response=await client.post(path,headers=getattr(routing,account),json=payload)
            assert response.status_code==200,response.text
            result=[json.loads(l[6:]) for l in response.text.splitlines() if l.startswith('data: ')][-1]['results']['language'] if stream else response.json()['results']['language']
            assert result['status']=='success',result
            assert len(routing.requests)==expected
            samples.append({'Action':'generate','Account':account,'Status':response.status_code,'Result':result,'Raw':response.text,'ProviderCallsSoFar':expected})
        await generate('alice',1);await generate('alice',1)
        await generate('bob',2);await generate('bob',2)
        await key('alice','alice-B')
        await generate('alice',3);await generate('alice',3);await generate('bob',3)
        # Cold client reloads encrypted DB keys through the actual auth helper.
        import app.core.api_scheduler as api
        from app.core.api_scheduler.client import UnifiedAPIClient
        reopened=UnifiedAPIClient();monkeypatch.setattr(api,'api_client',reopened)
        await generate('bob',3);await generate('alice',3)
        assert 'openai_alice' in reopened._adapters and 'openai_bob' in reopened._adapters
        backend_origin=str(client.base_url)
    assert len(redirected)==3
    assert len(await rows(routing,APICache))==len(await rows(routing,APICallLog))==3
    assert {q.user_id:q.used_today for q in await rows(routing,APIQuota)}=={'alice':36,'bob':18}
    assert [p['credential_marker'] for p in routing.requests]==['explicit-simulated-alice-A','explicit-simulated-bob-A','explicit-simulated-alice-B']
    evidence('signed-keys-'+('sse' if stream else 'rest')+'.json',start,samples,routing.requests,BackendOrigin=backend_origin,
        ProviderOrigin=routing.origin,RedirectedDefaultEndpoint=redirected,CacheEntries=3,Quota={'alice':36,'bob':18})
