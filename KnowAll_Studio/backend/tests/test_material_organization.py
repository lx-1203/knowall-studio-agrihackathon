"""Material organization persists independently of parsed source and study packs."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import documents
from app.core.auth import get_current_user
from app.database import Base, get_db
from app.models import Document


@pytest.fixture
async def material_client():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add(Document(id='biology', filename='生物.md', sha256='a' * 64, file_type='md',
                        status='ready', metadata_={'headings': [{'text': '细胞'}], 'image_count': 2}))
        await db.commit()
        app = FastAPI()
        app.include_router(documents.router)
        async def database():
            yield db
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='student')
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield client, db, app
    await engine.dispose()


async def test_organization_persists_without_replacing_parser_metadata(material_client):
    client, db, _ = material_client
    response = await client.patch('/api/v1/documents/biology/organization', json={
        'subject': '  生物  ', 'material_kind': 'question_bank'})
    assert response.status_code == 200, response.text
    assert response.json()['subject'] == '生物'
    detail = (await client.get('/api/v1/documents/biology')).json()
    listing = (await client.get('/api/v1/documents/')).json()
    assert detail['subject'] == listing[0]['subject'] == '生物'
    assert detail['material_kind'] == listing[0]['material_kind'] == 'question_bank'
    doc = await db.get(Document, 'biology')
    assert doc.metadata_['headings'] == [{'text': '细胞'}]
    assert doc.metadata_['image_count'] == 2
    assert detail['image_count'] == 2


async def test_old_material_has_explicit_default_kind(material_client):
    client, _, _ = material_client
    result = (await client.get('/api/v1/documents/biology')).json()
    assert result['material_kind'] == 'knowledge'
    assert result['subject'] == ''


@pytest.mark.parametrize('payload', [
    {'subject': '生物', 'material_kind': 'invalid'},
    {'subject': 'x' * 81, 'material_kind': 'knowledge'},
    {'subject': '生物', 'material_kind': 'knowledge', 'headings': []},
])
async def test_invalid_organization_is_rejected(material_client, payload):
    client, _, _ = material_client
    result = await client.patch('/api/v1/documents/biology/organization', json=payload)
    assert result.status_code == 422


async def test_missing_material_returns_404(material_client):
    client, _, _ = material_client
    result = await client.patch('/api/v1/documents/missing/organization', json={
        'subject': '', 'material_kind': 'mixed'})
    assert result.status_code == 404


async def test_organization_write_requires_authentication(material_client):
    client, _, app = material_client
    app.dependency_overrides.pop(get_current_user)
    result = await client.patch('/api/v1/documents/biology/organization', json={
        'subject': '', 'material_kind': 'knowledge'})
    assert result.status_code in (401, 403)
