"""Real persisted study-pack derivatives and HTTP ACLs; no live AI calls."""
from types import SimpleNamespace
import importlib

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models import Document, DocumentChunk, User, KnowledgeSummary, KnowledgeTree, KnowledgePointNode, QuestionBank, KnowledgeEdge, KnowledgeCoverage, AnswerRecord
from app.core.auth import create_access_token
from app.core import study_pack_service
from app.api import knowledge, questions, answer_review, interactive_quiz


@pytest.fixture
async def derived(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'derived.sqlite').as_posix())
    async with engine.begin() as conn:
        names = {'users', 'documents', 'document_chunks', 'knowledge_summaries', 'knowledge_trees', 'knowledge_point_nodes', 'question_bank', 'knowledge_edges', 'knowledge_coverage', 'study_packs', 'decks', 'flashcards', 'review_schedule', 'answer_records', 'error_logs', 'exam_papers', 'exam_submissions'}
        await conn.run_sync(lambda connection: Base.metadata.create_all(connection, tables=[table for name, table in Base.metadata.tables.items() if name in names]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def generated(section, question_count, cards, config):
        return {'notes': 'SECRET derived explanation', 'questions': [{'question_type': 'single_choice', 'question_text': 'SECRET which answer is correct?', 'options': [{'label': 'A', 'text': 'first'}, {'label': 'B', 'text': 'second'}], 'answer': 'A', 'analysis': 'SECRET analysis'}], 'flashcards': []}
    async with factory() as db:
        db.add_all([User(id=name, username=name, email=name+'@derived.invalid', password_hash='fixture') for name in ('alice', 'bob')])
        for doc in ('private', 'legacy'):
            db.add(Document(id=doc, filename=doc+'.md', file_type='md', sha256=doc, status='ready', metadata_={'knowledge_owner_id': 'alice'} if doc == 'private' else {}))
        await db.flush()
        db.add_all([DocumentChunk(id=doc+'-chunk', doc_id=doc, chunk_index=0, text_content='# Concept\nSource explanation.') for doc in ('private', 'legacy')])
        await db.commit()
        private = await study_pack_service.generate_pack(db, 'private', 'alice', {'question_count': 1, 'card_count': 0}, generated)
        legacy = await study_pack_service.generate_pack(db, 'legacy', 'alice', {'question_count': 1, 'card_count': 0}, generated)
        for pack in (private, legacy):
            pack['question_id'] = pack['sections'][0]['questions'][0]['id']
            pack['node_id'] = pack['sections'][0]['knowledge_point_id']
        db.add(KnowledgeEdge(id='private-edge', tree_id=private['tree_id'], source_node_id='a', target_node_id='b', description='SECRET edge'))
        await db.commit()
    async def no_keys(*args):
        pass
    async def analyze(*args):
        return {'mastery_map': {}}
    async def mindmap(*args, **kwargs):
        return SimpleNamespace(status='success', result={'title': 'SECRET map'}, error=None)
    monkeypatch.setattr(interactive_quiz, 'load_user_api_keys', no_keys)
    actual_analyze = answer_review.mastery_analyzer.analyze
    monkeypatch.setattr(answer_review.mastery_analyzer, 'analyze', analyze)
    # This route formerly bypassed its dependency and used a global session.
    monkeypatch.setattr('app.database.async_session', factory)
    monkeypatch.setattr(importlib.import_module('app.core.quiz.mastery_analyzer'), 'async_session', factory)
    monkeypatch.setattr('app.core.agents.mindmap_agent.MindMapAgent.run', mindmap)
    app = FastAPI()
    for module in (knowledge, questions, answer_review, interactive_quiz):
        app.include_router(module.router)
    async def database():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://derived-boundary') as client:
        yield SimpleNamespace(client=client, factory=factory, private=private, legacy=legacy, actual_analyze=actual_analyze,
                              alice={'Authorization': 'Bearer '+create_access_token('alice')}, bob={'Authorization': 'Bearer '+create_access_token('bob')})
    await engine.dispose()


@pytest.mark.parametrize('account', ['bob', 'anonymous'])
@pytest.mark.parametrize('resource', ['summary', 'nodes', 'mindmap', 'tree', 'edges', 'interactive'])
async def test_private_direct_reads_are_hidden(derived, account, resource):
    b = derived; p = b.private
    paths = {'summary': '/knowledge/summary/'+p['summary_id'], 'nodes': '/knowledge/summary/'+p['summary_id']+'/nodes', 'mindmap': '/knowledge/summary/'+p['summary_id']+'/mindmap', 'tree': '/knowledge/tree/'+p['tree_id'], 'edges': '/knowledge/edges/'+p['tree_id'], 'interactive': '/quiz/interactive/start?summary_id='+p['summary_id']}
    result = await b.client.get('/api/v1'+paths[resource], headers=getattr(b, account, {}))
    assert result.status_code == 404, result.text
    assert 'SECRET' not in result.text


@pytest.mark.parametrize('account', ['alice', 'bob', 'anonymous'])
async def test_lists_filter_before_pagination_and_keep_legacy(derived, account):
    b = derived; headers = getattr(b, account, {})
    allowed = 2 if account == 'alice' else 1
    summaries = (await b.client.get('/api/v1/knowledge/summaries?limit=1', headers=headers)).json()
    assert summaries['total'] == allowed
    assert len(summaries['items']) == 1
    trees = (await b.client.get('/api/v1/knowledge/trees?limit=100', headers=headers)).json()
    assert len(trees) == allowed
    bank = (await b.client.get('/api/v1/quiz/questions?limit=1', headers=headers)).json()
    assert len(bank) == 1
    nodes = (await b.client.get('/api/v1/review/knowledge-points', headers=headers)).json()
    assert nodes['total'] == allowed
    if account != 'alice':
        assert bank[0]['id'] == b.legacy['question_id']
        assert nodes['items'][0]['id'] == b.legacy['node_id']
    for p in (b.legacy, *([b.private] if account == 'alice' else [])):
        assert (await b.client.get('/api/v1/knowledge/summary/'+p['summary_id'], headers=headers)).status_code == 200
        assert (await b.client.get('/api/v1/knowledge/tree/'+p['tree_id'], headers=headers)).status_code == 200


@pytest.mark.parametrize('operation', ['summary-delete', 'tree-delete', 'tree-update', 'edge-delete', 'edge-create', 'merge', 'question-delete', 'question-batch', 'exam', 'submit'])
async def test_foreign_mutations_cannot_access_or_destroy_private_derivatives(derived, operation):
    b = derived; p = b.private
    requests = {
        'summary-delete': ('DELETE', '/knowledge/summary/'+p['summary_id'], None),
        'tree-delete': ('DELETE', '/knowledge/tree/'+p['tree_id'], None),
        'tree-update': ('PUT', '/knowledge/tree/'+p['tree_id'], {'name': 'overwrite'}),
        'edge-delete': ('DELETE', '/knowledge/edges/private-edge', None),
        'edge-create': ('POST', '/knowledge/edges', {'tree_id': p['tree_id'], 'source_node_id': 'a', 'target_node_id': 'c'}),
        'merge': ('POST', '/knowledge/tree/merge', {'tree_ids': [p['tree_id'], b.legacy['tree_id']]}),
        'question-delete': ('DELETE', '/quiz/bank/'+p['question_id'], None),
        'question-batch': ('POST', '/quiz/bank/delete-batch', [p['question_id'], b.legacy['question_id']]),
        'exam': ('POST', '/quiz/exam/create', {'question_ids': [p['question_id']]}),
        'submit': ('POST', '/quiz/interactive/submit', {'question_id': p['question_id'], 'user_answer': 'A'}),
    }
    method, path, body = requests[operation]
    result = await b.client.request(method, '/api/v1'+path, headers=b.bob, json=body)
    assert result.status_code == 404, result.text
    async with b.factory() as db:
        assert await db.get(KnowledgeSummary, p['summary_id']) is not None
        assert await db.get(KnowledgeTree, p['tree_id']) is not None
        assert await db.get(QuestionBank, p['question_id']) is not None


async def test_permission_refresh_ignores_stale_document_identity_cache(derived):
    b = derived
    async with b.factory() as stale:
        document = await stale.get(Document, 'private')
        async with b.factory() as writer:
            current = await writer.get(Document, 'private')
            current.metadata_ = {'knowledge_owner_id': 'bob'}
            await writer.commit()
        assert document.metadata_['knowledge_owner_id'] == 'alice'
        from app.core.derived_document_access import require_derived_access
        with pytest.raises(HTTPException) as denied:
            await require_derived_access(stale, await stale.get(KnowledgeSummary, b.private['summary_id']), 'alice')
        assert denied.value.status_code == 404


@pytest.mark.parametrize('account', ['alice', 'bob', 'anonymous'])
async def test_persistent_question_owner_survives_missing_source(derived, account):
    b = derived
    async with b.factory() as db:
        q = await db.get(QuestionBank, b.private['question_id'])
        q.review_scores = {**(q.review_scores or {}), 'knowledge_owner_id': 'alice'}
        q.source_chunk_id = None
        await db.commit()
    headers = getattr(b, account, {})
    result = await b.client.post('/api/v1/quiz/interactive/submit', headers=headers, json={'question_id': b.private['question_id'], 'user_answer': 'A'})
    assert result.status_code == (200 if account == 'alice' else 404), result.text
    listing = (await b.client.get('/api/v1/quiz/questions', headers=headers)).json()
    assert (b.private['question_id'] in {q['id'] for q in listing}) == (account == 'alice')


async def test_mixed_summary_and_public_question_private_coverage_do_not_leak(derived):
    b = derived
    async with b.factory() as db:
        db.add(KnowledgeSummary(id='mixed', document_id='legacy', document_ids=['legacy', 'private'], content_md='SECRET mixed'))
        db.add(KnowledgeCoverage(knowledge_point_id=b.private['node_id'], resource_type='question', resource_id=b.legacy['question_id']))
        await db.commit()
    assert (await b.client.get('/api/v1/knowledge/summary/mixed')).status_code == 404
    assert (await b.client.get('/api/v1/knowledge/summary/mixed', headers=b.alice)).status_code == 200
    public = (await b.client.get('/api/v1/quiz/questions', headers=b.bob)).json()
    assert [node['id'] for node in public[0]['knowledge_points']] == [b.legacy['node_id']]


async def test_deleted_referenced_document_fails_closed_for_summary_and_tree(derived):
    b = derived
    async with b.factory() as db:
        db.add(KnowledgeSummary(id='orphan-summary', document_ids=['missing-document'], content_md='SECRET orphan'))
        db.add(KnowledgeTree(id='orphan-tree', name='orphan', doc_ids=['missing-document'], tree_data={'secret': 'SECRET'}))
        await db.commit()
    for path in ('/knowledge/summary/orphan-summary', '/knowledge/tree/orphan-tree'):
        assert (await b.client.get('/api/v1'+path, headers=b.alice)).status_code == 404


async def test_existing_exam_cannot_grade_private_question_for_foreign_account(derived):
    b = derived
    created = await b.client.post('/api/v1/quiz/exam/create', headers=b.alice, json={'question_ids': [b.private['question_id']]})
    assert created.status_code == 200, created.text
    result = await b.client.post('/api/v1/quiz/exam/submit', headers=b.bob, json={'paper_id': created.json()['paper_id'], 'answers': {b.private['question_id']: 'A'}, 'enable_semantic': False})
    assert result.status_code == 404, result.text


async def test_foreign_question_save_cannot_reference_private_source(derived):
    b = derived
    result = await b.client.post('/api/v1/quiz/bank/save', headers=b.bob, json={'source_chunk_id': 'private-chunk', 'questions': [{'question_type': 'single_choice', 'question_text': 'copy', 'answer': 'A'}]})
    assert result.status_code == 404, result.text


@pytest.mark.parametrize('resource', ['node', 'variant'])
async def test_missing_summary_or_variant_parent_fails_closed(derived, resource):
    b = derived
    async with b.factory() as db:
        if resource == 'node':
            db.add(KnowledgePointNode(id='orphan-node', document_id='legacy', summary_id='missing-summary', level=1, sequence=2, title='SECRET orphan', explanation='SECRET'))
        else:
            db.add(QuestionBank(id='orphan-variant', parent_question_id='missing-parent', question_type='single_choice', question_text='SECRET orphan', correct_answer='A'))
        await db.commit()
    path = '/api/v1/review/knowledge-points' if resource == 'node' else '/api/v1/quiz/questions'
    listing = (await b.client.get(path, headers=b.bob)).json()
    assert 'SECRET orphan' not in str(listing)


@pytest.mark.parametrize('route', ['mastery', 'detail', 'history', 'recommend'])
async def test_old_foreign_records_cannot_expose_private_knowledge_or_recommend_it(derived, monkeypatch, route):
    b = derived
    monkeypatch.setattr(answer_review.mastery_analyzer, 'analyze', b.actual_analyze)
    calls = []
    async def recommendation(weak, moderate, model):
        calls.append(weak)
        return [{'knowledge_point': 'SECRET recommendation'}]
    monkeypatch.setattr(answer_review.mastery_analyzer, '_call_ai_for_recommendations', recommendation)
    async with b.factory() as db:
        db.add(AnswerRecord(user_id='bob', question_id=b.private['question_id'], knowledge_point_ids=[b.private['node_id']], user_answer='B', is_correct=False))
        await db.commit()
    path = '/api/v1/review/' + ('mastery/'+b.private['node_id'] if route == 'detail' else route)
    result = await b.client.request('POST' if route == 'recommend' else 'GET', path, headers=b.bob, **({'json': {}} if route == 'recommend' else {}))
    assert result.status_code in (200, 404), result.text
    assert 'SECRET' not in result.text
    assert calls == []


async def test_public_standalone_question_cannot_attach_private_knowledge_point(derived):
    b = derived
    result = await b.client.post('/api/v1/quiz/interactive/submit', headers=b.bob, json={'question_id': b.legacy['question_id'], 'knowledge_point_id': b.private['node_id'], 'user_answer': 'A'})
    assert result.status_code == 404, result.text
