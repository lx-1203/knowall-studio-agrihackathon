"""Learning workbench integration checks use an isolated database and no paid AI."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models import Document, DocumentChunk, QuestionBank, AnswerRecord, ReviewSchedule, KnowledgeTree
from app.models.study_pack import StudyPack
from app.core import study_pack_service as service
from app.api import learning


@pytest.fixture
async def db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Document(id='doc', filename='教材.md', file_type='md', sha256='a' * 64, status='ready'))
        session.add_all([
            DocumentChunk(id='c1', doc_id='doc', chunk_index=0, text_content='# 第一章\n细胞结构', page_range='p1'),
            DocumentChunk(id='c2', doc_id='doc', chunk_index=1, text_content='# 最后一章\n遗传规律', page_range='p9'),
        ])
        await session.commit()
        yield session
    await engine.dispose()


async def fake_section(section, question_count, card_count, config):
    return {
        'notes': '本章讲解：' + section['title'],
        'questions': [{
            'question_type': 'single_choice', 'question_text': section['title'] + '练习',
            'options': [{'label': 'A', 'text': '正确项'}, {'label': 'B', 'text': '错误项'}],
            'answer': 'A', 'analysis': '根据本章原文可得。',
        } for _ in range(question_count)],
        'flashcards': [{'front': section['title'] + '是什么？', 'back': '本章的核心知识。'} for _ in range(card_count)],
    }


async def make_pack(db, user_id='u1', generator=fake_section, **changes):
    config = {'question_count': 4, 'card_count': 4, 'model': 'fake', 'concurrency': 2, 'max_section_chars': 6000,
              'question_types': ['single_choice'], **changes}
    return await service.generate_pack(db, 'doc', user_id, config, generator=generator)


async def test_pack_persists_last_chapter_and_links_existing_resources(db):
    result = await make_pack(db)
    assert result['coverage']['total_sections'] == 2
    assert result['coverage']['question_sections'] == 2
    assert '最后一章' in result['sections'][-1]['title']
    assert result['summary_id'] and result['tree_id'] and result['deck_id']
    tree = await db.get(KnowledgeTree, result['tree_id'])
    assert tree.tree_data['nodes'][0]['label']
    assert tree.tree_data['nodes'][0]['summary']
    stored = (await db.execute(select(QuestionBank))).scalars().all()
    assert {q.source_chunk_id for q in stored} == {'c1', 'c2'}


async def test_repeated_generation_reuses_pack_without_ai(db):
    initial = await make_pack(db)
    generator = AsyncMock(side_effect=AssertionError('should use saved pack'))
    cached = await make_pack(db, generator=generator)
    assert cached['id'] == initial['id']
    assert cached['from_cache']
    generator.assert_not_awaited()


async def test_source_changes_mark_old_pack_stale_and_bypass_cache(db):
    initial = await make_pack(db)
    chunk = await db.get(DocumentChunk, 'c2')
    chunk.text_content += '\n新知识'
    await db.commit()
    view = await service.get_latest_pack(db, 'doc', 'u1')
    assert view['stale']
    regenerated = await make_pack(db)
    assert regenerated['id'] != initial['id']
    assert not regenerated['stale']


async def test_one_section_failure_is_visible_and_retry_does_not_cache_it(db):
    async def fail_last(section, *args):
        if '最后' in section['title']:
            raise ValueError('invalid model response')
        return await fake_section(section, *args)
    partial = await make_pack(db, generator=fail_last)
    assert partial['coverage']['failed_sections'] == 1
    assert partial['sections'][-1]['errors']
    complete = await make_pack(db)
    assert complete['id'] != partial['id']
    assert complete['coverage']['failed_sections'] == 0


async def test_quality_preserves_invalid_questions_but_session_excludes_them(db):
    async def invalid(section, q, c, config):
        data = await fake_section(section, q, c, config)
        data['questions'][0]['options'] = []
        return data
    pack = await make_pack(db, generator=invalid)
    assert pack['quality']['blocked'] == 2
    session = await service.build_session(db, pack['id'], 'u1', limit=20, mode='practice')
    items = [x for x in session['items'] if x['kind'] == 'question']
    assert len(items) == 2
    assert all('answer' not in x['question'] and 'correct_answer' not in x['question'] for x in items)


async def test_invalid_generation_can_be_regenerated_instead_of_cached(db):
    async def invalid(section, q, c, config):
        data = await fake_section(section, q, c, config)
        data['questions'][0]['options'] = []
        return data
    broken = await make_pack(db, generator=invalid)
    fixed = await make_pack(db)
    assert fixed['id'] != broken['id']
    assert fixed['quality']['blocked'] == 0


async def test_markdown_export_keeps_choices_and_csv_neutralizes_formulas(db):
    pack = await make_pack(db)
    exported = await learning.export_pack(pack['id'], 'markdown', db, SimpleNamespace(id='u1'))
    text = exported.body.decode('utf-8')
    assert 'A. 正确项' in text and 'B. 错误项' in text
    qid = pack['sections'][0]['questions'][0]['id']
    await service.repair_question(db, pack['id'], qid, 'u1', {'question_text': '=1+2'})
    report = await learning.export_pack(pack['id'], 'csv', db, SimpleNamespace(id='u1'))
    assert "'=1+2" in report.body.decode('utf-8')


async def test_shared_flashcard_endpoints_do_not_expose_other_users_pack(db):
    from app.api.flashcards import get_due_cards, review_card, ReviewRequest, list_decks
    pack = await make_pack(db)
    due = await get_due_cards(limit=20, db=db, current_user=SimpleNamespace(id='u2'))
    assert due['cards'] == []
    decks = await list_decks(db=db, current_user=SimpleNamespace(id='u2'))
    assert decks == []
    card_id = pack['sections'][0]['flashcards'][0]['id']
    with pytest.raises(HTTPException) as exc:
        await review_card(ReviewRequest(card_id=card_id, rating=4), db=db, current_user=SimpleNamespace(id='u2'))
    assert exc.value.status_code == 404


async def test_deleting_a_question_is_reported_as_missing_in_pack(db):
    pack = await make_pack(db)
    qid = pack['sections'][0]['questions'][0]['id']
    await db.delete(await db.get(QuestionBank, qid))
    await db.commit()
    view = await service.get_latest_pack(db, 'doc', 'u1')
    missing = next(q for s in view['sections'] for q in s['questions'] if q['id'] == qid)
    assert not missing['quality']['eligible']
    assert 'missing_resource' in missing['quality']['issues']


async def test_repair_rechecks_quality_and_persists_to_bank(db):
    pack = await make_pack(db)
    question_id = pack['sections'][0]['questions'][0]['id']
    updated = await service.repair_question(db, pack['id'], question_id, 'u1', {
        'question_text': '修复题目', 'options': [{'label': 'A', 'text': '甲'}, {'label': 'B', 'text': '乙'}],
        'answer': 'Z', 'analysis': '解释',
    })
    assert not updated['quality']['eligible']
    question = await db.get(QuestionBank, question_id)
    assert question.correct_answer == 'Z'
    session = await service.build_session(db, pack['id'], 'u1', limit=20, mode='practice')
    assert question_id not in [i['question']['id'] for i in session['items'] if i['kind'] == 'question']


async def test_user_cannot_read_or_grade_another_users_pack(db):
    pack = await make_pack(db)
    assert await service.get_latest_pack(db, 'doc', 'u2') is None
    with pytest.raises(HTTPException) as exc:
        await service.build_session(db, pack['id'], 'u2', limit=10, mode='practice')
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException):
        await service.submit_answer(db, pack['id'], 'u2', pack['sections'][0]['questions'][0]['id'], 'A', 10)


async def test_wrong_answer_enters_smart_session_and_correct_redo_removes_it(db):
    pack = await make_pack(db)
    question_id = pack['sections'][0]['questions'][0]['id']
    wrong = await service.submit_answer(db, pack['id'], 'u1', question_id, 'B', 10)
    assert not wrong['is_correct'] and wrong['source']['chunk_ids']
    smart = await service.build_session(db, pack['id'], 'u1', limit=20, mode='smart')
    assert any(i['reason'] == 'wrong_answer' and i['question']['id'] == question_id for i in smart['items'] if i['kind'] == 'question')
    correct = await service.submit_answer(db, pack['id'], 'u1', question_id, 'A', 10)
    assert correct['is_correct']
    smart = await service.build_session(db, pack['id'], 'u1', limit=20, mode='smart')
    assert question_id not in [i['question']['id'] for i in smart['items'] if i['kind'] == 'question']
    answers = (await db.execute(select(AnswerRecord))).scalars().all()
    assert len(answers) == 2


async def test_flashcard_review_updates_fsrs_and_leaves_due_session(db):
    pack = await make_pack(db)
    card_id = pack['sections'][0]['flashcards'][0]['id']
    result = await service.review_pack_card(db, pack['id'], 'u1', card_id, 4)
    assert result['next_review_at']
    schedule = (await db.execute(select(ReviewSchedule).where(ReviewSchedule.card_id == card_id))).scalar_one()
    assert schedule.review_count == 1
    smart = await service.build_session(db, pack['id'], 'u1', limit=20, mode='smart')
    assert card_id not in [i['card']['id'] for i in smart['items'] if i['kind'] == 'flashcard']


async def test_stale_pack_cannot_silently_start_a_session(db):
    pack = await make_pack(db)
    chunk = await db.get(DocumentChunk, 'c1')
    chunk.text_content += '更新'
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await service.build_session(db, pack['id'], 'u1', limit=10, mode='practice')
    assert exc.value.status_code == 409


def test_generation_request_bounds_and_supported_types():
    with pytest.raises(ValueError):
        learning.GeneratePackRequest(document_id='doc', concurrency=99)
    with pytest.raises(ValueError):
        learning.GeneratePackRequest(document_id='doc', question_types=['made_up'])


async def test_deleted_resources_invalidate_generation_cache(db):
    pack = await make_pack(db)
    qid = pack['sections'][0]['questions'][0]['id']
    await db.delete(await db.get(QuestionBank, qid))
    await db.commit()
    regenerated = await make_pack(db)
    assert regenerated['id'] != pack['id']
    assert regenerated['quality']['blocked'] == 0
