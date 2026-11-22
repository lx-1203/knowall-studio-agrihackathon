"""Real SQLite/ASGI boundaries for the central knowledge network; no paid calls."""
import importlib.util
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PREFIX = '/api/v1/knowledge-network'


def point(chunk='doc-1', quote='导数是瞬时变化率', definition='函数瞬时变化率。', **fields):
    return dict(name='导数', topic='微分', subject='数学', definition=definition, aliases=['微商'],
                conditions=['差商极限存在'], key_points=['极限定义'], pitfalls=['连续未必可导'],
                sources=[{'chunk_id': chunk, 'quote': quote}], **fields)


@pytest.fixture
async def network(tmp_path, monkeypatch):
    from app.core.auth import get_current_user, get_optional_user
    from app.database import Base, get_db
    from app.models import Document, DocumentChunk
    from app.api import documents
    from app.core.api_scheduler import api_client
    app = FastAPI()
    if importlib.util.find_spec('app.api.knowledge_network'):
        from app.api import knowledge_network
        from app.core import knowledge_network as core
        app.include_router(knowledge_network.router)
        async def no_keys(*args):
            pass
        monkeypatch.setattr(core, 'load_user_api_keys', no_keys)
        monkeypatch.setattr(core, 'schedule_source_searches', no_keys)
        monkeypatch.setattr(knowledge_network, 'schedule_source_searches', no_keys)
        monkeypatch.setattr(knowledge_network, 'seed_compiled_concepts', no_keys)
    app.include_router(documents.router)
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'network.sqlite').as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda connection: Base.metadata.create_all(connection, tables=[table for name, table in Base.metadata.tables.items() if name.startswith('network_') or name in ('documents', 'document_chunks', 'system_config')]))
    async with factory() as db:
        for i, owner in [('doc', 'alice'), ('other', 'alice'), ('bobdoc', 'bob'), ('legacy', None)]:
            db.add(Document(id=i, filename=f'{i}.md', sha256=(i * 64)[:64], file_type='md', status='ready',
                            metadata_={'knowledge_owner_id': owner} if owner else {}))
        await db.flush()
        for i in ['doc', 'other', 'bobdoc', 'legacy']:
            db.add(DocumentChunk(id=f'{i}-1', doc_id=i, chunk_index=0, text_content='导数是瞬时变化率。函数极限是逼近值。', page_range='p2-p3'))
        await db.commit()
        async def database():
            yield db
        app.dependency_overrides[get_db] = database
        account = {'id': 'alice'}
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=account['id'])
        app.dependency_overrides[get_optional_user] = lambda: SimpleNamespace(id=account['id']) if account['id'] else None
        calls = []
        generated = {'points': None}
        async def generate(**kwargs):
            calls.append(kwargs)
            body = json.loads(kwargs['generation_content'].split('<reference>\n', 1)[1].rsplit('\n</reference>', 1)[0])
            points = generated['points'] or [point(body[0]['chunk_id'])]
            return SimpleNamespace(content=json.dumps({'points': points}, ensure_ascii=False), model_used='fake-model')
        monkeypatch.setattr(api_client, 'generate', generate)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield SimpleNamespace(client=client, db=db, app=app, account=account, calls=calls, generated=generated,
                                  monkeypatch=monkeypatch, tmp_path=tmp_path)
    await engine.dispose()


async def extract(n, doc='doc', **fields):
    response = await n.client.post(PREFIX + '/extractions', json={'document_id': doc, 'model': 'fake-model', 'subject': '数学', **fields})
    assert response.status_code in (200, 202), response.text
    task = (await n.client.get(PREFIX + '/extractions/' + response.json()['id'])).json()
    return response, task


async def concepts(n, scope='personal'):
    result = await n.client.get(PREFIX + '/concepts', params={'scope': scope})
    assert result.status_code == 200, result.text
    return result.json()['items']


async def test_cross_document_identity_and_completed_job_reuse(network):
    n = network
    _, task = await extract(n)
    assert task['status'] == 'completed' and task['point_count'] == 1
    first = (await concepts(n))[0]
    _, again = await extract(n)
    assert again['id'] == task['id'] and len(n.calls) == 1
    await extract(n, 'other')
    entries = await concepts(n)
    assert len(entries) == 1 and entries[0]['id'] == first['id'] and entries[0]['source_count'] == 0
    detail = (await n.client.get(PREFIX + '/concepts/' + first['id'])).json()
    assert detail['sources'] == [] and detail['can_contribute'] is True
    from app.core.knowledge_network import valid_sources
    owned = await valid_sources(n.db, 'alice', first['id'])
    assert {d.id for _, d, _ in owned} == {'doc', 'other'}
    assert all(c.page_range == 'p2-p3' for _, _, c in owned)
    assert n.calls[0]['context'].user_id == 'alice' and n.calls[0]['config'].model == 'fake-model'


@pytest.mark.parametrize('bad', [point('bobdoc-1'), point(quote='模型编造的引文')])
async def test_fake_evidence_fails_without_partial_writes(network, bad):
    n = network
    n.generated['points'] = [point(), bad]
    _, task = await extract(n)
    assert task['status'] == 'failed' and task['error']
    assert await concepts(n) == []
    from app.models.knowledge_network import NetworkConcept, ConceptSource
    assert (await n.db.scalar(select(func.count()).select_from(NetworkConcept))) == 0
    assert (await n.db.scalar(select(func.count()).select_from(ConceptSource))) == 0
    n.generated['points'] = [point()]
    _, retried = await extract(n)
    assert retried['id'] == task['id'] and retried['status'] == 'completed'


async def test_private_candidates_sources_jobs_and_notes_are_owned(network):
    n = network
    _, task = await extract(n)
    concept = (await concepts(n))[0]
    cid = concept['id']
    assert (await n.client.put(PREFIX + '/concepts/' + cid + '/learning', json={'saved': True, 'mastery': 'learning', 'note': '私密笔记'})).status_code == 200
    n.account['id'] = 'bob'
    assert await concepts(n) == [] and await concepts(n, 'public') == []
    assert (await n.client.get(PREFIX + '/concepts/' + cid)).status_code == 404
    assert (await n.client.get(PREFIX + '/extractions/' + task['id'])).status_code == 404
    assert (await n.client.post(PREFIX + '/extractions', json={'document_id': 'doc'})).status_code == 404
    assert (await n.client.post(PREFIX + '/extractions', json={'document_id': 'legacy'})).status_code == 404


async def test_public_contribution_is_explicit_and_preserves_first_definition(network):
    n = network
    await extract(n)
    cid = (await concepts(n))[0]['id']
    assert (await n.client.post(PREFIX + '/concepts/' + cid + '/publish', json={'reviewed': False})).status_code == 422
    published = await n.client.post(PREFIX + '/concepts/' + cid + '/publish', json={'reviewed': True})
    assert published.status_code == 200
    original = published.json()['definition']
    n.account['id'] = 'bob'
    entries = await concepts(n, 'public')
    assert entries[0]['source_count'] == 0
    detail = (await n.client.get(PREFIX + '/concepts/' + cid)).json()
    assert detail['sources'] == [] and detail['learning'] is None and '私密' not in json.dumps(detail)
    assert (await n.client.post(PREFIX + '/concepts/' + cid + '/publish', json={'reviewed': True})).status_code == 404
    n.generated['points'] = [point('bobdoc-1', definition='另一份私有解释。')]
    await extract(n, 'bobdoc')
    assert (await n.client.post(PREFIX + '/concepts/' + cid + '/publish', json={'reviewed': True})).json()['definition'] == original
    detail = (await n.client.get(PREFIX + '/concepts/' + cid)).json()
    assert detail['sources'] == [] and detail['definition'] == '另一份私有解释。'
    assert detail['can_contribute'] is True


async def test_same_name_different_subject_or_topic_and_ambiguous_relations(network):
    n = network
    await extract(n)
    first = (await concepts(n))[0]['id']
    await extract(n, 'other', subject='物理')
    assert len(await concepts(n)) == 2
    n.generated['points'] = [point('doc-1', related=['导数']), {**point('doc-1'), 'topic': '数值计算'}]
    # Different model makes a deliberate new run, replaces this document's prior evidence.
    await extract(n, model='fake-model-2')
    detail = (await n.client.get(PREFIX + '/concepts/' + first)).json()
    assert detail['relations'] == []
    assert len(await concepts(n)) == 3


async def test_source_change_replaces_evidence_and_document_deletion_revokes_private_access(network):
    n = network
    await extract(n)
    cid = (await concepts(n))[0]['id']
    from app.models import DocumentChunk
    chunk = await n.db.get(DocumentChunk, 'doc-1')
    chunk.text_content = '函数极限是逼近值。'
    await n.db.commit()
    n.generated['points'] = [{**point(quote='函数极限是逼近值'), 'name': '极限', 'topic': '极限'}]
    await extract(n)
    assert (await n.client.get(PREFIX + '/concepts/' + cid)).status_code == 404
    new_id = (await concepts(n))[0]['id']
    assert (await n.client.delete('/api/v1/documents/doc')).status_code == 200
    assert (await n.client.get(PREFIX + '/concepts/' + new_id)).status_code == 404


async def test_upload_same_bytes_is_per_account_and_anonymous_does_not_generate(network):
    from app.api import documents
    from app.core.parsing import ParsedDocument
    from app.config import settings
    from app.core.glossary import extractor
    n = network
    async def parsed(*args):
        return ParsedDocument(text='导数是瞬时变化率。', page_count=1)
    async def no_glossary(*args, **kwargs):
        pass
    n.monkeypatch.setattr(documents.parser, 'parse', parsed)
    n.monkeypatch.setattr(extractor, 'sync_glossary_from_db', no_glossary)
    n.monkeypatch.setattr(settings, 'document_dir', str(n.tmp_path / 'uploads'))
    n.monkeypatch.setattr(settings, 'kaf_publish_enabled', False)
    data = {'file': ('lecture.txt', b'same-upload-content')}
    a = (await n.client.post('/api/v1/documents/upload', files=data)).json()
    assert a['knowledge_extraction']['status'] in ('pending', 'running', 'completed')
    n.account['id'] = 'bob'
    b = (await n.client.post('/api/v1/documents/upload', files=data)).json()
    assert a['document_id'] != b['document_id']
    a_job = (await n.client.get(PREFIX + '/extractions/' + b['knowledge_extraction']['id'])).json()
    assert a_job['status'] == 'completed'
    bob_ids = {p['id'] for p in await concepts(n)}
    n.account['id'] = 'alice'
    assert {p['id'] for p in await concepts(n)} == bob_ids
    n.account['id'] = 'bob'
    assert (await n.client.post(PREFIX + '/extractions', json={'document_id': a['document_id']})).status_code == 404
    before = len(n.calls)
    n.account['id'] = None
    anon = (await n.client.post('/api/v1/documents/upload', files=data)).json()
    assert 'knowledge_extraction' not in anon and len(n.calls) == before


async def test_owned_document_raw_surfaces_reject_other_account_and_anonymous(network):
    n = network
    for uid in ('bob', None):
        n.account['id'] = uid
        for path in ('doc', 'doc/chunks', 'doc/raw', 'doc/native-outline', 'doc/slides', 'doc/slides/0/image'):
            assert (await n.client.get('/api/v1/documents/' + path)).status_code == 404
        assert (await n.client.delete('/api/v1/documents/doc')).status_code == 404
        listing = (await n.client.get('/api/v1/documents/')).json()
        assert 'doc' not in {d['id'] for d in listing}


async def test_cross_document_relation_is_resolved_when_target_arrives(network):
    n = network
    n.generated['points'] = [point(related=['极限'])]
    await extract(n)
    cid = (await concepts(n))[0]['id']
    assert (await n.client.get(PREFIX + '/concepts/' + cid)).json()['relations'] == []
    n.generated['points'] = [{**point('other-1', quote='函数极限是逼近值'), 'name': '极限', 'topic': '极限'}]
    await extract(n, 'other')
    relations = (await n.client.get(PREFIX + '/concepts/' + cid)).json()['relations']
    assert len(relations) == 1 and relations[0]['target_name'] == '极限'
    n.account['id'] = 'bob'
    assert (await n.client.get(PREFIX + '/concepts/' + cid)).status_code == 404


async def test_literature_infers_subject_without_fabricated_formula_or_conditions(network):
    n = network
    n.generated['points'] = [{**point(), 'name': '意象', 'topic': '诗歌阅读', 'subject': '文学',
                              'definition': '承载情感的文学形象。', 'formulas': [], 'conditions': [], 'pitfalls': []}]
    _, task = await extract(n, subject='')
    assert task['status'] == 'completed'
    item = (await concepts(n))[0]
    assert item['subject'] == '文学'
    detail = (await n.client.get(PREFIX + '/concepts/' + item['id'])).json()
    assert detail['content']['formulas'] == [] and detail['content']['conditions'] == []


def test_source_batches_preserve_all_characters_and_reject_over_budget():
    from app.core.knowledge_network import make_source_batches
    text = '头部' + 'x' * 25001 + '最后知识点'
    chunk = SimpleNamespace(id='chunk', text_content=text)
    batches = make_source_batches([chunk])
    assert ''.join(part['text'] for batch in batches for part in batch) == text
    assert all(len(json.dumps(batch, ensure_ascii=False)) <= 12000 for batch in batches)
    with pytest.raises(ValueError, match='16'):
        make_source_batches([SimpleNamespace(id='chunk', text_content='x' * 192001)])


def test_additive_network_migration_preserves_documents_and_is_idempotent():
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/202610101300_add_knowledge_network.py'
    assert path.exists(), '中央网络迁移尚未实现'
    spec = importlib.util.spec_from_file_location('network_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE documents (id VARCHAR(36) PRIMARY KEY)'))
        conn.execute(sa.text('CREATE TABLE document_chunks (id VARCHAR(36) PRIMARY KEY)'))
        conn.execute(sa.text("INSERT INTO documents (id) VALUES ('preserved')"))
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        module.upgrade()
        assert conn.scalar(sa.text('SELECT id FROM documents')) == 'preserved'
        assert set(sa.inspect(conn).get_table_names()) >= {'network_concepts', 'network_extraction_jobs',
            'network_concept_sources', 'network_concept_relations', 'network_concept_learning', 'network_authority_searches'}
        assert module.down_revision == '202610100930'
        from app.models.knowledge_network import NetworkConcept
        assert {c['name'] for c in sa.inspect(conn).get_columns('network_concepts')} == set(NetworkConcept.__table__.columns.keys())
    engine.dispose()


async def test_explicit_json_none_is_private_and_can_be_published(network):
    from app.models.knowledge_network import NetworkConcept
    n = network
    await extract(n)
    cid = (await concepts(n))[0]['id']
    row = await n.db.get(NetworkConcept, cid)
    row.public_content = None
    await n.db.commit()
    assert await concepts(n, 'public') == []
    result = await n.client.post(PREFIX + '/concepts/' + cid + '/publish', json={'reviewed': True})
    assert result.status_code == 200 and result.json()['definition']
    assert len(await concepts(n, 'public')) == 1


async def test_restart_marks_interrupted_jobs_retryable_without_paid_resubmission(network):
    from app.core import knowledge_network as core
    from app.models.knowledge_network import ExtractionJob
    n = network
    _, task = await extract(n)
    job = await n.db.get(ExtractionJob, task['id'], populate_existing=True)
    job.status = 'running'
    from datetime import timedelta
    from app.models.knowledge_network import now
    job.updated_at = now() - timedelta(hours=3)
    await n.db.commit()
    before = len(n.calls)
    assert hasattr(core, 'fail_interrupted_extractions'), '后台任务重启恢复尚未实现'
    await core.fail_interrupted_extractions(n.db)
    await n.db.commit()
    n.db.expire_all()
    job = await n.db.get(ExtractionJob, task['id'])
    assert job.status == 'failed' and job.error and len(n.calls) == before
    _, retry = await extract(n)
    assert retry['status'] == 'completed' and len(n.calls) == before + 1


async def test_startup_recovery_does_not_interrupt_another_active_worker(network):
    from app.core import knowledge_network as core
    from app.models.knowledge_network import ExtractionJob
    n = network
    _, task = await extract(n)
    job = await n.db.get(ExtractionJob, task['id'], populate_existing=True)
    job.status = 'running'
    await n.db.commit()
    await core.fail_interrupted_extractions(n.db)
    await n.db.commit()
    n.db.expire_all()
    assert (await n.db.get(ExtractionJob, task['id'])).status == 'running'


async def test_later_bad_batch_never_persists_earlier_good_batch(network):
    from app.models import DocumentChunk
    from app.core.api_scheduler import api_client
    n = network
    chunk = await n.db.get(DocumentChunk, 'doc-1')
    chunk.text_content = '导数是瞬时变化率。' + 'x' * 12000 + '最后知识点'
    await n.db.commit()
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        points = [point()] if len(calls) == 1 else [point(quote='虚构的最后引文')]
        return SimpleNamespace(content=json.dumps({'points': points}, ensure_ascii=False), model_used='fake-model')
    n.monkeypatch.setattr(api_client, 'generate', generate)
    _, task = await extract(n)
    assert len(calls) == 2 and task['status'] == 'failed'
    assert await concepts(n) == []


async def test_public_detail_uses_central_definition_and_personal_uses_own(network):
    n = network
    await extract(n)
    cid = (await concepts(n))[0]['id']
    await n.client.post(PREFIX + '/concepts/' + cid + '/publish', json={'reviewed': True})
    n.account['id'] = 'bob'
    n.generated['points'] = [point('bobdoc-1', definition='鲍勃自己的理解。')]
    await extract(n, 'bobdoc')
    public = (await n.client.get(PREFIX + '/concepts/' + cid, params={'scope': 'public'})).json()
    personal = (await n.client.get(PREFIX + '/concepts/' + cid, params={'scope': 'personal'})).json()
    assert public['definition'] == '函数瞬时变化率。'
    assert personal['definition'] == '鲍勃自己的理解。'
    assert public['content']['id'] == personal['content']['id'] == cid
    assert 'bobdoc' not in json.dumps(public) and 'doc-1' not in json.dumps(personal)


async def test_url_import_auto_extraction_is_owned_and_deduplicated_per_account(network):
    from app.api import documents
    from app.core.parsing import ParsedDocument
    from app.config import settings
    from app.core.glossary import extractor
    n = network
    async def parsed(*args):
        return ParsedDocument(text='导数是瞬时变化率。', page_count=1)
    async def no_glossary(*args, **kwargs):
        pass
    n.monkeypatch.setattr(documents, '_validate_url_safe', lambda url: None)
    n.monkeypatch.setattr(documents.parser, 'parse', parsed)
    n.monkeypatch.setattr(extractor, 'sync_glossary_from_db', no_glossary)
    n.monkeypatch.setattr(settings, 'document_dir', str(n.tmp_path / 'uploads'))
    url = {'url': 'https://example.org/lecture'}
    a = (await n.client.post('/api/v1/documents/import-url', json=url)).json()
    same = (await n.client.post('/api/v1/documents/import-url', json=url)).json()
    assert same['document_id'] == a['document_id'] and same['duplicate'] is True
    assert same['knowledge_extraction']['status'] == 'completed'
    n.account['id'] = 'bob'
    b = (await n.client.post('/api/v1/documents/import-url', json=url)).json()
    assert b['document_id'] != a['document_id'] and b['knowledge_extraction']
    assert (await n.client.get('/api/v1/documents/' + a['document_id'] + '/chunks')).status_code == 404


async def test_personal_content_preserves_other_document_explanations_without_public_leak(network):
    n = network
    await extract(n)
    cid = (await concepts(n))[0]['id']
    await n.client.post(PREFIX + '/concepts/' + cid + '/publish', json={'reviewed': True})
    n.generated['points'] = [point('other-1', definition='另一本人的补充解释。', formulas=[r'$f\prime$'])]
    await extract(n, 'other')
    detail = (await n.client.get(PREFIX + '/concepts/' + cid)).json()
    assert detail['content']['definition'] == '函数瞬时变化率。'
    assert '另一本人的补充解释。' in detail['content']['supplements']
    assert r'$f\prime$' in detail['content']['formulas']
    assert detail['review_status'] == 'needs_review'
    public = (await n.client.get(PREFIX + '/concepts/' + cid, params={'scope': 'public'})).json()
    assert '另一本人的' not in json.dumps(public, ensure_ascii=False)
    n.account['id'] = 'bob'
    bob = (await n.client.get(PREFIX + '/concepts/' + cid)).json()
    assert '另一本人的' not in json.dumps(bob, ensure_ascii=False)


async def test_reviewed_relation_becomes_public_when_another_account_publishes_target(network):
    n = network
    n.generated['points'] = [point(related=['极限'])]
    await extract(n)
    derivative_id = (await concepts(n))[0]['id']
    n.generated['points'] = [{**point('other-1', quote='函数极限是逼近值'), 'name': '极限', 'topic': '极限'}]
    await extract(n, 'other')
    limit_id = next(p['id'] for p in await concepts(n) if p['name'] == '极限')
    await n.client.post(PREFIX + '/concepts/' + derivative_id + '/publish', json={'reviewed': True})
    n.account['id'] = 'bob'
    n.generated['points'] = [{**point('bobdoc-1', quote='函数极限是逼近值'), 'name': '极限', 'topic': '极限'}]
    await extract(n, 'bobdoc')
    await n.client.post(PREFIX + '/concepts/' + limit_id + '/publish', json={'reviewed': True})
    public = (await n.client.get(PREFIX + '/concepts/' + derivative_id, params={'scope': 'public'})).json()
    assert any(r['target_id'] == limit_id for r in public['relations'])
    assert public['sources'] == []


async def test_owned_upload_and_url_skip_public_glossary_while_anonymous_keeps_legacy_sync(network):
    from app.api import documents
    from app.core.parsing import ParsedDocument
    from app.config import settings
    from app.core.glossary import extractor
    n = network
    calls = []
    async def parsed(*args):
        return ParsedDocument(text='导数是瞬时变化率。', page_count=1)
    async def glossary(*args, **kwargs):
        calls.append(kwargs['document_id'])
    n.monkeypatch.setattr(documents.parser, 'parse', parsed)
    n.monkeypatch.setattr(documents, '_validate_url_safe', lambda url: None)
    n.monkeypatch.setattr(extractor, 'sync_glossary_from_db', glossary)
    n.monkeypatch.setattr(settings, 'document_dir', str(n.tmp_path / 'uploads'))
    n.monkeypatch.setattr(settings, 'kaf_publish_enabled', False)
    own_upload = await n.client.post('/api/v1/documents/upload', files={'file': ('private.txt', b'private-source')})
    assert own_upload.status_code == 200
    own_url = await n.client.post('/api/v1/documents/import-url', json={'url': 'https://example.org/owned'})
    assert own_url.status_code == 200
    assert calls == []
    n.account['id'] = None
    anonymous = await n.client.post('/api/v1/documents/upload', files={'file': ('legacy.txt', b'anonymous-source')})
    assert anonymous.status_code == 200
    assert calls == [anonymous.json()['document_id']]


@pytest.mark.parametrize('extension,subject,topic,items', [
    ('md', '数学', '微分', [
        ('导数', '导数是函数在一点的瞬时变化率，由差商的极限定义。'),
        ('可导', '可导要求差商极限存在且有限，连续不一定可导。'),
    ]),
    ('txt', '计算机科学', '网络传输', [
        ('TCP', 'TCP通过序列号、确认和重传提供可靠的字节流传输。'),
        ('UDP', 'UDP按数据报传输，不保证可靠交付或到达顺序。'),
    ]),
    ('docx', '文学', '修辞', [
        ('比喻', '比喻利用不同事物的相似之处，以一事物说明另一事物。'),
        ('拟人', '拟人赋予非人的事物以人的动作、情感或特征。'),
    ]),
])
async def test_real_multisubject_files_parse_then_extract_and_isolate_accounts(
        network, extension, subject, topic, items):
    """Real files/parser/chunks/SQL/background work; model output is scripted."""
    import io
    from app.config import settings
    from app.core.api_scheduler import api_client
    from app.models import Document, DocumentChunk
    from app.core.knowledge_network import valid_sources
    n = network
    n.monkeypatch.setattr(settings, 'document_dir', str(n.tmp_path / 'uploads'))
    n.monkeypatch.setattr(settings, 'use_docling', False)
    if extension == 'docx':
        from docx import Document as WordDocument
        document = WordDocument()
        document.add_heading(subject, level=1)
        for name, quote in items:
            document.add_heading(name, level=2)
            document.add_paragraph(quote)
        stream = io.BytesIO()
        document.save(stream)
        data = stream.getvalue()
    else:
        data = (f'# {subject}\n\n' + '\n\n'.join(
            f'## {name}\n{quote}' for name, quote in items)).encode('utf-8')

    async def scripted_model(**kwargs):
        n.calls.append(kwargs)
        provided = json.loads(kwargs['generation_content'].split('<reference>\n', 1)[1]
                              .rsplit('\n</reference>', 1)[0])
        result = []
        for name, quote in items:
            chunk = next(part for part in provided if quote in part['text'])
            result.append(dict(name=name, subject=subject, topic=topic, aliases=[],
                               definition=quote, formulas=[], conditions=[], pitfalls=[],
                               key_points=[quote], prerequisites=[], related=[],
                               sources=[{'chunk_id': chunk['chunk_id'], 'quote': quote}]))
        return SimpleNamespace(content=json.dumps({'points': result}, ensure_ascii=False),
                               model_used='scripted-multisubject-model')

    n.monkeypatch.setattr(api_client, 'generate', scripted_model)
    file = {'file': (f'lecture.{extension}', data)}
    uploaded = await n.client.post('/api/v1/documents/upload', files=file)
    assert uploaded.status_code == 200, uploaded.text
    upload = uploaded.json()
    job = (await n.client.get(PREFIX + '/extractions/' + upload['knowledge_extraction']['id'])).json()
    assert job['status'] == 'completed' and job['point_count'] == 2, job
    doc = await n.db.get(Document, upload['document_id'])
    assert doc.metadata_['parser'] == ('python-docx' if extension == 'docx' else 'builtin')
    chunks = list((await n.db.scalars(select(DocumentChunk).where(
        DocumentChunk.doc_id == doc.id))).all())
    for name, quote in items:
        assert any(quote in chunk.text_content for chunk in chunks)
        assert any(quote in call['generation_content'] for call in n.calls)
    entries = await concepts(n)
    assert {entry['name'] for entry in entries} == {item[0] for item in items}
    assert {entry['subject'] for entry in entries} == {subject}
    assert len(await valid_sources(n.db, 'alice')) == 2
    cid = entries[0]['id']
    note = {'saved': True, 'mastery': 'learning', 'note': '只属于Alice的学习笔记'}
    assert (await n.client.put(PREFIX + f'/concepts/{cid}/learning', json=note)).status_code == 200
    before = len(n.calls)
    repeated = (await n.client.post('/api/v1/documents/upload', files=file)).json()
    assert repeated['document_id'] == doc.id and len(n.calls) == before
    assert repeated['knowledge_extraction']['id'] == job['id']

    n.account['id'] = 'bob'
    assert (await n.client.get(PREFIX + f'/concepts/{cid}')).status_code == 404
    assert (await n.client.get(PREFIX + f"/extractions/{job['id']}")).status_code == 404
    assert await concepts(n) == []
    own = (await n.client.post('/api/v1/documents/upload', files=file)).json()
    assert own['document_id'] != doc.id
    own_job = (await n.client.get(PREFIX + '/extractions/' + own['knowledge_extraction']['id'])).json()
    assert own_job['status'] == 'completed' and own_job['point_count'] == 2
    assert {entry['id'] for entry in await concepts(n)} == {entry['id'] for entry in entries}
    detail = (await n.client.get(PREFIX + f'/concepts/{cid}')).json()
    assert detail['sources'] == [] and detail['learning'] is None
    assert 'Alice' not in json.dumps(detail)
    n.account['id'] = 'alice'
    detail = (await n.client.get(PREFIX + f'/concepts/{cid}')).json()
    assert detail['learning']['note'] == note['note']


async def test_owned_upload_never_automatically_publishes_raw_source_as_kaf(network):
    from app.api import documents
    from app.core.parsing import ParsedDocument
    from app.config import settings
    from app.core import kaf_publisher
    from app.core.glossary import extractor
    n = network
    calls = []
    async def parsed(*args):
        return ParsedDocument(text='导数是瞬时变化率。', page_count=1)
    async def publish(*args, **kwargs):
        calls.append(kwargs['document_id'])
    async def no_glossary(*args, **kwargs):
        pass
    n.monkeypatch.setattr(documents.parser, 'parse', parsed)
    n.monkeypatch.setattr(extractor, 'sync_glossary_from_db', no_glossary)
    n.monkeypatch.setattr(kaf_publisher, 'publish_document', publish)
    n.monkeypatch.setattr(settings, 'document_dir', str(n.tmp_path / 'uploads'))
    n.monkeypatch.setattr(settings, 'kaf_publish_enabled', True)
    own = await n.client.post('/api/v1/documents/upload', files={'file': ('private.txt', b'private-kaf-source')})
    assert own.status_code == 200
    assert calls == []
    n.account['id'] = None
    anonymous = await n.client.post('/api/v1/documents/upload', files={'file': ('legacy.txt', b'legacy-kaf-source')})
    assert anonymous.status_code == 200
    assert calls == [anonymous.json()['document_id']]
