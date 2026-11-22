"""Real disposable SQLite/cache/quota/Agents; only external AI adapters are simulated."""
import asyncio
import copy
import hashlib
import importlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.database import Base
from app.models import APICache, APIQuota, APICallLog, LanguageVocabulary
from app.core.api_scheduler.client import (UnifiedAPIClient, GenerationConfig,
    RequestContext, TaskType, AllModelsFailedError, _current_user_id)
from app.core.api_scheduler.adapters.base import BaseModelAdapter, AdapterResponse
from tests.test_study_notification_flow import flow


class SimulatedAdapter(BaseModelAdapter):
    provider = 'simulated'

    def __init__(self, marker):
        super().__init__('explicit-simulation-no-real-key')
        self.marker, self.calls, self.fail = marker, [], False
        self.content = None

    async def chat_completion(self, messages, config):
        self.calls.append({'messages':copy.deepcopy(messages),'config':vars(config).copy()})
        if self.fail:
            raise RuntimeError('explicit simulated provider failure')
        digest=hashlib.sha256(json.dumps(messages,ensure_ascii=False).encode()).hexdigest()[:10]
        content=self.content or json.dumps([{'word':self.marker+'-'+digest,
            'definition':'明确模拟AI输出','example_sentence':'simulated'}],ensure_ascii=False)
        return AdapterResponse(content,content,self.marker,tokens_input=7,tokens_output=11)

    def count_tokens(self, text):
        return len(text)//4


@pytest.fixture
async def cache_flow(flow, monkeypatch):
    import app.core.api_scheduler as api
    from app.core.api_scheduler import cache, client
    rate_limiter=importlib.import_module('app.core.api_scheduler.rate_limiter')
    engine=flow.factory.kw['bind']
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[APICache.__table__,APIQuota.__table__,
            APICallLog.__table__,LanguageVocabulary.__table__])
    monkeypatch.setattr(cache,'async_session',flow.factory)
    monkeypatch.setattr(rate_limiter,'async_session',flow.factory)
    monkeypatch.setattr(client,'rate_limiter',rate_limiter.RateLimiter())
    unified=UnifiedAPIClient()
    adapters={key:SimulatedAdapter(key) for key in ('primary','alternate','primary_alice','primary_bob')}
    unified._adapters.update(adapters)
    monkeypatch.setattr(api,'api_client',unified)
    token=_current_user_id.set('local_user')
    try:
        yield SimpleNamespace(**vars(flow),api=unified,adapters=adapters,engine=engine,
            database=Path(engine.url.database))
    finally:
        _current_user_id.reset(token)


def request():
    return dict(task_type=TaskType.CHAT,
        messages=[{'role':'system','content':'Respond as a tutor'}, {'role':'user','content':'x = A B'}],
        prompt_template_id='simulated.template',generation_content='x = A B',
        config=GenerationConfig(model='primary',max_retries=0),
        context=RequestContext(user_id='alice'))


async def rows(flow, model):
    async with flow.factory() as db:
        return (await db.scalars(select(model))).all()


VARIANTS=['system','history','role','order','user_case','user_space','content_case','content_space',
          'user','project','permission','task','template','model','fallback',
          'temperature','top_p','max_tokens','multimodal','message_name']


@pytest.mark.parametrize('variant',VARIANTS)
async def test_different_generation_requests_do_not_share_cache(cache_flow,variant):
    first=request(); second=copy.deepcopy(first)
    if variant=='system': second['messages'][0]['content']='Respond as an examiner'
    elif variant=='history': second['messages'].insert(1,{'role':'assistant','content':'Earlier answer'})
    elif variant=='role': second['messages'][0]['role']='user'
    elif variant=='order': second['messages'].reverse()
    elif variant=='user_case': second['messages'][1]['content']='x = a b'
    elif variant=='user_space': second['messages'][1]['content']='x = AB'
    elif variant=='content_case': second['generation_content']='x = a b'
    elif variant=='content_space': second['generation_content']='x = AB'
    elif variant=='user': second['context'].user_id='bob'
    elif variant=='project': second['context'].project_id='another-project'
    elif variant=='permission': second['context'].api_key_level='teacher'
    elif variant=='task': second['task_type']=TaskType.KNOWLEDGE_TREE
    elif variant=='template': second['prompt_template_id']='another.template'
    elif variant=='model': second['config'].model='alternate'
    elif variant=='fallback': second['config'].fallback_models=['alternate']
    elif variant=='temperature': second['config'].temperature=0.1
    elif variant=='top_p': second['config'].top_p=0.8
    elif variant=='max_tokens': second['config'].max_tokens=100
    elif variant=='multimodal': second['messages'][1]['content']=[{'type':'text','text':'different image context'}]
    elif variant=='message_name': second['messages'][1]['name']='another-author'
    a=await cache_flow.api.generate(**first)
    b=await cache_flow.api.generate(**second)
    assert not a.from_cache and not b.from_cache,variant
    assert len(await rows(cache_flow,APICache))==2
    assert len(await rows(cache_flow,APICallLog))==2
    assert sum(len(adapter.calls) for adapter in cache_flow.adapters.values())==2
    repeated=await cache_flow.api.generate(**copy.deepcopy(second))
    assert repeated.from_cache and repeated.content==b.content
    assert len(await rows(cache_flow,APICallLog))==2


async def test_identical_request_hits_persistent_cache_without_spending_quota(cache_flow):
    a=await cache_flow.api.generate(**request())
    b=await cache_flow.api.generate(**request())
    assert not a.from_cache and b.from_cache and a.content==b.content
    assert b.tokens_input==b.tokens_output==b.cost_estimate==0
    quota=(await rows(cache_flow,APIQuota))[0]
    assert quota.user_id=='alice' and quota.used_today==quota.total_used==18
    assert len(await rows(cache_flow,APICache))==len(await rows(cache_flow,APICallLog))==1


async def test_contextvar_user_is_resolved_before_cache_lookup(cache_flow):
    args=request(); args['context']=RequestContext()
    _current_user_id.set('alice')
    a=await cache_flow.api.generate(**copy.deepcopy(args))
    _current_user_id.set('bob')
    b=await cache_flow.api.generate(**copy.deepcopy(args))
    assert not b.from_cache and a.content!=b.content
    assert {q.user_id for q in await rows(cache_flow,APIQuota)}=={'alice','bob'}


async def test_async_contexts_do_not_reuse_each_others_result(cache_flow):
    await cache_flow.api.generate(**request())
    async def own(user):
        token=_current_user_id.set(user)
        try:
            args=request(); args['context']=RequestContext()
            return user,await cache_flow.api.generate(**args)
        finally:
            _current_user_id.reset(token)
    result=dict(await asyncio.gather(own('alice'),own('bob')))
    assert result['alice'].from_cache and not result['bob'].from_cache
    assert result['alice'].content!=result['bob'].content


async def test_normalization_task_does_not_receive_a_chat_cached_body(cache_flow):
    cache_flow.adapters['primary_alice'].content='intro\n```json\n[{"word":"simulated"}]\n```'
    args=request()
    chat=await cache_flow.api.generate(**args)
    args['task_type']=TaskType.KNOWLEDGE_TREE
    tree=await cache_flow.api.generate(**args)
    assert chat.content.startswith('intro')
    assert json.loads(tree.content)==[{'word':'simulated'}]
    assert not tree.from_cache


async def test_fallback_cache_preserves_saved_model_reference(cache_flow):
    cache_flow.adapters['primary_alice'].fail=True
    args=request(); args['config'].fallback_models=['alternate']
    a=await cache_flow.api.generate(**args)
    b=await cache_flow.api.generate(**args)
    assert a.model_used=='alternate' and b.model_used=='alternate(cache)'
    assert b.from_cache and a.content==b.content
    assert (await rows(cache_flow,APICache))[0].model_used=='alternate'
    assert len(cache_flow.adapters['alternate'].calls)==1


async def test_failures_leave_no_cache_and_can_recover(cache_flow):
    adapter=cache_flow.adapters['primary_alice']; adapter.fail=True
    with pytest.raises(AllModelsFailedError,match='explicit simulated'):
        await cache_flow.api.generate(**request())
    assert await rows(cache_flow,APICache)==[]
    assert not (await rows(cache_flow,APICallLog))[0].success
    adapter.fail=False
    result=await cache_flow.api.generate(**request())
    assert not result.from_cache and len(await rows(cache_flow,APICache))==1


async def test_expired_cache_is_replaced_using_real_database_timestamp(cache_flow):
    await cache_flow.api.generate(**request())
    async with cache_flow.factory() as db:
        await db.execute(update(APICache).values(created_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=31)))
        await db.commit()
    result=await cache_flow.api.generate(**request())
    assert not result.from_cache and len(cache_flow.adapters['primary_alice'].calls)==2
    assert len(await rows(cache_flow,APICache))==1


async def test_legacy_unscoped_entry_is_never_reused(cache_flow):
    from app.core.api_scheduler.cache import compute_cache_key,compute_config_hash
    args=request();cfg=args['config']
    old=compute_cache_key(args['generation_content'],args['prompt_template_id'],cfg.model,
        compute_config_hash(cfg.temperature,cfg.top_p,cfg.max_tokens))
    async with cache_flow.factory() as db:
        db.add(APICache(cache_key=old,response_content='legacy-other-user',model_used='primary'))
        await db.commit()
    result=await cache_flow.api.generate(**args)
    assert not result.from_cache and result.content!='legacy-other-user'
    assert len(await rows(cache_flow,APICache))==2


async def test_chat_wrapper_system_and_history_are_part_of_identity(cache_flow):
    a=await cache_flow.api.chat('same prompt',system='tutor',model='primary',context=RequestContext(user_id='alice'))
    b=await cache_flow.api.chat('same prompt',system='examiner',model='primary',context=RequestContext(user_id='alice'))
    c=await cache_flow.api.chat('same prompt',model='primary',context=RequestContext(user_id='alice'),
        messages=[{'role':'assistant','content':'Earlier simulated answer'}, {'role':'user','content':'same prompt'}])
    assert len({a,b,c})==3 and len(cache_flow.adapters['primary_alice'].calls)==3


async def test_cache_survives_independent_process_readback(cache_flow):
    await cache_flow.api.generate(**request())
    script="import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); r=c.execute('select model_used,response_content from api_cache').fetchall(); print(json.dumps(r))"
    result=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',script,str(cache_flow.database)],
        capture_output=True,text=True,check=False)
    assert result.returncode==0,result.stderr
    data=json.loads(result.stdout)
    assert len(data)==1 and data[0][0]==cache_flow.adapters['primary_alice'].marker and '明确模拟' in data[0][1]


async def test_equivalent_message_dictionary_order_still_hits(cache_flow):
    args=request()
    first=await cache_flow.api.generate(**args)
    args['messages']=[dict(reversed(list(message.items()))) for message in args['messages']]
    second=await cache_flow.api.generate(**args)
    assert second.from_cache and first.content==second.content
    assert len(cache_flow.adapters['primary_alice'].calls)==1


async def test_structured_fields_cannot_collide_at_string_boundaries(cache_flow):
    a=request();b=request()
    a.update(generation_content='ab',prompt_template_id='c')
    b.update(generation_content='a',prompt_template_id='bc')
    await cache_flow.api.generate(**a)
    second=await cache_flow.api.generate(**b)
    assert not second.from_cache and len(await rows(cache_flow,APICache))==2


async def test_transport_limits_do_not_invalidate_identical_generation(cache_flow):
    args=request()
    first=await cache_flow.api.generate(**args)
    args['config'].timeout=1;args['config'].max_retries=1
    second=await cache_flow.api.generate(**args)
    assert second.from_cache and first.content==second.content


async def test_new_client_reuses_only_same_owned_persistent_request(cache_flow):
    first=await cache_flow.api.generate(**request())
    reopened=UnifiedAPIClient()
    # Persistent reuse requires the same resolved provider configuration.
    reopened._adapters.update({key:copy.copy(adapter) for key,adapter in cache_flow.api._adapters.items()})
    result=await reopened.generate(**request())
    assert result.from_cache and result.content==first.content
    assert len(await rows(cache_flow,APICallLog))==1


@pytest.mark.parametrize('state',['valid','empty','expired'])
async def test_legacy_string_get_and_metadata_get_maintain_ttl_contract(cache_flow,state):
    from app.core.api_scheduler.cache import cache_manager
    content='' if state=='empty' else 'explicit simulated cached content'
    await cache_manager.set('simulated-key',content,'alternate',7,11)
    if state=='expired':
        async with cache_flow.factory() as db:
            await db.execute(update(APICache).values(created_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=31)))
            await db.commit()
    value=await cache_manager.get('simulated-key')
    entry=await cache_manager.get_entry('simulated-key')
    if state=='expired':
        assert value is None and entry is None and await rows(cache_flow,APICache)==[]
    else:
        assert value==content and entry.response_content==content and entry.model_used=='alternate'
        assert entry.tokens_input==7 and entry.tokens_output==11
