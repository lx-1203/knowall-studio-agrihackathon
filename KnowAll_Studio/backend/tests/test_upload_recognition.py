"""Recognition must work with hash-named files and reject empty results."""
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import documents
from app.config import settings
from app.core.parsing import DocumentParser, ParsedDocument
from app.database import Base
from app.models import DocumentChunk
from sqlalchemy import select


@pytest.mark.asyncio
async def test_xlsx_recognizes_hash_named_upload(tmp_path):
    path = tmp_path / ('a' * 64)
    workbook = Workbook()
    workbook.active.append(['Course', 'Calculus'])
    workbook.save(path)
    parsed = await DocumentParser().parse(str(path), 'xlsx')
    assert 'Calculus' in parsed.text


@pytest.mark.asyncio
async def test_excel_upload_stores_recognized_chunks(tmp_path, monkeypatch):
    from app.core.glossary import extractor

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(settings, 'document_dir', str(tmp_path / 'uploads'))
    monkeypatch.setattr(settings, 'kaf_publish_enabled', False)
    monkeypatch.setattr(extractor, 'sync_glossary_from_db', skip_glossary)
    payload = BytesIO()
    workbook = Workbook()
    workbook.active.append(['Course', 'Calculus'])
    workbook.save(payload)
    payload.seek(0)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            result = await documents.upload_document(
                UploadFile(file=payload, filename='courses.xlsx'), db=db,
            )
            assert result['status'] == 'ready'
            assert result['total_chunks'] > 0
            chunks = (await db.execute(select(DocumentChunk))).scalars().all()
            assert any('Calculus' in chunk.text_content for chunk in chunks)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upload_rejects_empty_recognition(tmp_path, monkeypatch):
    async def empty_parse(*args):
        return ParsedDocument(text=' \n\t', page_count=1)

    monkeypatch.setattr(settings, 'document_dir', str(tmp_path / 'uploads'))
    monkeypatch.setattr(documents.parser, 'parse', empty_parse)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            with pytest.raises(HTTPException) as error:
                await documents.upload_document(
                    UploadFile(file=BytesIO(b'empty document'), filename='empty.txt'), db=db,
                )
            assert error.value.status_code == 400
    finally:
        await engine.dispose()
