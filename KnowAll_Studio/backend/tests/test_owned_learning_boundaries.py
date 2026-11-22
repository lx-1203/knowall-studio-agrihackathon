"""Private uploaded documents remain private across the learning workbench."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base, get_db
from app.models import Document, DocumentChunk, User, Deck, Flashcard, QuestionBank
from app.models.study_pack import StudyPack
from app.core.auth import create_access_token
from app.core import study_pack_service as service
from app.api import learning, flashcards


@pytest.fixture
async def boundary(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'learning-boundary.sqlite').as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([User(id=name, username=name, email=name+'@boundary.invalid', password_hash='fixture-only') for name in ('alice', 'bob')])
        db.add_all([Document(id='private', filename='private-lecture.md', file_type='md', sha256='private', status='ready',
                            metadata_={'knowledge_owner_id': 'alice'}),
                    Document(id='legacy', filename='legacy-lecture.md', file_type='md', sha256='legacy', status='ready')])
        await db.flush()
        db.add_all([DocumentChunk(id=doc+'-chunk', doc_id=doc, chunk_index=0, text_content='# Concept\nPrivate source text.', page_range='p1')
                    for doc in ('private', 'legacy')])
        await db.commit()
    calls = []
    async def simulated(section, questions, cards, config):
        calls.append(config['_user_id'])
        return {'notes': 'Learning notes for the concept.', 'questions': [], 'flashcards': ([{'front': 'Private concept question?', 'back': 'Private source answer.'}] if cards else [])}
    async def no_keys(*args):
        pass
    monkeypatch.setattr(service, 'generate_section', simulated)
    monkeypatch.setattr(learning, 'load_user_api_keys', no_keys)
    from app.core.memory.retrieval import card_retriever
    async def no_vectors(*args, **kwargs):
        return []
    monkeypatch.setattr(card_retriever, 'search', no_vectors)
    app = FastAPI()
    app.include_router(learning.router)
    app.include_router(flashcards.router)
    async def database():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://isolated-learning') as client:
        yield SimpleNamespace(factory=factory, client=client, calls=calls, simulated=simulated,
                              alice={'Authorization': 'Bearer '+create_access_token('alice')},
                              bob={'Authorization': 'Bearer '+create_access_token('bob')})
    await engine.dispose()


async def generate(boundary, doc='private', account='alice'):
    return await boundary.client.post('/api/v1/learning/generate', headers=getattr(boundary, account),
                                     json={'document_id': doc, 'question_count': 0, 'card_count': 0, 'model': 'explicit-fake'})


async def test_foreign_preview_and_generation_never_read_owned_source_or_call_ai(boundary):
    b = boundary
    preview = await b.client.get('/api/v1/learning/documents/private/preview', headers=b.bob)
    assert preview.status_code in (403, 404), preview.text
    result = await generate(b, account='bob')
    assert result.status_code in (403, 404) and b.calls == []
    async with b.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPack)) == 0


async def test_own_document_learning_and_legacy_document_remain_usable(boundary):
    b = boundary
    assert (await b.client.get('/api/v1/learning/documents/private/preview', headers=b.alice)).status_code == 200
    result = await generate(b)
    assert result.status_code == 200, result.text
    pack = result.json()
    assert (await b.client.get('/api/v1/learning/documents/private/pack', headers=b.alice)).json()['id'] == pack['id']
    assert (await b.client.get('/api/v1/learning/packs/'+pack['id']+'/export', headers=b.alice)).status_code == 200
    assert (await b.client.get('/api/v1/learning/documents/legacy/preview', headers=b.bob)).status_code == 200
    assert (await generate(b, 'legacy', 'bob')).status_code == 200


async def test_anonymous_http_and_explicit_core_call_cannot_read_owned_document(boundary):
    b = boundary
    assert (await b.client.get('/api/v1/learning/documents/private/preview')).status_code == 401
    assert (await b.client.post('/api/v1/learning/generate', json={'document_id': 'private'})).status_code == 401
    async with b.factory() as db:
        with pytest.raises(HTTPException) as denied:
            await service._chunks(db, 'private')
        assert denied.value.status_code in (403, 404)


async def test_old_foreign_pack_cannot_export_or_expose_notes_after_document_is_owned(boundary):
    b = boundary
    async with b.factory() as db:
        db.add(StudyPack(id='old-foreign-pack', user_id='bob', document_id='private', title='private title',
                         source_hash='old', config={}, sections=[{'notes': 'private-derived-notes', 'questions': [], 'flashcards': []}]))
        await db.commit()
    result = await b.client.get('/api/v1/learning/packs/old-foreign-pack/export', headers=b.bob)
    assert result.status_code in (403, 404), result.text
    latest = await b.client.get('/api/v1/learning/documents/private/pack', headers=b.bob)
    assert latest.status_code in (403, 404)
    assert 'private-derived-notes' not in latest.text


async def test_document_owner_change_during_generation_prevents_pack_writes(boundary):
    b = boundary
    async def ownership_changed(section, questions, cards, config):
        async with b.factory() as other_db:
            document = await other_db.get(Document, 'private')
            document.metadata_ = {'knowledge_owner_id': 'bob'}
            await other_db.commit()
        return await b.simulated(section, questions, cards, config)
    async with b.factory() as db:
        with pytest.raises(HTTPException) as denied:
            await service.generate_pack(db, 'private', 'alice', {'question_count': 0, 'card_count': 0, 'concurrency': 1}, ownership_changed)
        assert denied.value.status_code in (403, 404)
    async with b.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPack)) == 0


async def test_deleted_document_does_not_leave_an_orphan_pack_visible_by_document_id(boundary):
    b = boundary
    async with b.factory() as db:
        db.add(StudyPack(id='orphan-foreign-pack', user_id='bob', document_id='private', title='private title',
                         source_hash='old', config={}, sections=[{'notes': 'private-derived-notes', 'questions': [], 'flashcards': []}]))
        await db.commit()
        await db.delete(await db.get(Document, 'private'))
        await db.commit()
    latest = await b.client.get('/api/v1/learning/documents/private/pack', headers=b.bob)
    assert latest.status_code == 200 and latest.json() is None
    exported = await b.client.get('/api/v1/learning/packs/orphan-foreign-pack/export', headers=b.bob)
    assert exported.status_code == 404


async def private_card_pack(b):
    result = await b.client.post('/api/v1/learning/generate', headers=b.alice,
                                 json={'document_id': 'private', 'question_count': 0, 'card_count': 1, 'model': 'explicit-fake'})
    assert result.status_code == 200, result.text
    pack = result.json()
    async with b.factory() as db:
        card = (await db.execute(select(Flashcard).where(Flashcard.deck_id == pack['deck_id']))).scalar_one()
        return pack, card.id


async def test_learning_deck_retains_owner_when_pack_link_is_removed(boundary):
    b = boundary
    pack, card_id = await private_card_pack(b)
    async with b.factory() as db:
        await db.delete(await db.get(StudyPack, pack['id']))
        await db.commit()
    for headers in (b.bob, {}):
        result = await b.client.get('/api/v1/flashcards/cards/'+card_id, headers=headers)
        assert result.status_code == 404, result.text
    async with b.factory() as db:
        assert (await db.get(Deck, pack['deck_id'])).user_id == 'alice'
    assert (await b.client.get('/api/v1/flashcards/cards/'+card_id, headers=b.alice)).status_code == 200


async def test_learning_card_deck_search_due_and_export_are_owner_scoped(boundary, monkeypatch):
    b = boundary
    pack, card_id = await private_card_pack(b)
    from app.core.memory.retrieval import card_retriever
    async def vector_result(*args, **kwargs):
        return [{'id': card_id, 'front': 'stale vector text', 'back': 'stale answer'}]
    monkeypatch.setattr(card_retriever, 'search', vector_result)
    exports = []
    def fake_export(cards, name, *, output_path, deck_id):
        from pathlib import Path
        exports.append(deck_id)
        Path(output_path).write_bytes(b'owner-export')
    monkeypatch.setattr(flashcards, 'export_apkg', fake_export)
    # Remove the aggregate link to prove the deck's own persistent ACL works.
    async with b.factory() as db:
        await db.delete(await db.get(StudyPack, pack['id']))
        await db.commit()
    for headers in (b.bob, {}):
        for path in ('/cards/'+card_id, '/related/'+card_id, '/export/anki/'+pack['deck_id']):
            result = await b.client.get('/api/v1/flashcards'+path, headers=headers)
            assert result.status_code == 404, (path, result.text)
        for path in ('/decks', '/due', '/search?q=Private'):
            result = await b.client.get('/api/v1/flashcards'+path, headers=headers)
            assert result.status_code == 200, result.text
            assert card_id not in result.text and pack['deck_id'] not in result.text and 'Private source answer.' not in result.text
        summary = await b.client.post('/api/v1/flashcards/deck/'+pack['deck_id']+'/summary', headers=headers, json={})
        assert summary.status_code == 404
        result = await b.client.post('/api/v1/flashcards/review', headers=headers, json={'card_id': card_id, 'rating': 3})
        assert result.status_code == 404
    assert exports == []
    own_decks = await b.client.get('/api/v1/flashcards/decks', headers=b.alice)
    assert pack['deck_id'] in own_decks.text
    own_due = await b.client.get('/api/v1/flashcards/due', headers=b.alice)
    assert card_id in own_due.text and 'Private source answer.' in own_due.text
    own_search = await b.client.get('/api/v1/flashcards/search?q=Private', headers=b.alice)
    assert card_id in own_search.text and 'Private source answer.' in own_search.text and 'stale answer' not in own_search.text
    own_export = await b.client.get('/api/v1/flashcards/export/anki/'+pack['deck_id'], headers=b.alice)
    assert own_export.status_code == 200, own_export.text
    assert own_export.content == b'owner-export' and exports == [pack['deck_id']]
    review = await b.client.post('/api/v1/flashcards/review', headers=b.alice, json={'card_id': card_id, 'rating': 3})
    assert review.status_code == 200 and review.json()['review_count'] == 1


@pytest.mark.parametrize('document_id,expected_owner', [('private', 'alice'), ('legacy', None)])
async def test_question_owner_provenance_survives_source_chunk_removal(boundary, document_id, expected_owner):
    b = boundary
    async def simulated(section, questions, cards, config):
        return {'notes': 'Learning notes', 'questions': [{'question_type': 'single_choice',
                'question_text': 'Which concept is described?', 'options': [{'label': 'A', 'text': 'Concept'}, {'label': 'B', 'text': 'Other'}],
                'answer': 'A', 'analysis': 'Concept matches.'}], 'flashcards': []}
    async with b.factory() as db:
        pack = await service.generate_pack(db, document_id, 'alice',
                      {'question_count': 1, 'card_count': 0, 'concurrency': 1}, generator=simulated)
        question_id = pack['sections'][0]['questions'][0]['id']
        bank = await db.get(QuestionBank, question_id)
        assert bank.review_scores.get('knowledge_owner_id') == expected_owner
        assert 'structural_quality' in bank.review_scores
        # Simulate the FK SET NULL behavior after source deletion.
        bank.source_chunk_id = None
        await db.delete(await db.get(DocumentChunk, document_id+'-chunk'))
        await db.commit()
    async with b.factory() as db:
        assert (await db.get(QuestionBank, question_id)).review_scores.get('knowledge_owner_id') == expected_owner
