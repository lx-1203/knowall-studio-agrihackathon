"""Knowledge-only collections: consolidation, no input loss, and account isolation."""
import importlib.util
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_collection_module_exists():
    assert importlib.util.find_spec('app.core.knowledge_collections') is not None, '知识点集锦尚未实现'


def point(**kwargs):
    return dict(name='导数', topic='微分', definition='函数在某点的瞬时变化率。',
                formulas=['$f\'(x)$'], conditions=['对应差商极限存在'],
                key_points=['用极限定义'], pitfalls=['可导比连续要求更强'], **kwargs)


def test_merge_preserves_conditions_and_distinguishes_topics():
    from app.core.knowledge_collections import consolidate
    a = point(aliases=['微商'])
    b = {**point(), 'name': ' 导数 ', 'conditions': ['函数在该点邻域有定义'],
         'definition': '差商的极限。'}
    c = {**point(), 'topic': '数值计算', 'definition': '数值差分近似变化率。'}
    result = consolidate([a, b, c])
    assert len(result) == 2
    assert result[0].conditions == ['对应差商极限存在', '函数在该点邻域有定义']
    assert result[0].supplements == ['差商的极限。']
    assert result[0].id != result[1].id


def test_batches_cover_every_character_and_reject_oversize():
    from app.core.knowledge_collections import make_batches
    texts = ['头部' + 'x' * 25001 + '最后知识点']
    batches = make_batches(texts)
    assert ''.join(batches) == ''.join(texts)
    assert all(len(b) <= 12000 for b in batches)
    with pytest.raises(ValueError, match='16'):
        make_batches(['x' * 192001])


def test_schema_rejects_raw_document_fields_and_empty_points():
    from app.core.knowledge_collections import CollectionContent
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CollectionContent.model_validate({'title': '测试', 'subject': '数学', 'points': [], 'raw_text': '原文'})
    with pytest.raises(ValidationError):
        CollectionContent.model_validate({'title': '测试', 'subject': '数学', 'points': [point(raw_text='原文')]})


def test_formula_case_and_internal_spacing_are_not_collapsed():
    from app.core.knowledge_collections import consolidate
    a = {**point(), 'formulas': ['$P(A)$', '$x y$']}
    b = {**point(), 'formulas': ['$p(a)$', '$xy$']}
    assert consolidate([a, b])[0].formulas == ['$P(A)$', '$x y$', '$p(a)$', '$xy$']


async def test_real_extraction_batches_validate_and_merge_with_owner_context(monkeypatch):
    from app.core.api_scheduler import api_client
    from app.core.knowledge_collections import generate_collection, make_batches
    calls = []
    async def generated(**kwargs):
        calls.append(kwargs)
        item = {**point(), 'conditions': [f'第 {len(calls)} 批条件']}
        return SimpleNamespace(content=json.dumps({'points': [item]}, ensure_ascii=False), model_used='configured-model')
    monkeypatch.setattr(api_client, 'generate', generated)
    batches = make_batches(['x' * 12001 + '最后知识点'])
    points, models = await generate_collection(batches, '高数', 'test-model', 'alice')
    assert len(calls) == 2
    assert calls[-1]['generation_content'].endswith('最后知识点\n</reference>')
    assert all(call['context'].user_id == 'alice' for call in calls)
    assert all(call['config'].model == 'test-model' for call in calls)
    assert len(points) == 1 and points[0].conditions == ['第 1 批条件', '第 2 批条件']
    assert models == ['configured-model']


def test_builtin_content_preserves_subjects_and_valid_relations():
    from app.core.knowledge_collections import builtin_collections, normalized
    collections = builtin_collections()
    assert [c['subject'] for c in collections] == ['农业数据分析', '分子生物学']
    assert all(len(c['points']) >= 2 for c in collections)
    from pathlib import Path
    raw = json.loads((Path(__file__).resolve().parents[1] / 'app/assets/knowledge_collections.json').read_text(encoding='utf-8'))
    for entry in raw:
        identities = [(normalized(p['topic']), normalized(p['name'])) for p in entry['points']]
        assert len(set(identities)) == len(identities), f"重复知识点: {entry['title']}"
    for collection in collections:
        points = collection['points']
        names = {p['name'] for p in points}
        for p in points:
            assert p['definition'] and p['conditions'] and p['key_points'] and p['pitfalls']
            assert all(n in names for n in p['prerequisites'] + p['related'])
            assert 'raw_text' not in p


@pytest.fixture
async def collection_client():
    from app.api import knowledge_collections as routes
    from app.core.auth import get_current_user
    from app.database import Base, get_db
    from app.models import Document, DocumentChunk
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add(Document(id='math', filename='数学笔记.md', sha256='d' * 64, file_type='md', status='ready'))
        await db.flush()
        db.add(DocumentChunk(id='math-1', doc_id='math', chunk_index=0, text_content='仅测试输入，末尾定义'))
        await db.commit()
        app = FastAPI()
        app.include_router(routes.router)
        async def database():
            yield db
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='alice')
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield client, db, app, routes
    await engine.dispose()


async def test_import_persists_only_knowledge_and_isolates_accounts(collection_client):
    from app.core.auth import get_current_user
    client, _, app, _ = collection_client
    response = await client.post('/api/v1/knowledge-collections/import', json={
        'title': '微积分集锦', 'subject': '高数', 'points': [point()]})
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved['review_status'] == 'needs_review'
    detail = (await client.get(f"/api/v1/knowledge-collections/{saved['id']}")).json()
    assert detail['points'][0]['definition'] == '函数在某点的瞬时变化率。'
    assert 'provenance' not in detail
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='bob')
    assert (await client.get(f"/api/v1/knowledge-collections/{saved['id']}")).status_code == 404
    listing = (await client.get('/api/v1/knowledge-collections')).json()
    assert saved['id'] not in {c['id'] for c in listing}
    app.dependency_overrides.pop(get_current_user)
    assert (await client.get('/api/v1/knowledge-collections')).status_code in (401, 403)


async def test_preview_does_not_generate_and_missing_documents_are_rejected(collection_client, monkeypatch):
    client, _, _, routes = collection_client
    async def forbidden(*args, **kwargs):
        pytest.fail('预览不得调用模型')
    monkeypatch.setattr(routes, 'generate_collection', forbidden)
    response = await client.post('/api/v1/knowledge-collections/preview', json={'document_ids': ['math']})
    assert response.status_code == 200
    assert response.json()['batch_count'] == 1
    assert response.json()['characters'] == len('仅测试输入，末尾定义')
    assert (await client.post('/api/v1/knowledge-collections/preview', json={'document_ids': ['math', 'missing']})).status_code == 404


async def test_generation_failure_does_not_save_partial_collection(collection_client, monkeypatch):
    client, _, _, routes = collection_client
    async def failed(*args, **kwargs):
        raise ValueError('无法解析完整知识点')
    async def no_keys(*args, **kwargs):
        pass
    monkeypatch.setattr(routes, 'generate_collection', failed)
    monkeypatch.setattr(routes, 'load_user_api_keys', no_keys)
    response = await client.post('/api/v1/knowledge-collections/generate', json={
        'title': '失败集锦', 'subject': '高数', 'document_ids': ['math'], 'model': 'test-model'})
    assert response.status_code == 502
    from app.core.knowledge_collections import builtin_collections
    assert len((await client.get('/api/v1/knowledge-collections')).json()) == len(builtin_collections())


async def test_generation_saves_trace_without_source_text(collection_client, monkeypatch):
    from app.models.knowledge_collection import KnowledgeCollection
    from app.core.knowledge_collections import consolidate
    client, db, _, routes = collection_client
    async def generated(*args, **kwargs):
        return consolidate([point()]), ['test-model']
    async def no_keys(*args, **kwargs):
        pass
    monkeypatch.setattr(routes, 'generate_collection', generated)
    monkeypatch.setattr(routes, 'load_user_api_keys', no_keys)
    response = await client.post('/api/v1/knowledge-collections/generate', json={
        'title': '高数集锦', 'subject': '高数', 'document_ids': ['math'], 'model': 'test-model'})
    assert response.status_code == 201, response.text
    row = await db.get(KnowledgeCollection, response.json()['id'])
    assert row.provenance['document_ids'] == ['math']
    assert row.provenance['models'] == ['test-model']
    assert '仅测试输入' not in json.dumps(row.points, ensure_ascii=False)
    assert '仅测试输入' not in json.dumps(row.provenance, ensure_ascii=False)


async def test_review_changes_are_owned_and_do_not_overwrite_provenance(collection_client):
    from app.core.auth import get_current_user
    from app.models.knowledge_collection import KnowledgeCollection
    client, db, app, _ = collection_client
    payload = {'title': '高数集锦', 'subject': '高数', 'points': [point()]}
    saved = (await client.post('/api/v1/knowledge-collections/import', json=payload)).json()
    payload['points'][0]['definition'] = '已核对的差商极限定义。'
    result = await client.put(f"/api/v1/knowledge-collections/{saved['id']}", json={**payload, 'reviewed': True})
    assert result.status_code == 200, result.text
    assert result.json()['review_status'] == 'reviewed'
    assert (await db.get(KnowledgeCollection, saved['id'])).provenance == {'method': 'structured_import'}
    edited = await client.put(f"/api/v1/knowledge-collections/{saved['id']}", json=payload)
    assert edited.json()['review_status'] == 'needs_review'
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='bob')
    assert (await client.put(f"/api/v1/knowledge-collections/{saved['id']}", json=payload)).status_code == 404
    assert (await client.put('/api/v1/knowledge-collections/builtin-1', json=payload)).status_code == 404
