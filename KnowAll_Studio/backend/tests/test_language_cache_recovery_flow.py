"""Language consumer uses actual scheduler/cache/SQLite; AI explicitly simulated."""
import json,os,hashlib
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
import pytest
from app.models import APICache,APICallLog,APIQuota,LanguageVocabulary
from app.core.agents.language_agent import LanguageAgent
from tests.test_generation_cache_isolation_flow import cache_flow,flow,rows

VALID=[{'word':'energy','definition':'能量','part_of_speech':'noun'}]
INVALID=['not JSON','{}','null','1','[1]','[{"word":1}]','[{"word":"energy","definition":1}]',
    '[{"word":"energy","phonetic":null}]','[{"word":"energy","part_of_speech":[]}]',
    '[{"word":"energy","example_sentence":false}]','[]','[{"word":"  \\n "}]',
    json.dumps([{'word':'x'*129}]),json.dumps([{'word':'energy','phonetic':'x'*65}]),
    json.dumps([{'word':'energy','part_of_speech':'x'*33}]),
    json.dumps(VALID+[{'word':False}]),'prefix [not-json] suffix']

@pytest.fixture
async def language_flow(cache_flow,monkeypatch,request,tmp_path):
    f=cache_flow
    # These fixtures call only localhost simulators; bypass the user's proxy.
    monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost')
    monkeypatch.setenv('no_proxy','127.0.0.1,localhost')
    original=f.api.generate
    async def generate(**kwargs):
        kwargs['config'].max_retries=0
        kwargs['config'].fallback_models=[]
        return await original(**kwargs)
    monkeypatch.setattr(f.api,'generate',generate)
    f.api._adapters['deepseek-chat']=f.adapters['primary']
    root=Path(__file__).resolve().parents[1]
    paths=['app/core/agents/language_agent.py','app/core/api_scheduler/client.py','app/core/api_scheduler/cache.py','app/models/__init__.py','app/database.py','app/api/language.py']
    hashes=lambda:{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
    d={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),'SourceBefore':hashes(),
       'AI':'explicit simulated adapter; actual SQLite/scheduler, no live provider','DB':str(f.database)}
    f.evidence=d
    try:yield f
    finally:
        d['SourceAfter']=hashes();d['End']=datetime.now().astimezone().isoformat()
        d['AdapterCalls']=len(f.adapters['primary'].calls)
        await f.engine.dispose()
        for suffix in ('','-wal','-shm'):
            p=Path(str(f.database)+suffix)
            assert p.resolve().parent==tmp_path.resolve()
            if p.exists():p.unlink()
        d['DBRemoved']=not f.database.exists()
        out=Path(os.environ.get('KNOWALL_LANGUAGE_CACHE_EVIDENCE_DIR',str(tmp_path/'language-evidence')))
        out.mkdir(parents=True,exist_ok=True)
        (out/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')

async def run(f,content):
    f.adapters['primary'].content=content
    return await LanguageAgent().run(summary_id='summary',document_id='source')

@pytest.mark.parametrize('body',INVALID)
async def test_bad_vocabulary_not_cached_or_stored_then_same_request_recovers(language_flow,body):
    f=language_flow
    first=await run(f,body)
    f.evidence['First']={'Status':first.status,'Error':first.error}
    assert first.status=='error'
    assert await rows(f,LanguageVocabulary)==[]
    assert await rows(f,APICache)==[]
    logs=await rows(f,APICallLog);assert len(logs)==1 and not logs[0].success
    recovered=await run(f,json.dumps(VALID,ensure_ascii=False))
    assert recovered.status=='success' and recovered.result['total_words']==1
    cached=await run(f,'invalid response should never be used')
    assert cached.status=='success'
    assert len(f.adapters['primary'].calls)==2
    assert len(await rows(f,APICache))==1 and len(await rows(f,LanguageVocabulary))==1
    assert (await rows(f,APIQuota))[0].used_today==36
    f.evidence['Recovery']={'Status':recovered.status,'CachedStatus':cached.status,'Words':recovered.result['words'],'Quota':36}

@pytest.mark.parametrize('body',['not JSON','[]','[{"word":false}]',json.dumps([{'word':'x'*129}])])
async def test_legacy_bad_cache_evicted_and_mastered_word_preserved(language_flow,body):
    f=language_flow
    first=await run(f,json.dumps(VALID));assert first.status=='success'
    async with f.factory() as db:
        c=(await db.get(APICache,(await rows(f,APICache))[0].cache_key));c.response_content=body
        word=(await rows(f,LanguageVocabulary))[0];v=await db.get(LanguageVocabulary,word.id);v.mastered=True
        await db.commit()
    recovered=await run(f,json.dumps([*VALID,{'word':'world','definition':'世界'}]))
    assert recovered.status=='success' and recovered.result['total_words']==2
    assert len(f.adapters['primary'].calls)==2
    saved=await rows(f,LanguageVocabulary)
    assert next(w for w in saved if w.word=='energy').mastered
    assert next(w for w in saved if w.word=='energy').id==word.id
    assert len(await rows(f,APICache))==1
    f.evidence['RecoveredWords']=[{'Word':w.word,'ID':w.id,'Mastered':w.mastered} for w in saved]

@pytest.mark.parametrize('style',['json','wrapped','dedup','optional','limits','unicode'])
async def test_valid_language_contract_still_cached_and_persistent(language_flow,style):
    f=language_flow
    items=VALID
    if style=='dedup':items=[{'word':' '},*VALID,{'word':' energy ','definition':'duplicate'}]
    if style=='optional':items=[{'word':'term'}]
    if style=='limits':items=[{'word':'x'*128,'phonetic':'p'*64,'part_of_speech':'n'*32}]
    if style=='unicode':items=[{'word':'日本語é🙂','definition':'含[方括号]，合法'}]
    content=json.dumps(items,ensure_ascii=False)
    if style=='wrapped':content='说明：```json\n'+content+'\n```'
    first=await run(f,content);assert first.status=='success',first.error
    assert first.result['total_words']==1
    second=await run(f,'unused');assert second.status=='success'
    assert first.result['words'][0]['id']==second.result['words'][0]['id']
    assert len(f.adapters['primary'].calls)==1
    assert len(await rows(f,APICache))==1 and len(await rows(f,LanguageVocabulary))==1
    f.evidence['Stored']=first.result
