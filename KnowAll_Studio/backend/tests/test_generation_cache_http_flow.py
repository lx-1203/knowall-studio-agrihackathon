"""Actual localhost REST/SSE with signed accounts and real LanguageAgent/cache DB."""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from tests.test_generation_cache_isolation_flow import cache_flow,rows
from tests.test_study_notification_flow import flow
from tests.test_pipeline_lifecycle_flow import localhost
from app.models import APICache,APIQuota,APICallLog,LanguageVocabulary


@pytest.mark.parametrize('stream',[False,True],ids=['rest','sse'])
async def test_real_http_language_generation_accounts_repeat_and_model(cache_flow,monkeypatch,stream):
    from app.api import agents
    from app.core.memory import coverage
    monkeypatch.setattr(coverage,'async_session',cache_flow.factory)
    app=cache_flow.client._transport.app
    app.include_router(agents.router)
    url='/api/v1/agents/orchestrate'+('/stream' if stream else '')
    payload={'summary_id':'summary','document_id':'source','agents':['language'],
             'language_type':'english','config':{'model':'primary','user_id':'forged'}}
    samples=[];started=datetime.now().astimezone().isoformat()
    async with localhost(app) as client:
        for user,model in [('alice','primary'),('bob','primary'),('bob','primary'),('bob','alternate')]:
            payload['config']['model']=model
            response=await client.post(url,headers=getattr(cache_flow,user),json=payload)
            assert response.status_code==200,response.text
            if stream:
                data=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                assert 'event: agent_error' not in response.text
                assert 'event: orchestrate_complete' in response.text
                result=data[-1]['results']['language']
            else:
                result=response.json()['results']['language']
            assert result['status']=='success',result
            assert result['result']['total_words']==1
            samples.append({'account':user,'model':model,'status_code':response.status_code,
                            'response':response.text,'result':result})
        origin=str(client.base_url)
    words=[sample['result']['result']['words'][0] for sample in samples]
    assert words[0]['word'].startswith('primary_alice-')
    assert words[1]['word'].startswith('primary_bob-')
    assert words[1]['id']==words[2]['id'] and words[0]['id']!=words[1]['id']
    assert words[3]['word'].startswith('alternate-')
    caches=await rows(cache_flow,APICache);quotas=await rows(cache_flow,APIQuota)
    logs=await rows(cache_flow,APICallLog);vocab=await rows(cache_flow,LanguageVocabulary)
    assert len(caches)==len(logs)==len(vocab)==3
    assert {row.user_id:row.used_today for row in quotas}=={'alice':18,'bob':36}
    assert all(row.user_id!='forged' for row in logs)
    evidence=Path(os.environ.get('KNOWALL_CACHE_EVIDENCE_DIR',str(Path(__file__).resolve().parents[2]/'docs/test-round-06b7')))
    evidence.mkdir(parents=True,exist_ok=True)
    value={'Start':started,'End':datetime.now().astimezone().isoformat(),'Origin':origin,
        'ExternalAI':'explicit simulated low-level adapter, no real provider network',
        'RequestCount':4,'Samples':samples,'DatabaseCounts':{'cache':len(caches),'logs':len(logs),'vocabulary':len(vocab)},
        'Quota':{row.user_id:row.used_today for row in quotas},
        'SourceHashes':{name:hashlib.sha256((Path(__file__).resolve().parents[1]/name).read_bytes()).hexdigest()
                       for name in ('app/core/api_scheduler/client.py','app/core/api_scheduler/cache.py')},
        'AdapterCalls':{key:adapter.calls for key,adapter in cache_flow.adapters.items()}}
    (evidence/('real-http-sse.json' if stream else 'real-http-rest.json')).write_text(
        json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def test_cloud_personal_workers_use_separate_databases(tmp_path):
    from app.core.cloud_workspaces import worker_environment
    alice=worker_environment(tmp_path/'alice','alice','explicit-simulated-secret',{})
    bob=worker_environment(tmp_path/'bob','bob','explicit-simulated-secret',{})
    assert alice['DATABASE_URL']!=bob['DATABASE_URL']
    assert alice['KNOWALL_WORKSPACE_OWNER']=='alice' and bob['KNOWALL_WORKSPACE_OWNER']=='bob'
    assert Path(alice['KNOWALL_DATA_ROOT']).name=='alice' and Path(bob['KNOWALL_DATA_ROOT']).name=='bob'
