"""Compiled public knowledge joins the central architecture without AI or overwrites."""
import importlib.util

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_compiled_seed_service_exists():
    assert importlib.util.find_spec('app.core.knowledge_network_seed') is not None


def compiled(subject='中国古代文学', definition='用直接陈述表达内容。'):
    return {'id': 'builtin-test', 'subject': subject, 'points': [{
        'name': '赋', 'topic': '诗歌表现手法', 'aliases': [], 'definition': definition,
        'formulas': [], 'conditions': ['用于诗歌表达'], 'key_points': ['直接陈述'],
        'pitfalls': ['与汉赋文体区分'], 'prerequisites': [], 'related': [], 'supplements': [],
    }]}


@pytest.fixture
async def seed_db():
    from app.database import Base
    from app.models.knowledge_network import NetworkConcept
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db, NetworkConcept
    await engine.dispose()


async def test_seed_is_idempotent_and_scoped_to_subject(seed_db, monkeypatch):
    from app.core import knowledge_network_seed as service
    from app.core.knowledge_network import canonical_identity
    db, model = seed_db
    monkeypatch.setattr(service, 'builtin_collections', lambda: [compiled(), compiled('修辞学')])
    await service.seed_compiled_concepts(db)
    await service.seed_compiled_concepts(db)
    rows = list((await db.execute(select(model))).scalars())
    assert len(rows) == 2
    assert {r.subject for r in rows} == {'中国古代文学', '修辞学'}
    assert all(r.public_content['definition'] == '用直接陈述表达内容。' for r in rows)
    assert all(r.review_status == 'compiled' for r in rows)
    assert {r.id for r in rows} == {
        canonical_identity(c['subject'], c['points'][0]['topic'], '赋')
        for c in [compiled(), compiled('修辞学')]
    }


async def test_seed_does_not_publish_private_candidate_or_replace_public_definition(seed_db, monkeypatch):
    from app.core import knowledge_network_seed as service
    from app.core.knowledge_network import canonical_identity
    db, model = seed_db
    private_id = canonical_identity('中国古代文学', '诗歌表现手法', '赋')
    public_id = canonical_identity('修辞学', '诗歌表现手法', '赋')
    private = model(id=private_id, name='赋', subject='中国古代文学', topic='诗歌表现手法', public_content=None)
    published = model(id=public_id, name='赋', subject='修辞学', topic='诗歌表现手法',
                      public_content=compiled()['points'][0] | {'definition': '已经校对贡献的解释'},
                      review_status='reviewed')
    db.add_all([private, published])
    await db.commit()
    monkeypatch.setattr(service, 'builtin_collections', lambda: [compiled(), compiled('修辞学')])
    await service.seed_compiled_concepts(db)
    await db.refresh(private)
    await db.refresh(published)
    assert private.public_content is None
    assert published.public_content['definition'] == '已经校对贡献的解释'
    assert published.review_status == 'reviewed'


async def test_seed_reads_current_compiled_library_without_private_sources(seed_db):
    from app.core import knowledge_network_seed as service
    from app.core.knowledge_network import canonical_identity
    db, model = seed_db
    current = service.builtin_collections()
    await service.seed_compiled_concepts(db)
    rows = list((await db.execute(select(model))).scalars())
    expected = {canonical_identity(c['subject'], p['topic'], p['name']) for c in current for p in c['points']}
    assert {r.id for r in rows} == expected
    for row in rows:
        assert row.public_content['definition']
        assert row.public_content['id'] == row.id
        assert not {'document_id', 'chunk_id', 'quote', 'user_id'} & row.public_content.keys()


async def test_seed_relations_require_unique_public_target_and_are_idempotent(seed_db, monkeypatch):
    from app.core import knowledge_network_seed as service
    from app.core.knowledge_network import canonical_identity
    from app.models.knowledge_network import ConceptRelation
    db, model = seed_db
    collection = compiled()
    parent = collection['points'][0]
    parent['prerequisites'] = ['比', '不存在']
    parent['related'] = ['歧义', '私有']
    for name, topic in [('比', '诗歌表现手法'), ('歧义', '主题一'), ('歧义', '主题二'), ('私有', '主题一')]:
        collection['points'].append(parent | {'name': name, 'topic': topic, 'prerequisites': [], 'related': []})
    private_id = canonical_identity(collection['subject'], '主题一', '私有')
    db.add(model(id=private_id, name='私有', subject=collection['subject'], topic='主题一', public_content=None))
    await db.commit()
    monkeypatch.setattr(service, 'builtin_collections', lambda: [collection])
    await service.seed_compiled_concepts(db)
    await service.seed_compiled_concepts(db)
    relations = list((await db.scalars(select(ConceptRelation))).all())
    assert len(relations) == 1
    row = relations[0]
    assert row.source_id == canonical_identity(collection['subject'], parent['topic'], '赋')
    assert row.target_id == canonical_identity(collection['subject'], parent['topic'], '比')
    assert row.relation_type == 'prerequisite'
    assert row.is_public and row.reviewed
