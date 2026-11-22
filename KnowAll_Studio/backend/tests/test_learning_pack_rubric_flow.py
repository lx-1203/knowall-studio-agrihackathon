"""Learning-pack rubric flow in private SQLite; generation is explicitly simulated."""
import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import learning
from app.core import study_pack_service as service
from app.core.auth import create_access_token
from app.core.learning_workbench import grade_interaction, question_quality
from app.database import Base, get_db
from app.models import User, Document, DocumentChunk, QuestionBank, AnswerRecord, ErrorLog
from app.models.study_pack import StudyPack


CONFIG = dict(question_count=1, card_count=0, model='explicit-simulation', concurrency=1,
              max_section_chars=6000, question_types=['fill_blank'])


def question(kind='fill_blank'):
    if kind == 'fill_blank':
        return dict(question_type=kind, question_text='元素两空', options=[], answer='氢;氧', analysis='参考原文',
                    blanks=[{'index': 1, 'answer': '氢', 'hint': '首项'}, {'index': 2, 'answer': '氧'}])
    return dict(question_type=kind, question_text='传输协议评分点', options=[], answer='完整参考答案', analysis='参考原文',
                scoring_points=[{'point': '可靠传输', 'keywords': ['reliable'], 'score': 3},
                                {'point': '流量控制', 'keywords': ['flow'], 'score': 1}])


@pytest.fixture
async def flow(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'learning.sqlite').as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([User(id=name, username=name, email=name+'@09b3.invalid', password_hash='fixture-only')
                    for name in ('alice', 'bob')])
        db.add(Document(id='doc', filename='隔离学习资料.md', file_type='md', sha256='09b3', status='ready'))
        await db.flush()
        db.add(DocumentChunk(id='chunk', doc_id='doc', chunk_index=0, text_content='# 元素\n氢和氧，可靠传输与流量控制', page_range='p1'))
        await db.commit()
    async def database():
        async with factory() as db:
            yield db
    app = FastAPI()
    app.include_router(learning.router)
    app.dependency_overrides[get_db] = database
    model = AsyncMock(return_value=SimpleNamespace(content=json.dumps(
        {'notes': '本章学习讲解', 'questions': [question()], 'flashcards': []}, ensure_ascii=False)))
    from app.core.api_scheduler import api_client
    monkeypatch.setattr(api_client, 'generate', model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://private-learning') as client:
        yield SimpleNamespace(factory=factory, client=client, app=app, model=model,
                              alice={'Authorization': 'Bearer '+create_access_token('alice')},
                              bob={'Authorization': 'Bearer '+create_access_token('bob')})
    await engine.dispose()


async def make(flow, raw=None, **config):
    async def simulated(*args):
        return {'notes': '本章学习讲解', 'questions': copy.deepcopy(raw if raw is not None else [question()]), 'flashcards': []}
    async with flow.factory() as db:
        return await service.generate_pack(db, 'doc', 'alice', {**CONFIG, **config}, generator=simulated)


async def inject_rules(flow, pack, raw):
    """Seed legacy JSON too, so privacy/repair fail independently of generation loss."""
    async with flow.factory() as db:
        stored = await db.get(StudyPack, pack['id'])
        sections = copy.deepcopy(stored.sections)
        q = sections[0]['questions'][0]
        for field in ('blanks', 'scoring_points'):
            q[field] = copy.deepcopy(raw.get(field, []))
        bank = await db.get(QuestionBank, q['id'])
        bank.blanks, bank.scoring_points = q['blanks'], q['scoring_points']
        stored.sections = sections
        await db.commit()
    return q


@pytest.mark.parametrize('kind,partial,ratio,full', [
    ('fill_blank', '氢;错', .5, '氢;氧'), ('short_answer', 'reliable', .75, 'reliable flow'),
    ('term_definition', 'reliable', .75, 'reliable flow')])
async def test_rules_survive_pack_sql_cache_and_partial_smart_review(flow, kind, partial, ratio, full):
    raw = question(kind)
    pack = await make(flow, [raw], question_types=[kind])
    q = pack['sections'][0]['questions'][0]
    async with flow.factory() as db:
        bank = await db.get(QuestionBank, q['id'])
        for field in ('blanks', 'scoring_points'):
            assert q[field] == raw.get(field, [])
            assert getattr(bank, field) == raw.get(field, [])
        cached = await service.generate_pack(db, 'doc', 'alice', {**CONFIG, 'question_types': [kind]},
                                             generator=AsyncMock(side_effect=AssertionError('cache must bypass AI')))
        assert cached['from_cache'] and cached['id'] == pack['id']
        response = await service.submit_answer(db, pack['id'], 'alice', q['id'], partial)
        assert response['score_ratio'] == ratio and response['is_correct'] is False
        smart = await service.build_session(db, pack['id'], 'alice')
        assert smart['items'][0]['reason'] == 'wrong_answer'
        assert (await db.scalar(select(ErrorLog).where(ErrorLog.question_id == q['id']))).error_count == 1
        assert (await service.submit_answer(db, pack['id'], 'alice', q['id'], full))['is_correct']
        assert (await service.build_session(db, pack['id'], 'alice'))['items'] == []
        records = (await db.scalars(select(AnswerRecord))).all()
        assert {r.user_answer: r.is_correct for r in records} == {partial: False, full: True}


@pytest.mark.parametrize('mode', ['practice', 'smart'])
async def test_practice_question_does_not_expose_blank_answers_or_point_keywords(flow, mode):
    pack = await make(flow)
    q = await inject_rules(flow, pack, question())
    async with flow.factory() as db:
        if mode == 'smart':
            await service.submit_answer(db, pack['id'], 'alice', q['id'], '错')
        session = await service.build_session(db, pack['id'], 'alice', mode=mode)
        public = session['items'][0]['question']
        assert not ({'answer', 'correct_answer', 'analysis', 'blanks', 'scoring_points'} & public.keys())


BAD_RULES = [('blanks', {}), ('blanks', [{'index': '1', 'answer': '氢'}]),
             ('blanks', [{'index': -1, 'answer': '氢'}]), ('blanks', [{'index': True, 'answer': '氢'}]),
             ('scoring_points', {}), ('scoring_points', [{'point': '点', 'keywords': '答案', 'score': 1}]),
             ('scoring_points', [{'point': '点', 'keywords': ['答案'], 'score': -1}]),
             ('scoring_points', [{'point': '点', 'keywords': ['答案'], 'score': float('inf')}])]


@pytest.mark.parametrize('field,bad', BAD_RULES)
async def test_bad_generated_rule_is_isolated_without_losing_notes_or_good_questions(flow, field, bad):
    broken = {**question(), field: bad}
    pack = await make(flow, [broken, question()], question_count=2)
    section = pack['sections'][0]
    assert section['notes'] == '本章学习讲解'
    assert len(section['questions']) == 1 and section['errors']
    async with flow.factory() as db:
        assert len((await db.scalars(select(QuestionBank))).all()) == 1
        assert (await db.get(StudyPack, pack['id'])).cache_key is None


@pytest.mark.parametrize('field,bad', BAD_RULES[:3] + BAD_RULES[4:6])
async def test_bad_old_json_is_blocked_in_quality_session_answer_and_cache(flow, field, bad):
    pack = await make(flow)
    raw = {**question(), field: bad}
    q = await inject_rules(flow, pack, raw)
    assert 'invalid_rubric' in question_quality(q)['issues']
    async with flow.factory() as db:
        assert (await service.build_session(db, pack['id'], 'alice', mode='practice'))['items'] == []
        with pytest.raises(HTTPException) as error:
            await service.submit_answer(db, pack['id'], 'alice', q['id'], '氢;氧')
        assert error.value.status_code == 409
        updated = await service.generate_pack(db, 'doc', 'alice', CONFIG,
            generator=AsyncMock(return_value={'notes': '本章学习讲解', 'questions': [question()], 'flashcards': []}))
        assert updated['id'] != pack['id'] and not updated['from_cache']


@pytest.mark.parametrize('change', ['text', 'same_answer', 'new_answer'])
async def test_repair_preserves_rules_except_when_aggregate_answer_changes(flow, change):
    pack = await make(flow)
    q = await inject_rules(flow, pack, question())
    changes = {'question_text': '修改题干'} if change == 'text' else {'answer': '新答案' if change == 'new_answer' else q['answer']}
    async with flow.factory() as db:
        repaired = await service.repair_question(db, pack['id'], q['id'], 'alice', changes)
        bank = await db.get(QuestionBank, q['id'])
        assert repaired['blanks'] == ([] if change == 'new_answer' else question()['blanks'])
        assert bank.blanks == repaired['blanks'] and bank.scoring_points == repaired['scoring_points']
        assert (await db.get(StudyPack, pack['id'])).cache_key is None
        if change == 'new_answer':
            assert (await service.submit_answer(db, pack['id'], 'alice', q['id'], '新答案'))['score_ratio'] == 1
            assert (await service.submit_answer(db, pack['id'], 'alice', q['id'], '氢;氧'))['score_ratio'] == 0


async def test_v1_generation_cache_is_not_reused_after_rule_format_upgrade(flow):
    pack = await make(flow)
    async with flow.factory() as db:
        stored = await db.get(StudyPack, pack['id'])
        stored.cache_key = hashlib.sha256(json.dumps(['alice', stored.source_hash, CONFIG, 'learning.v1'],
                                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        await db.commit()
    new = await make(flow)
    assert new['id'] != pack['id'] and not new['from_cache']


@pytest.mark.parametrize('metadata', [{}, {'blanks': None, 'scoring_points': None}])
def test_legacy_optional_metadata_retains_existing_full_answer_grading(metadata):
    q = dict(question_type='short_answer', question_text='旧题', answer='正确', **metadata)
    assert question_quality(q)['eligible']
    assert grade_interaction(q, ' 正 确 ')['score_ratio'] == 1
    assert grade_interaction(q, '错误')['score_ratio'] == 0


async def test_native_json_generation_prompt_save_and_http_practice(flow):
    generated = await flow.client.post('/api/v1/learning/generate', headers=flow.alice,
                                       json={'document_id': 'doc', **CONFIG})
    assert generated.status_code == 200, generated.text
    pack = generated.json()
    q = pack['sections'][0]['questions'][0]
    prompt = flow.model.await_args.kwargs['generation_content']
    assert 'blanks' in prompt and 'scoring_points' in prompt
    response = await flow.client.post('/api/v1/learning/packs/'+pack['id']+'/session', headers=flow.alice, json={'mode': 'practice'})
    assert response.status_code == 200
    assert 'blanks' not in response.json()['items'][0]['question']
    scored = await flow.client.post('/api/v1/learning/packs/'+pack['id']+'/answers', headers=flow.alice,
                                   json={'question_id': q['id'], 'answer': '氢;错'})
    assert scored.status_code == 200 and scored.json()['score_ratio'] == .5
    for method, suffix, payload in [('post', '/session', {'mode': 'practice'}),
                                    ('post', '/answers', {'question_id': q['id'], 'answer': '氢;氧'}),
                                    ('patch', '/questions/'+q['id'], {'question_text': '攻击', 'answer': '攻击'}),
                                    ('get', '/export?format=json', None)]:
        denied = await flow.client.request(method, '/api/v1/learning/packs/'+pack['id']+suffix, headers=flow.bob, json=payload)
        assert denied.status_code == 404, denied.text
    reread = await flow.client.get('/api/v1/learning/documents/doc/pack', headers=flow.alice)
    assert reread.json()['sections'][0]['questions'][0]['blanks'] == question()['blanks']
