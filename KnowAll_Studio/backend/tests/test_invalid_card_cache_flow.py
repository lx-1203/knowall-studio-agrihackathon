"""Real parser/cache/quota SQLite; model adapter explicitly simulated."""
import json
import pytest
from sqlalchemy import update
from app.core import memory
from app.models import APICache,APIQuota,APICallLog
from app.core.api_scheduler.client import AllModelsFailedError,_current_user_id
from tests.test_generation_cache_isolation_flow import cache_flow,flow,rows
from tests.test_generation_cache_isolation_flow import request
from app.core.api_scheduler.client import GenerationConfig,QuotaExceededError

GOOD={'front':'明确模拟问题内容','back':'明确模拟答案','card_type':'qa','tags':{'domain_tags':['模拟']},'hints':None}
BAD=[' ', 'not json', '[]', '{"cards":null}', '{"cards":{}}', '[null]', '[1]', '["bad"]',
     json.dumps([{**GOOD,'front':4}]),json.dumps([{**GOOD,'back':{}}]),
     json.dumps([{**GOOD,'tags':False}]),json.dumps([{**GOOD,'tags':['ok',1]}]),
     json.dumps([{**GOOD,'tags':{'domain_tags':'bad'}}]),json.dumps([{**GOOD,'hints':False}]),
     json.dumps([{**GOOD,'card_type':False}]),json.dumps([{**GOOD,'card_type':'cloze'}]),
     json.dumps([{**GOOD,'card_type':'unknown'}]),json.dumps([{**GOOD,'back':GOOD['front']}])]

@pytest.fixture
def native(cache_flow,monkeypatch):
    monkeypatch.setattr(memory,'api_client',cache_flow.api)
    _current_user_id.set('alice')
    return memory.FlashcardGenerator()

async def generate(native):return await native.generate('明确模拟同一知识','qa',2,'primary')

@pytest.mark.parametrize('bad',BAD)
async def test_invalid_paid_response_never_cached_and_same_request_recovers(native,cache_flow,bad):
    adapter=cache_flow.adapters['primary_alice'];adapter.content=bad
    try:await generate(native)
    except Exception:pass
    assert await rows(cache_flow,APICache)==[], 'invalid output poisoned retry cache'
    logs=await rows(cache_flow,APICallLog)
    assert len(logs)==1 and not logs[0].success and logs[0].tokens_input==7 and logs[0].tokens_output==11
    adapter.content=json.dumps([GOOD],ensure_ascii=False)
    cards=await generate(native);assert len(cards)==1 and cards[0]['front']==GOOD['front']
    assert len(await generate(native))==1 and len(adapter.calls)==2
    assert (await rows(cache_flow,APIQuota))[0].used_today==36
    assert [r.success for r in await rows(cache_flow,APICallLog)]==[False,True]

@pytest.mark.parametrize('bad',BAD[:8])
async def test_preexisting_bad_cache_is_evicted_and_recovered(native,cache_flow,bad):
    adapter=cache_flow.adapters['primary_alice'];adapter.content=json.dumps([GOOD])
    await generate(native)
    async with cache_flow.factory() as db:
        await db.execute(update(APICache).values(response_content=bad));await db.commit()
    cards=await generate(native)
    assert len(cards)==1 and len(adapter.calls)==2, 'bad cached entry prevented fresh provider retry'
    assert (await rows(cache_flow,APIQuota))[0].used_today==36
    assert len(await rows(cache_flow,APICache))==1

@pytest.mark.parametrize('kind',['qa','cloze','compare'])
async def test_partial_valid_output_and_optional_nulls_keep_usable_cards(native,cache_flow,kind):
    card={**GOOD,'card_type':kind,'tags':None}
    if kind=='cloze':card['front']='明确模拟 {{c1::答案}}'
    cache_flow.adapters['primary_alice'].content=json.dumps([None,{'front':3},card])
    result=await native.generate('部分合法','qa',2,'primary')
    assert len(result)==1 and result[0]['card_type']==kind
    assert result[0]['tags']==[] and result[0]['hints']==''
    assert len(await native.generate('部分合法','qa',2,'primary'))==1
    assert len(cache_flow.adapters['primary_alice'].calls)==1

async def test_json_repair_and_missing_type_remain_accepted(native,cache_flow):
    card={'front':GOOD['front'],'back':GOOD['back']}
    cache_flow.adapters['primary_alice'].content='broken prefix '+json.dumps(card)+' broken suffix'
    result=await generate(native);assert len(result)==1 and result[0]['card_type']=='qa'

@pytest.mark.parametrize('all_invalid',[False,True])
async def test_optional_validator_fallback_charges_each_paid_response(cache_flow,all_invalid):
    args=request();args['config']=GenerationConfig(model='primary',fallback_models=['alternate'],max_retries=0,
        response_validator=lambda body:body=='usable')
    cache_flow.adapters['primary_alice'].content='invalid'
    cache_flow.adapters['alternate'].content='invalid' if all_invalid else 'usable'
    if all_invalid:
        with pytest.raises(AllModelsFailedError):await cache_flow.api.generate(**args)
        assert await rows(cache_flow,APICache)==[]
    else:
        result=await cache_flow.api.generate(**args);assert result.content=='usable' and result.model_used=='alternate'
        assert (await cache_flow.api.generate(**args)).from_cache
    assert len(cache_flow.adapters['primary_alice'].calls)==len(cache_flow.adapters['alternate'].calls)==1
    assert (await rows(cache_flow,APIQuota))[0].used_today==36
    logs=await rows(cache_flow,APICallLog)
    assert len(logs)==2 and [l.success for l in logs]==([False,False] if all_invalid else [False,True])
    assert [l.model_name for l in logs]==['primary_alice','alternate']
    assert all(l.tokens_input==7 and l.tokens_output==11 for l in logs)

async def test_validator_exception_rejects_paid_content_and_leaves_no_slot_leak(cache_flow):
    def throws(content):raise ValueError('explicit simulated schema exception')
    args=request();args['config'].response_validator=throws
    with pytest.raises(AllModelsFailedError):await cache_flow.api.generate(**args)
    assert await rows(cache_flow,APICache)==[]
    logs=await rows(cache_flow,APICallLog);assert len(logs)==1 and not logs[0].success and logs[0].tokens_output==11
    args['config'].response_validator=None
    assert not (await cache_flow.api.generate(**args)).from_cache
    assert (await rows(cache_flow,APIQuota))[0].used_today==36

async def test_default_validator_keeps_existing_untyped_consumer_contract(cache_flow):
    cache_flow.adapters['primary_alice'].content='untyped response without JSON'
    first=await cache_flow.api.generate(**request());second=await cache_flow.api.generate(**request())
    assert first.content==second.content=='untyped response without JSON' and second.from_cache
    assert len(await rows(cache_flow,APICallLog))==1 and (await rows(cache_flow,APIQuota))[0].used_today==18

async def test_bad_cache_eviction_still_enforces_quota(native,cache_flow):
    cache_flow.adapters['primary_alice'].content=json.dumps([GOOD]);await generate(native)
    async with cache_flow.factory() as db:
        await db.execute(update(APICache).values(response_content='[]'))
        await db.execute(update(APIQuota).values(used_today=1000000));await db.commit()
    with pytest.raises(QuotaExceededError):await generate(native)
    assert await rows(cache_flow,APICache)==[]
    assert len(cache_flow.adapters['primary_alice'].calls)==1
