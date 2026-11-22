"""The existing quiz entry points must obey the new source/quality rules."""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models import Document, DocumentChunk, QuestionBank
from app.core.documents import load_document_chunks
from app.api.questions import GenerateQuestionsRequest, generate_questions, CreateExamRequest, create_exam
from app.api.interactive_quiz import start_interactive_quiz


@pytest.fixture
async def session():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([Document(id='doc', filename='book.md', file_type='md', sha256='a' * 64),
                    Document(id='other', filename='other.md', file_type='md', sha256='b' * 64)])
        db.add_all([DocumentChunk(id='head', doc_id='doc', chunk_index=0, text_content='# 第一章\n' + '细胞 ' * 4000),
                    DocumentChunk(id='tail', doc_id='doc', chunk_index=1, text_content='# 尾章\n遗传末尾'),
                    DocumentChunk(id='foreign', doc_id='other', chunk_index=0, text_content='其他资料')])
        await db.commit()
        yield db
    await engine.dispose()


async def test_existing_generation_reaches_tail_and_keeps_source_ids(session, monkeypatch):
    from app.api import questions as routes
    monkeypatch.setattr(routes, 'load_user_api_keys', AsyncMock())
    seen = []
    async def generate(text, config, model):
        seen.append(text)
        return [{'question_text': '哪一项正确？', 'options': [{'label': 'A', 'text': '甲'}, {'label': 'B', 'text': '乙'}],
                 'answer': 'A', 'type': 'single_choice'} for _ in range(config.count)]
    monkeypatch.setattr(routes.quiz_generator, 'generate', generate)
    result = await generate_questions(GenerateQuestionsRequest(document_id='doc', count=8), session, None)
    assert any('遗传末尾' in text for text in seen)
    assert len(result['questions']) == 8
    assert {q['source_chunk_id'] for q in result['questions']} == {'head', 'tail'}
    assert result['coverage']['uncovered_sections'] == 0


async def test_selected_chunks_cannot_escape_document(session):
    chunks = await load_document_chunks(session, 'doc', ['head', 'foreign'])
    assert [c.id for c in chunks] == ['head']


async def _seed_quality(db):
    db.add_all([QuestionBank(id='good', question_type='single_choice', question_text='正确题', correct_answer='A',
                            options=[{'label': 'A', 'text': '甲'}, {'label': 'B', 'text': '乙'}]),
                QuestionBank(id='bad', question_type='single_choice', question_text='缺选项题', correct_answer='A', options=[])])
    await db.commit()


async def test_existing_exam_excludes_incomplete_question(session):
    await _seed_quality(session)
    exam = await create_exam(CreateExamRequest(question_ids=['good', 'bad']), session)
    assert exam['question_count'] == 1
    assert exam['excluded_question_ids'] == ['bad']


async def test_existing_interactive_quiz_excludes_incomplete_question(session, monkeypatch):
    from app.api import interactive_quiz as routes
    monkeypatch.setattr(routes, 'load_user_api_keys', AsyncMock())
    await _seed_quality(session)
    quiz = await start_interactive_quiz('unknown-summary', count=10, db=session, current_user=None)
    assert [q['id'] for q in quiz['questions']] == ['good']


async def test_enhanced_exam_structured_questions_receive_partial_credit():
    from app.core.quiz import exam_engine
    q = dict(id='match', question_type='matching', question_text='配对概念',
             options=[{'label': 'A', 'text': '甲', 'choices': ['一', '二']}, {'label': 'B', 'text': '乙', 'choices': ['一', '二']}],
             answer='{"A":"一","B":"二"}')
    result = await exam_engine.grade_enhanced({'questions': [q]}, {'match': '{"A":"一","B":"一"}'}, enable_semantic=False)
    assert result['details'][0]['partial_score'] == 0.5


async def test_invalid_only_generated_section_is_not_playable_coverage():
    from app.core.section_generation import generate_section_questions
    from app.core.quiz import QuizGenerationConfig
    generator = AsyncMock()
    generator.generate.return_value = [{'question_text': '缺少选项', 'answer': 'A', 'question_type': 'single_choice', 'options': []}]
    questions, coverage, errors = await generate_section_questions(
        [{'id': 'c', 'chunk_index': 0, 'text_content': '# 章节\n正文'}], QuizGenerationConfig(count=1), 'fake', generator)
    assert len(questions) == 1
    assert coverage['covered_sections'] == 0
    assert coverage['uncovered_sections'] == 1


def test_local_exam_structured_answers_ignore_json_key_order():
    from app.core.quiz import exam_engine
    q = dict(id='match', question_type='matching', question_text='配对概念',
             options=[{'label': 'A', 'text': '甲', 'choices': ['一', '二']}, {'label': 'B', 'text': '乙', 'choices': ['一', '二']}],
             answer='{"A":"一","B":"二"}')
    result = exam_engine.grade({'questions': [q]}, {'match': '{"B":"二", "A":"一"}'})
    assert result['details'][0]['is_correct']
