"""Actual signed REST/SSE -> Agents -> real adapters -> explicitly simulated local AI HTTP."""
import json
import os
import hashlib
from pathlib import Path
from datetime import datetime

import pytest
from tests.test_model_routing_flow import routing,cache_flow,flow,rows
from tests.test_pipeline_lifecycle_flow import localhost
from app.models import APICache,APICallLog,APIQuota,LanguageVocabulary


@pytest.mark.parametrize('stream',[False,True],ids=['rest','sse'])
@pytest.mark.parametrize('protocol',['openai','anthropic'])
async def test_real_http_selected_model_reaches_provider_and_repeat_cache(routing,monkeypatch,stream,protocol):
    from app.api import agents
    from app.core.memory import coverage
    monkeypatch.setattr(coverage,'async_session',routing.factory)
    for user in ('alice','bob'):
        routing.api.configure_adapter(protocol+'_'+user,'explicit-simulated-'+user,routing.origin+'/'+protocol,'registered')
    app=routing.client._transport.app;app.include_router(agents.router)
    path='/api/v1/agents/orchestrate'+('/stream' if stream else '')
    samples=[];started=datetime.now().astimezone().isoformat()
    async with localhost(app) as client:
        for user,name in [('alice','selected'),('alice','selected'),('bob','another')]:
            ref=protocol+'/'+name
            payload={'summary_id':'summary','document_id':'source','agents':['mindmap','language'],
                'language_type':'english','config':{'model':ref,'force_restructure':True,'user_id':'forged'}}
            response=await client.post(path,headers=getattr(routing,user),json=payload)
            assert response.status_code==200,response.text
            if stream:
                assert 'event: agent_error' not in response.text
                events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                result=events[-1]['results']
                assert 'event: orchestrate_complete' in response.text
            else:result=response.json()['results']
            assert all(result[agent]['status']=='success' for agent in ('mindmap','language')),result
            assert result['mindmap']['result']['llm_restructured']
            assert result['language']['result']['total_words']==1
            assert result['mindmap']['result']['nodes'][0]['label']=='模拟-'+name
            assert result['language']['result']['words'][0]['word']=='simulated-'+name
            samples.append({'Account':user,'Model':ref,'StatusCode':response.status_code,'Response':response.text,
                            'Result':result,'ProviderCallsSoFar':len(routing.requests)})
        backend_origin=str(client.base_url)
    assert [sample['ProviderCallsSoFar'] for sample in samples]==[2,2,4]
    assert {request['payload']['model'] for request in routing.requests}=={'selected','another'}
    assert all(request['path']==protocol+('/messages' if protocol=='anthropic' else '/chat/completions') for request in routing.requests)
    assert not routing.blocked
    assert [call['credential_marker'] for call in routing.requests]==[
        'explicit-simulated-alice','explicit-simulated-alice','explicit-simulated-bob','explicit-simulated-bob']
    assert samples[0]['Result']['language']['result']['words'][0]['id']==samples[1]['Result']['language']['result']['words'][0]['id']
    caches=await rows(routing,APICache);logs=await rows(routing,APICallLog);quotas=await rows(routing,APIQuota)
    assert len(caches)==len(logs)==4
    assert {entry.model_used for entry in caches}=={'selected-reported','another-reported'}
    assert {entry.model_name for entry in logs}=={'selected-reported','another-reported'}
    assert {quota.user_id:quota.used_today for quota in quotas}=={'alice':36,'bob':36}
    assert len(await rows(routing,LanguageVocabulary))==2
    folder=Path(os.environ.get('KNOWALL_MODEL_EVIDENCE_DIR',str(Path(__file__).resolve().parents[2]/'docs/test-round-06b8')))
    value={'Start':started,'End':datetime.now().astimezone().isoformat(),'BackendOrigin':backend_origin,
        'ProviderOrigin':routing.origin,'BackendRequestCount':3,'ProviderRequestCount':4,'Samples':samples,
        'ProviderRequests':routing.requests,'ExternalAI':'explicit simulated localhost provider, real adapters/protocol HTTP',
        'DatabaseCounts':{'cache':4,'log':4,'vocabulary':2},'Quota':{'alice':36,'bob':36},
        'Hashes':{path:hashlib.sha256((Path(__file__).resolve().parents[1]/path).read_bytes()).hexdigest() for path in (
            'app/core/api_scheduler/client.py','app/core/agents/mindmap_agent.py','app/core/agents/language_agent.py')}}
    folder.mkdir(parents=True,exist_ok=True)
    (folder/('real-agent-'+protocol+('-sse.json' if stream else '-rest.json'))).write_text(
        json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
