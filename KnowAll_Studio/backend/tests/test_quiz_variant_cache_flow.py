"""Native quiz parser/cache/quota; external AI adapter is explicitly simulated."""
import json
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import update
from app.core import quiz
from app.core.api_scheduler.client import _current_user_id, AllModelsFailedError
from app.models import APICache, APICallLog, APIQuota
from tests.test_generation_cache_isolation_flow import cache_flow, flow, rows

GOOD = {'id':'q_1','type':'fill_blank','question_text':'模拟变式元素___和___',
        'blanks':[{'index':1,'answer':'氢'},{'index':2,'answer':'氧'}],
        'answer':'氢;氧','analysis':'明确模拟解析'}
BAD = ['not json', 'null', '4', '[]', '{"questions":null}', '{"questions":{}}',
       '[null,4,"bad"]', json.dumps({'questions':[{**GOOD,'blanks':{}}]}),
       json.dumps({'questions':[{**GOOD,'question_text':''}]}),
       json.dumps({'questions':[{**GOOD,'type':'short_answer'}]})]

@pytest.mark.parametrize('wrap',['object','array','fence','surround'])
def test_supported_json_envelopes_keep_rules(wrap):
    content=json.dumps([GOOD],ensure_ascii=False)
    if wrap=='object':content=json.dumps({'questions':[GOOD]},ensure_ascii=False)
    if wrap=='fence':content='```json\n'+content+'\n```'
    if wrap=='surround':content='以下是题目：\n'+content+'\n结束'
    result=quiz.QuizGenerator()._parse_questions(content)
    assert len(result)==1 and result[0]['blanks']==GOOD['blanks']
    assert result[0]['question_type']=='fill_blank'

@pytest.mark.parametrize('bad',BAD[:-1])
def test_bad_shapes_and_rubrics_are_not_usable(bad):
    assert quiz.QuizGenerator()._parse_questions(bad)==[]

def test_partial_valid_batch_and_blank_only_answer_recovery():
    q={k:v for k,v in GOOD.items() if k!='answer'}
    result=quiz.QuizGenerator()._parse_questions(json.dumps({'questions':[None,4,q]}))
    assert len(result)==1 and result[0]['answer']=='氢;氧'

@pytest.mark.parametrize('change',[{'type':{}},{'answer':{}},{'answer':['氢','氧']},
    {'difficulty_score':'broken'},{'difficulty_score':float('nan')},{'difficulty_score':True},
    {'tags':'broken'},{'tags':[4]},{'options':'broken'}])
def test_wrong_field_types_cannot_be_treated_as_usable_questions(change):
    assert quiz.QuizGenerator()._parse_questions(json.dumps({'questions':[{**GOOD,**change}]}))==[]

@pytest.fixture
def generator(cache_flow,monkeypatch):
    _current_user_id.set('alice')
    native=cache_flow.api.generate
    async def no_retry(**kwargs):
        kwargs['config'].max_retries=0
        return await native(**kwargs)
    monkeypatch.setattr(quiz,'api_client',type('Client',(),{'generate':staticmethod(no_retry)})())
    result=quiz.QuizGenerator()
    async def keep(items,*args):return items
    monkeypatch.setattr(result,'_review_and_refine',keep)
    return result

async def generate(g,entry='normal'):
    config=quiz.QuizGenerationConfig(question_type='fill_blank',count=1,enable_review=False)
    if entry=='variant':return await g.generate_variants(GOOD,1,model='primary')
    if entry=='relations':return await g.generate_with_relations('模拟知识',config,'关系',model='primary')
    if entry=='cross':return await g.generate_cross_topic({'id':'a','title':'氢'},{'id':'b','title':'氧'},'analogous_to','模拟关系',model='primary')
    return await g.generate('模拟知识',config,model='primary')

@pytest.mark.parametrize('bad',BAD)
async def test_bad_paid_output_is_uncached_and_same_request_recovers(generator,cache_flow,bad):
    adapter=cache_flow.adapters['primary_alice'];adapter.content=bad
    try:await generate(generator)
    except Exception:pass
    assert await rows(cache_flow,APICache)==[]
    logs=await rows(cache_flow,APICallLog)
    assert len(logs)==1 and logs[0].success is False
    assert (logs[0].tokens_input,logs[0].tokens_output)==(7,11)
    adapter.content=json.dumps({'questions':[GOOD]},ensure_ascii=False)
    assert len(await generate(generator))==1
    assert len(await generate(generator))==1 and len(adapter.calls)==2
    assert (await rows(cache_flow,APIQuota))[0].used_today==36

@pytest.mark.parametrize('entry',['normal','variant','relations','cross'])
async def test_old_invalid_cache_is_evicted_for_every_generation_entry(generator,cache_flow,entry):
    adapter=cache_flow.adapters['primary_alice'];adapter.content=json.dumps({'questions':[GOOD]})
    assert len(await generate(generator,entry))==1
    async with cache_flow.factory() as db:
        await db.execute(update(APICache).values(response_content='{"questions":null}'));await db.commit()
    assert len(await generate(generator,entry))==1
    assert len(adapter.calls)==2 and len(await rows(cache_flow,APICache))==1

async def test_variant_prompt_contains_original_rules_without_reusing_as_new_rules(generator,cache_flow):
    cache_flow.adapters['primary_alice'].content=json.dumps({'questions':[GOOD]})
    await generator.generate_variants({**GOOD,'blanks':[{'index':1,'answer':'原题独有答案'}]},1,model='primary')
    messages=cache_flow.adapters['primary_alice'].calls[0]['messages']
    assert '原题独有答案' in messages[-1]['content']

async def test_invalid_revision_is_uncached_and_never_replaces_valid_question(generator,cache_flow):
    adapter=cache_flow.adapters['primary_alice'];adapter.content=json.dumps({'revised_questions':[{**GOOD,'blanks':{}}]})
    try:result=await generator._revise_question(GOOD,{'decision':'revise'},quiz.QuizGenerationConfig(question_type='fill_blank'),'primary')
    except AllModelsFailedError:result=None
    assert result is None and await rows(cache_flow,APICache)==[]
    adapter.content=json.dumps({'revised_questions':[GOOD]})
    result=await generator._revise_question(GOOD,{'decision':'revise'},quiz.QuizGenerationConfig(question_type='fill_blank'),'primary')
    assert result['question_type']=='fill_blank' and result['blanks']==GOOD['blanks']

CHOICES=[{'label':'A','text':'第一种传输方案'},{'label':'B','text':'第二种传输方案'}]
@pytest.mark.parametrize('kind,answer',[('single_choice','Z'),('multi_choice',['A','Z']),('single_choice',['A','B'])])
def test_unknown_or_invalid_choice_labels_are_not_usable(kind,answer):
    q={'question_type':kind,'question_text':'模拟选择题','options':CHOICES,'answer':answer}
    assert quiz.QuizGenerator()._parse_questions(json.dumps({'questions':[q]}))==[]

@pytest.mark.parametrize('kind,answer',[('single_choice','A'),('multi_choice',['A','B']),('true_false',False),
    ('fill_blank','氢;氧'),('short_answer','reliable flow'),('term_definition','reliable flow')])
def test_current_six_menu_types_keep_compatible_answer_forms(kind,answer):
    q={'question_type':kind,'question_text':'模拟现有菜单题型','answer':answer}
    if kind in {'single_choice','multi_choice'}:q['options']=CHOICES
    if kind=='fill_blank':q['blanks']=GOOD['blanks']
    result=quiz.QuizGenerator()._parse_questions(json.dumps({'questions':[q]}))
    assert len(result)==1 and result[0]['answer']==answer
