"""Disposable real SQL/scheduler/book consumers; only AI is simulated."""
import asyncio,copy,hashlib,importlib,json,os
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
import pytest
from app.core.book.compiler import BookCompiler
from app.core.book.models import Book
from app.core.book.block_generators import generate_quiz_block
from app.core.api_scheduler.adapters.base import AdapterResponse
from app.core.api_scheduler.client import _current_user_id
from app.models import APICache,APICallLog,APIQuota,DocumentChunk
from tests.test_generation_cache_isolation_flow import cache_flow,flow,rows

OUTLINE={'subtitle':'模拟教材','chapters':[{'title':'细胞基础','description':'可靠章节','sections':[{'title':'能量转换','description':'可靠节'}]}]}
QUIZ={'questions':[{'question':'哪种细胞器？','type':'single_choice','options':[{'label':'A','text':'线粒体'},{'label':'B','text':'核'}],'answer':'A','explanation':'明确模拟解释'}, {'question':'能量词___','type':'fill_blank','answer':'ATP'}, {'question':'解释转换','type':'short_answer','answer':'转化能量'}]}
def dump(x):return json.dumps(x,ensure_ascii=False)
def outline(**kw):return {**copy.deepcopy(OUTLINE),**kw}
def question(**kw):return {'questions':[{**copy.deepcopy(QUIZ['questions'][0]),**kw}]}
BAD_OUTLINE=['not json','null','4','[]',dump({}),dump(outline(chapters={})),dump(outline(chapters=[])),dump(outline(chapters=[None])),dump(outline(chapters=['bad'])),dump(outline(chapters=[{'title':4,'sections':[{'title':'s'}]}])),dump(outline(chapters=[{'title':'ch','description':[],'sections':[{'title':'s'}]}])),dump(outline(chapters=[{'title':'ch','sections':{}}])),dump(outline(chapters=[{'title':'ch','sections':[None]}])),dump(outline(chapters=[{'title':'ch','sections':[{'title':{},'description':'s'}]}])),dump(outline(chapters=[{'title':'ch','sections':[{'title':'s','description':None}]}])),dump(outline(subtitle=['bad']))]
BAD_QUIZ=['not json','null','4','[]',dump({}),dump({'questions':[]}),dump({'questions':{}}),dump({'questions':[None]}),dump(question(question={})),dump(question(question=' ')),dump(question(answer=[])),dump(question(answer='')),dump(question(type='multiple_choice')),dump(question(type=[])),dump(question(explanation=5)),dump(question(options={})),dump(question(options=[None])),dump(question(options=[{'label':'A','text':{}}])),dump(question(options=[{'label':2,'text':'text'}])),dump(question(options=[{'label':'A','text':' '}])) ,dump(question(options=[{'label':'A','text':'first'},{'label':'A','text':'second'}])),dump(question(answer='C')),dump(question(options=[]))]
SOURCE_PATHS=['app/core/book/compiler.py','app/core/book/block_generators.py','app/core/book/models.py','app/api/books.py','app/core/api_scheduler/client.py','app/core/api_scheduler/cache.py','app/core/api_scheduler/rate_limiter.py','app/core/api_scheduler/adapters/openai.py','app/core/auth.py','app/core/generation_access.py','app/database.py','app/config.py','app/models/__init__.py','app/models/user.py','tests/test_book_structured_cache_flow.py','tests/test_book_structured_native_flow.py','tests/test_generation_cache_isolation_flow.py','tests/test_study_notification_flow.py','tests/test_reading_native_stream_http_flow.py']
def hashes():
    root=Path(__file__).resolve().parents[1]
    return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in SOURCE_PATHS if (root/p).exists()}

@pytest.fixture
async def book_flow(cache_flow,monkeypatch,request):
    import app.database as dbmodule
    import app.core.book.compiler as module
    from app.core.api_scheduler import client
    f=cache_flow;compiler=BookCompiler();outputs={'outline':dump(OUTLINE),'quiz':dump(QUIZ),'text':'明确模拟教材正文','callout':'明确模拟学习重点'}
    async with f.engine.begin() as conn:await conn.run_sync(DocumentChunk.__table__.create,checkfirst=True)
    async with f.factory() as db:
        db.add(DocumentChunk(id='book-source',doc_id='source',chunk_index=0,text_content='临时来源：细胞能量转换。',content_hash='isolated-book'));await db.commit()
    monkeypatch.setattr(module,'book_compiler',compiler)
    def kind(prompt):return 'outline' if '教材策划' in prompt else 'quiz' if '测试题' in prompt else 'callout' if '一句话概括' in prompt else 'text'
    for name in ('primary_alice','primary_bob'):
        adapter=f.adapters[name]
        async def simulated(messages,config,a=adapter):
            prompt=messages[-1]['content'];a.calls.append({'Kind':kind(prompt),'Messages':copy.deepcopy(messages),'Config':vars(config).copy()})
            content=outputs[kind(prompt)];return AdapterResponse(content,content,'explicit-book-simulation',tokens_input=7,tokens_output=11)
        monkeypatch.setattr(adapter,'chat_completion',simulated)
    original=f.api.generate
    async def actual_generate(**kwargs):
        cfg=kwargs['config'];cfg=copy.copy(cfg);cfg.model='primary';cfg.max_retries=0;cfg.fallback_models=[];kwargs['config']=cfg
        return await original(**kwargs)
    monkeypatch.setattr(f.api,'generate',actual_generate)
    monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost');monkeypatch.setenv('no_proxy','127.0.0.1,localhost')
    token=_current_user_id.set('alice')
    d={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),'SourceBefore':hashes(),'AI':'explicit simulation only; real scheduler/SQL/book','DB':str(f.database)}
    try:yield SimpleNamespace(**vars(f),compiler=compiler,outputs=outputs,evidence=d,kind=kind)
    finally:
        await compiler.close();_current_user_id.reset(token)
        d['End']=datetime.now().astimezone().isoformat();d['SourceAfter']=hashes();d['AdapterCalls']={k:len(v.calls) for k,v in f.adapters.items()}
        await f.engine.dispose()
        for suffix in ('','-wal','-shm'):
            p=Path(str(f.database)+suffix);assert p.resolve().parent==f.database.resolve().parent
            if p.exists():p.unlink()
        d['DBRemoved']=not f.database.exists()
        out=Path(os.environ.get('KNOWALL_BOOK_EVIDENCE_DIR',str(f.database.parent/'evidence')));out.mkdir(parents=True,exist_ok=True)
        (out/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')

async def consume(f,kind):
    return await f.compiler._ideate(Book(id='same',title='相同教材'), '临时细胞资料') if kind=='outline' else await generate_quiz_block('能量转换','临时細胞资料','细胞基础')
def acceptable(value,kind):return value.subtitle=='模拟教材' if kind=='outline' else value==QUIZ

@pytest.mark.parametrize('bad',BAD_OUTLINE)
async def test_invalid_outline_is_not_success_cache_and_same_request_recovers(book_flow,bad):await invalid_recovery(book_flow,'outline',bad)
@pytest.mark.parametrize('bad',BAD_QUIZ)
async def test_invalid_quiz_is_not_success_cache_and_same_request_recovers(book_flow,bad):await invalid_recovery(book_flow,'quiz',bad)
async def invalid_recovery(f,kind,bad):
    f.outputs[kind]=bad;result=await consume(f,kind)
    assert not acceptable(result,kind)
    assert await rows(f,APICache)==[], 'invalid structured content was cached as success'
    assert not (await rows(f,APICallLog))[0].success
    f.outputs[kind]=dump(OUTLINE if kind=='outline' else QUIZ)
    assert acceptable(await consume(f,kind),kind) and acceptable(await consume(f,kind),kind)
    assert len(f.adapters['primary_alice'].calls)==2
    logs=await rows(f,APICallLog);quota=(await rows(f,APIQuota))[0]
    assert len(await rows(f,APICache))==1 and [x.success for x in logs]==[False,True] and quota.used_today==quota.total_used==36
    f.evidence['SQL']={'Cache':1,'Logs':[x.success for x in logs],'Used':quota.used_today,'Provider':2}

@pytest.mark.parametrize('kind',['outline','quiz'])
@pytest.mark.parametrize('bad',['null','shape','typed'])
async def test_saved_bad_cache_is_evicted_and_same_prompt_repaired(book_flow,kind,bad):
    f=book_flow;assert acceptable(await consume(f,kind),kind)
    content='null' if bad=='null' else (dump({'chapters':[None]}) if kind=='outline' else dump({'questions':[None]})) if bad=='shape' else (dump(outline(subtitle={})) if kind=='outline' else dump(question(answer={})))
    async with f.factory() as db:
        c=await db.get(APICache,(await rows(f,APICache))[0].cache_key);c.response_content=content;await db.commit()
    assert acceptable(await consume(f,kind),kind) and acceptable(await consume(f,kind),kind)
    assert len(f.adapters['primary_alice'].calls)==2 and len(await rows(f,APICache))==1
    assert (await rows(f,APIQuota))[0].used_today==36
    f.evidence['SQL']={'Cache':1,'Logs':[x.success for x in await rows(f,APICallLog)],'Used':36,'Provider':2}

@pytest.mark.parametrize('kind',['outline','quiz'])
@pytest.mark.parametrize('wrapped',[False,True])
async def test_valid_structure_three_types_and_fenced_json_are_cached(book_flow,kind,wrapped):
    f=book_flow;raw=dump(OUTLINE if kind=='outline' else QUIZ);f.outputs[kind]='```json\n'+raw+'\n```' if wrapped else raw
    assert acceptable(await consume(f,kind),kind) and acceptable(await consume(f,kind),kind)
    assert len(f.adapters['primary_alice'].calls)==1 and len(await rows(f,APICache))==1 and (await rows(f,APIQuota))[0].used_today==18

async def test_optional_titles_and_quiz_type_preserve_existing_defaults(book_flow):
    f=book_flow;f.outputs['outline']=dump({'chapters':[{'sections':[{}]}]});p=await consume(f,'outline');assert p.chapters==[{'sections':[{}]}]
    f.outputs['quiz']=dump({'questions':[{'question':'旧短答','answer':'旧答案'}]});assert await consume(f,'quiz')=={'questions':[{'question':'旧短答','answer':'旧答案'}]}

async def test_empty_sections_remain_a_terminal_error_not_fallback_completion(book_flow):
    f=book_flow;f.outputs['outline']=dump({'chapters':[{'title':'无节','sections':[]}]});b=await f.compiler.create_book('相同教材',source_doc_ids=['source'],user_id='alice')
    await asyncio.gather(f.compiler._compilations[b.id]);assert b.status.value=='error' and '没有可编译' in b.error and b.progress<1

@pytest.mark.parametrize('kind',['outline','quiz'])
async def test_transport_failure_is_not_silently_a_structured_fallback(book_flow,monkeypatch,kind):
    from app.core.api_scheduler.client import AllModelsFailedError
    async def failure(messages,config):raise RuntimeError('explicit book transport failure')
    monkeypatch.setattr(book_flow.adapters['primary_alice'],'chat_completion',failure)
    with pytest.raises(AllModelsFailedError,match='explicit book transport failure'):await consume(book_flow,kind)
    assert await rows(book_flow,APICache)==[]
    assert (await rows(book_flow,APICallLog))[0].success is False

@pytest.mark.parametrize('kind',['outline','quiz'])
async def test_bad_cached_structure_cannot_bypass_exhausted_quota(book_flow,kind):
    from app.core.api_scheduler.client import QuotaExceededError
    f=book_flow;await consume(f,kind)
    async with f.factory() as db:
        quota=(await rows(f,APIQuota))[0]
        quota=await db.get(APIQuota,quota.id);quota.daily_limit=0
        c=await db.get(APICache,(await rows(f,APICache))[0].cache_key);c.response_content='null';await db.commit()
    with pytest.raises(QuotaExceededError):await consume(f,kind)
    assert await rows(f,APICache)==[] and len(f.adapters['primary_alice'].calls)==1
    assert (await rows(f,APIQuota))[0].used_today==18

@pytest.mark.parametrize('kind',['fill_blank','short_answer'])
@pytest.mark.parametrize('changes',[{'options':None},{'answer':True},{'explanation':{}}])
async def test_nonchoice_fields_obey_same_cache_contract(book_flow,kind,changes):
    await invalid_recovery(book_flow,'quiz',dump({'questions':[{'question':'明确模拟题干','type':kind,'answer':'可靠答案',**changes}]}))
