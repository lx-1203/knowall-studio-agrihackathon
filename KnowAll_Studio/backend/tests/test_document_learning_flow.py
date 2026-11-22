"""Real HTTP upload-to-practice flow; only external generation is simulated."""
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import pytest
from docx import Document as WordDocument
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import documents, learning
from app.config import settings
from app.core import study_pack_service
from app.core.auth import get_current_user
from app.database import Base, get_db
from app.models import AnswerRecord, Document


@pytest.fixture
async def flow(tmp_path, monkeypatch):
    from app.core.glossary import extractor
    monkeypatch.setattr(settings, 'document_dir', str(tmp_path / 'uploads'))
    monkeypatch.setattr(settings, 'kaf_publish_enabled', False)
    # Exercise bundled local parsers without optional Docling model downloads.
    monkeypatch.setattr(settings, 'use_docling', False)
    monkeypatch.setattr(extractor, 'sync_glossary_from_db', AsyncMock())
    monkeypatch.setattr(learning, 'load_user_api_keys', AsyncMock())
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        async def database():
            yield db
        app = FastAPI()
        app.include_router(documents.router)
        app.include_router(learning.router)
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='flow-user')
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://isolated') as client:
            yield client, db
    await engine.dispose()


def sample(extension):
    text = 'Mitochondria produce energy for living cells through cellular respiration.'
    stream = BytesIO()
    if extension == 'txt':
        return text.encode()
    if extension == 'docx':
        document = WordDocument()
        document.add_paragraph(text)
        document.save(stream)
    elif extension == 'xlsx':
        workbook = Workbook()
        workbook.active.append(['Biology', text])
        workbook.save(stream)
    elif extension == 'pdf':
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), text)
        stream.write(document.tobytes())
        document.close()
    elif extension == 'png':
        image = Image.new('RGB', (1800, 240), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 48)
        draw.text((40, 60), 'Mitochondria produce energy for living cells.', fill='black', font=font)
        image.save(stream, format='PNG')
    return stream.getvalue()


async def generated_section(section, questions, cards, config):
    assert config['_user_id'] == 'flow-user'
    return {'notes': 'Mitochondria produce energy.', 'questions': [
        {'question_type': 'single_choice', 'question_text': 'Which organelle produces energy?',
         'options': [{'label': 'A', 'text': 'Mitochondria'}, {'label': 'B', 'text': 'Cell wall'}],
         'answer': 'A', 'analysis': 'The uploaded source describes cellular respiration.'}
        for _ in range(questions)], 'flashcards': [
        {'front': 'What is the role of mitochondria?', 'back': 'They produce energy through respiration.'}
        for _ in range(cards)]}


@pytest.mark.parametrize('extension', ['txt', 'docx', 'xlsx', 'pdf', 'png'])
async def test_real_upload_generate_answer_export_and_reload(flow, monkeypatch, extension):
    client, db = flow
    monkeypatch.setattr(study_pack_service, 'generate_section', generated_section)
    payload = sample(extension)
    uploaded = await client.post('/api/v1/documents/upload', files={'file': ('biology.' + extension, payload)})
    assert uploaded.status_code == 200, uploaded.text
    document = uploaded.json()
    assert document['status'] == 'ready' and document['total_chunks'] > 0
    doc_id = document['document_id']
    duplicate = await client.post('/api/v1/documents/upload', files={'file': ('copy.' + extension, payload)})
    assert duplicate.json()['document_id'] == doc_id
    chunks = (await client.get(f'/api/v1/documents/{doc_id}/chunks')).json()
    assert any('Mitochondria' in item['text'] for item in chunks)
    preview = await client.get(f'/api/v1/learning/documents/{doc_id}/preview')
    assert preview.status_code == 200 and preview.json()['sections']
    config = {'document_id': doc_id, 'question_count': 1, 'card_count': 1, 'question_types': ['single_choice']}
    response = await client.post('/api/v1/learning/generate', json=config)
    assert response.status_code == 200, response.text
    pack = response.json()
    assert pack['coverage']['failed_sections'] == 0
    assert pack['quality']['blocked'] == 0
    assert set(pack['sections'][0]['chunk_ids']).issubset({c['id'] for c in chunks})
    session = await client.post(f"/api/v1/learning/packs/{pack['id']}/session", json={'mode': 'practice'})
    question = next(i['question'] for i in session.json()['items'] if i['kind'] == 'question')
    assert 'answer' not in question and 'correct_answer' not in question
    feedback = await client.post(f"/api/v1/learning/packs/{pack['id']}/answers", json={
        'question_id': question['id'], 'answer': 'A', 'time_spent_ms': 1234})
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()['is_correct']
    records = (await db.execute(select(AnswerRecord))).scalars().all()
    assert len(records) == 1 and records[0].question_id == question['id']
    for format in ['json', 'markdown', 'csv']:
        export = await client.get(f"/api/v1/learning/packs/{pack['id']}/export", params={'format': format})
        assert export.status_code == 200 and 'Which organelle produces energy?' in export.text
        assert 'attachment' in export.headers['content-disposition']
    db.expire_all()
    restored = await client.get(f'/api/v1/learning/documents/{doc_id}/pack')
    assert restored.json()['id'] == pack['id']
    cached = await client.post('/api/v1/learning/generate', json=config)
    assert cached.json()['from_cache'] and cached.json()['id'] == pack['id']


@pytest.mark.parametrize('filename,payload', [('empty.txt', b''), ('broken.docx', b'not a zip'), ('file.exe', b'data')])
async def test_bad_upload_never_creates_ready_document(flow, filename, payload):
    client, db = flow
    response = await client.post('/api/v1/documents/upload', files={'file': (filename, payload)})
    assert response.status_code == 400, response.text
    assert settings.document_dir not in response.text
    assert not (await db.execute(select(Document).where(Document.status == 'ready'))).scalars().all()


async def test_failed_generation_is_visible_and_retry_is_not_cached(flow, monkeypatch):
    client, _ = flow
    uploaded = await client.post('/api/v1/documents/upload', files={'file': ('biology.txt', sample('txt'))})
    config = {'document_id': uploaded.json()['document_id'], 'question_count': 1, 'card_count': 1}
    monkeypatch.setattr(study_pack_service, 'generate_section', AsyncMock(side_effect=RuntimeError('provider unavailable')))
    failed = (await client.post('/api/v1/learning/generate', json=config)).json()
    assert failed['coverage']['failed_sections'] > 0
    assert failed['sections'][0]['errors']
    practice = await client.post(f"/api/v1/learning/packs/{failed['id']}/session", json={'mode': 'practice'})
    assert practice.json()['items'] == []
    monkeypatch.setattr(study_pack_service, 'generate_section', generated_section)
    recovered = (await client.post('/api/v1/learning/generate', json=config)).json()
    assert recovered['id'] != failed['id'] and recovered['coverage']['failed_sections'] == 0
