"""Real generation pipelines and HTTP handlers; providers are explicit simulations."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import books, research
from app.core.agents.research_agent import ResearchAgent
from app.core.book.compiler import BookCompiler
from app.database import Base
from app.models import Document, DocumentChunk


async def terminal(client, path):
    for _ in range(100):
        response = await client.get(path)
        assert response.status_code == 200, response.text
        data = response.json()
        if data['status'] in ('completed', 'error'):
            return data
        await asyncio.sleep(0.001)
    raise AssertionError('Generation did not reach a terminal state')


@pytest.fixture
async def flow(tmp_path, monkeypatch):
    import app.database
    import app.core.tools
    import app.core.agents.research_agent as agent_module
    import app.core.book.compiler as book_module
    from app.core.api_scheduler import api_client
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path/'source.sqlite').as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app.database, 'async_session', factory)
    async with factory() as db:
        db.add(Document(id='source', filename='biology.txt', file_type='txt', sha256='test-source'))
        db.add(DocumentChunk(id='chunk', doc_id='source', chunk_index=0,
                             text_content='隔离资料：线粒体参与能量转换。', content_hash='test-chunk'))
        await db.commit()
    calls = []
    async def chat(prompt, **kwargs):
        calls.append(prompt)
        if '细化为' in prompt:
            return '细化研究主题'
        if '分解为' in prompt:
            return json.dumps([{'title': '能量研究', 'description': '研究细胞能量'}])
        if '撰写一份结构化' in prompt:
            return '# 研究结果\n来自受控来源的发现。[来源1]'
        if '教材策划' in prompt:
            return json.dumps({'subtitle': '模拟生成', 'chapters': [{'title': '基础', 'sections': [{'title': '能量转换'}]}]})
        if '测试题' in prompt:
            return json.dumps({'questions': [{'question': '哪种细胞器？', 'type': 'single_choice', 'options': [{'label': 'A', 'text': '线粒体'}], 'answer': 'A'}]})
        if '一句话概括' in prompt:
            return '重点：能量转换'
        return '受控教材正文：线粒体参与能量转换。'
    model = AsyncMock(side_effect=chat)
    monkeypatch.setattr(api_client, 'chat', model)
    async def tool(name, **kwargs):
        if name == 'knowledge_search':
            output = {'results': [{'title': '本地资料', 'text': '本地能量转换'}]}
        else:
            output = [{'title': '受控来源', 'snippet': '能量发现', 'abstract': '能量摘要', 'url': 'https://example.invalid/source'}]
        return SimpleNamespace(success=True, output=output)
    search = AsyncMock(side_effect=tool)
    monkeypatch.setattr(app.core.tools, 'execute_tool', search)
    monkeypatch.setattr(agent_module, 'execute_tool', search)
    agent, compiler = ResearchAgent(), BookCompiler()
    monkeypatch.setattr(agent_module, 'research_agent', agent)
    monkeypatch.setattr(book_module, 'book_compiler', compiler)
    app = FastAPI()
    app.include_router(research.router)
    app.include_router(books.router)
    existing_tasks = asyncio.all_tasks()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://generation') as client:
        yield SimpleNamespace(client=client, model=model, search=search, agent=agent,
                              compiler=compiler, calls=calls, chat=chat)
    pending = [task for task in asyncio.all_tasks() - existing_tasks
               if not task.done() and task is not asyncio.current_task()]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await engine.dispose()


async def start_research(flow):
    response = await flow.client.post('/api/v1/research/start', json={'topic': '细胞能量'})
    assert response.status_code == 200, response.text
    return response.json()['task_id']


async def start_book(flow, title='细胞教材'):
    response = await flow.client.post('/api/v1/books/create', json={'title': title, 'source_doc_ids': ['source']})
    assert response.status_code == 200, response.text
    return response.json()['id']


async def test_generation_routes_match_frontend_api_prefix(flow):
    task_id = await start_research(flow)
    book_id = await start_book(flow)
    await terminal(flow.client, f'/api/v1/research/{task_id}/status')
    await terminal(flow.client, f'/api/v1/books/{book_id}')


async def test_research_success_status_list_sources_and_terminal_sse(flow):
    task_id = await start_research(flow)
    status = await terminal(flow.client, f'/api/v1/research/{task_id}/status')
    assert status['status'] == 'completed' and status['progress'] == 1
    assert '研究结果' in status['report'] and len(status['sources']) == 1
    tasks = (await flow.client.get('/api/v1/research/tasks')).json()['tasks']
    assert tasks[0]['task_id'] == task_id and tasks[0]['completed_at'] > 0
    stream = await flow.client.get(f'/api/v1/research/{task_id}/stream')
    assert stream.status_code == 200 and 'event: complete' in stream.text
    assert '研究结果' in stream.text


async def test_research_provider_failure_has_error_and_terminal_sse(flow):
    flow.model.side_effect = RuntimeError('受控模型故障')
    task_id = await start_research(flow)
    status = await terminal(flow.client, f'/api/v1/research/{task_id}/status')
    assert status['status'] == 'error' and '受控模型故障' in status['error']
    assert status['progress'] < 1
    stream = await flow.client.get(f'/api/v1/research/{task_id}/stream')
    assert 'event: error' in stream.text and 'event: complete' not in stream.text


@pytest.mark.parametrize('report', ['', '   '])
async def test_empty_research_report_is_not_a_success(flow, report):
    async def chat(prompt, **kwargs):
        return report if '撰写一份结构化' in prompt else await flow.chat(prompt, **kwargs)
    flow.model.side_effect = chat
    task_id = await start_research(flow)
    status = await terminal(flow.client, f'/api/v1/research/{task_id}/status')
    assert status['status'] == 'error' and status['error']
    assert status['progress'] < 1


async def test_empty_subtopics_still_researches_original_topic(flow):
    async def chat(prompt, **kwargs):
        return '[]' if '分解为' in prompt else await flow.chat(prompt, **kwargs)
    flow.model.side_effect = chat
    task_id = await start_research(flow)
    status = await terminal(flow.client, f'/api/v1/research/{task_id}/status')
    assert status['status'] == 'completed' and len(status['sub_topics']) == 1
    assert status['sub_topics'][0]['finding_count'] > 0 and flow.search.await_count > 0


@pytest.mark.parametrize('path,key', [('/api/v1/research/start', 'topic'), ('/api/v1/books/create', 'title')])
async def test_whitespace_generation_input_is_rejected(flow, path, key):
    response = await flow.client.post(path, json={key: '   '})
    assert response.status_code == 422
    assert flow.model.await_count == 0


async def test_book_compiles_real_source_blocks_and_exports_chinese_title(flow):
    book_id = await start_book(flow)
    book = await terminal(flow.client, f'/api/v1/books/{book_id}')
    assert book['status'] == 'completed' and book['progress'] == 1
    assert any('隔离资料：线粒体' in prompt for prompt in flow.calls)
    blocks = book['chapters'][0]['sections'][0]['blocks']
    assert [block['type'] for block in blocks] == ['text', 'quiz', 'callout', 'retrieval_practice']
    listing = (await flow.client.get('/api/v1/books')).json()['books']
    assert listing[0]['id'] == book_id and listing[0]['chapter_count'] == 1
    exported = await flow.client.get(f'/api/v1/books/{book_id}/export')
    assert exported.status_code == 200 and '# 细胞教材' in exported.text
    assert '受控教材正文' in exported.text and '答案: A' in exported.text
    assert '细胞教材.md' in unquote(exported.headers['content-disposition'])
    assert (await flow.client.delete(f'/api/v1/books/{book_id}')).status_code == 200
    assert (await flow.client.get(f'/api/v1/books/{book_id}')).status_code == 404


async def test_book_provider_failure_surfaces_reason(flow):
    flow.model.side_effect = RuntimeError('受控教材模型故障')
    book_id = await start_book(flow)
    book = await terminal(flow.client, f'/api/v1/books/{book_id}')
    assert book['status'] == 'error' and '受控教材模型故障' in book.get('error', '')
    assert book['progress'] < 1


@pytest.mark.parametrize('text', ['', '   '])
async def test_empty_book_body_is_not_a_success(flow, text):
    async def chat(prompt, **kwargs):
        return text if '教材编写者' in prompt else await flow.chat(prompt, **kwargs)
    flow.model.side_effect = chat
    book_id = await start_book(flow)
    book = await terminal(flow.client, f'/api/v1/books/{book_id}')
    assert book['status'] == 'error' and book.get('error') and book['progress'] < 1


async def test_outline_without_sections_cannot_complete_empty_book(flow):
    async def chat(prompt, **kwargs):
        return '{"chapters":[{"title":"空章节","sections":[]}]}' if '教材策划' in prompt else await flow.chat(prompt, **kwargs)
    flow.model.side_effect = chat
    book_id = await start_book(flow)
    book = await terminal(flow.client, f'/api/v1/books/{book_id}')
    assert book['status'] == 'error' and book.get('error')


async def test_incomplete_book_export_is_rejected(flow):
    from app.core.book.models import Book, BookStatus
    flow.compiler._books['pending'] = Book(id='pending', title='未完成教材', status=BookStatus.PLANNING)
    response = await flow.client.get('/api/v1/books/pending/export')
    assert response.status_code == 409


async def test_delete_running_book_stops_further_provider_work(flow):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def chat(prompt, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    flow.model.side_effect = chat
    book_id = await start_book(flow)
    await asyncio.wait_for(entered.wait(), 1)
    assert (await flow.client.delete(f'/api/v1/books/{book_id}')).status_code == 200
    for _ in range(10):
        if cancelled.is_set():
            break
        await asyncio.sleep(0.001)
    assert cancelled.is_set(), 'Deleting the resource must cancel its background generation'
    assert (await flow.client.get('/api/v1/books')).json()['books'] == []


@pytest.mark.parametrize('path', ['/api/v1/research/missing/status', '/api/v1/research/missing/stream', '/api/v1/books/missing', '/api/v1/books/missing/export'])
async def test_missing_generation_resources_are_404(flow, path):
    assert (await flow.client.get(path)).status_code == 404


def test_main_router_registration_matches_frontend_calls():
    """Assemble the actual two main registrations without its production lifespan."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).parents[1]/'app/main.py').read_text(encoding='utf-8'))
    statements = [node for node in tree.body if isinstance(node, ast.Expr)
                  and isinstance(node.value, ast.Call) and node.value.args
                  and isinstance(node.value.func, ast.Attribute)
                  and node.value.func.attr == 'include_router'
                  and isinstance(node.value.args[0], ast.Name)
                  and node.value.args[0].id in {'research_router', 'books_router'}]
    assert len(statements) == 2
    app = FastAPI()
    exec(compile(ast.Module(body=statements, type_ignores=[]), '<isolated-main-registrations>', 'exec'),
         {'app': app, 'research_router': research.router, 'books_router': books.router})
    paths = {route.path for route in app.routes}
    assert {'/api/v1/research/start', '/api/v1/research/tasks', '/api/v1/books/create', '/api/v1/books/{book_id}/export'} <= paths


@pytest.mark.parametrize('title', ['标题/带空格', '标题"\r\nX-Unsafe: header', '教材📚'])
async def test_export_filename_is_encoded_without_header_injection(flow, title):
    from app.core.book.models import Book, BookStatus
    flow.compiler._books['ready'] = Book(id='ready', title=title, status=BookStatus.COMPLETED)
    response = await flow.client.get('/api/v1/books/ready/export')
    assert response.status_code == 200
    header = response.headers['content-disposition']
    assert '\r' not in header and '\n' not in header and 'filename*=UTF-8' in header
    assert title + '.md' in unquote(header)


async def test_active_research_sse_reports_progress_before_completion(flow):
    entered, release = asyncio.Event(), asyncio.Event()
    async def chat(prompt, **kwargs):
        if '细化为' in prompt:
            entered.set()
            await release.wait()
        return await flow.chat(prompt, **kwargs)
    flow.model.side_effect = chat
    task_id = await start_research(flow)
    await asyncio.wait_for(entered.wait(), 1)
    response_task = asyncio.create_task(flow.client.get(f'/api/v1/research/{task_id}/stream'))
    await asyncio.sleep(0.01)
    release.set()
    response = await asyncio.wait_for(response_task, 2)
    assert 'event: progress' in response.text and 'event: complete' in response.text
    assert response.text.index('event: progress') < response.text.index('event: complete')
