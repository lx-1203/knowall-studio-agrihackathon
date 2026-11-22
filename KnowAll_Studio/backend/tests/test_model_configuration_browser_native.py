"""Real upload/config/learning routers, JWT, SQL and adapters; only provider is simulated."""
import asyncio
import importlib
import json
import os
from pathlib import Path
from datetime import datetime
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select
from app.models import Document, DocumentChunk, KnowledgeTree, Flashcard, APICallLog
from app.models.study_pack import StudyPack
from tests.test_model_connections import connections
from tests.test_reading_native_stream_http_flow import server


@pytest.mark.integration
async def test_native_model_configuration_and_uploaded_learning(connections, monkeypatch, tmp_path):
    from app.api import documents, learning, agent_runs
    from app.config import settings
    from app.core import knowledge_network
    from app.core.api_scheduler import cache, client
    limiter = importlib.import_module('app.core.api_scheduler.rate_limiter')
    c = connections
    c.app.include_router(documents.router); c.app.include_router(learning.router); c.app.include_router(agent_runs.router)
    monkeypatch.setattr(agent_runs, 'api_client', c.api)
    monkeypatch.setattr(settings, 'document_dir', str(tmp_path/'uploads'))
    monkeypatch.setattr(cache, 'async_session', c.factory)
    monkeypatch.setattr(limiter, 'async_session', c.factory)
    monkeypatch.setattr(client, 'rate_limiter', limiter.RateLimiter())
    # Background global indexing is a separate workflow; the learning generator is real.
    monkeypatch.setattr(knowledge_network, 'enqueue_extraction', lambda *args: None)
    provider = FastAPI(); seen = []
    @provider.get('/v1/models')
    async def models():
        return {'data': [{'id': 'vendor/brand-new', 'name': 'Brand New Model'}]}
    @provider.post('/v1/chat/completions')
    async def completion(request: Request):
        body = await request.json()
        assert request.headers['authorization'] == 'Bearer synthetic-private-key'
        assert body['model'] == 'vendor/brand-new'
        probe = body['messages'][0]['content'] == 'Reply with OK only.'
        seen.append({'model': body['model'], 'kind': 'probe' if probe else 'learning'})
        text = 'OK' if probe else json.dumps({'notes': '细胞膜控制物质进出，例如选择性吸收。', 'questions': [],
            'flashcards': [{'front': '细胞膜的作用是什么？', 'back': '控制物质进出。', 'hints': '物质运输'}]}, ensure_ascii=False)
        return {'model': body['model'], 'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 12, 'completion_tokens': 10}}
    upload = await c.http.post('/api/v1/documents/upload', headers=c.alice,
        files={'file': ('biology.md', '# 细胞\n\n细胞膜控制物质进出，维持细胞内部环境稳定。\n'.encode(), 'text/markdown')})
    assert upload.status_code == 200, upload.text
    doc_id = upload.json()['document_id']
    out = Path(__file__).resolve().parents[2]/'docs/model-configuration-2026-10-20'
    out.mkdir(parents=True, exist_ok=True)
    async with server(provider) as remote, server(c.app) as backend:
        frontend = Path(__file__).resolve().parents[2]/'frontend'
        env = dict(os.environ, MODEL_CONFIG_BACKEND=str(backend.base_url).rstrip('/'),
            MODEL_CONFIG_PROVIDER=str(remote.base_url).rstrip('/')+'/v1', MODEL_CONFIG_DOCUMENT=doc_id,
            MODEL_CONFIG_TOKEN=c.alice['Authorization'][7:],
            PLAYWRIGHT_BROWSERS_PATH=str(frontend.parents[1]/'.playwright-browsers'))
        process = await asyncio.create_subprocess_exec('node', 'node_modules/@playwright/test/cli.js', 'test',
            '--config=playwright.model-config-native.config.ts', '--reporter=list', cwd=frontend, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 100)
        except TimeoutError:
            process.kill(); await process.wait(); raise
        (out/'playwright-native.log').write_bytes(stdout)
        assert process.returncode == 0, stdout.decode(errors='replace')[-6500:]
    async with c.factory() as db:
        pack = (await db.scalars(select(StudyPack))).one()
        cards = (await db.scalars(select(Flashcard))).all()
        tree = await db.get(KnowledgeTree, pack.tree_id)
        doc = await db.get(Document, doc_id)
        chunks = (await db.scalars(select(DocumentChunk))).all()
        logs = (await db.scalars(select(APICallLog))).all()
        assert pack.user_id == 'alice' and pack.config['model'].startswith('connection-')
        assert len(cards) == 1 and cards[0].front == '细胞膜的作用是什么？'
        assert tree.tree_data['nodes'][0]['summary'].startswith('细胞膜控制物质进出')
        assert doc.metadata_['knowledge_owner_id'] == 'alice' and chunks
        assert len(logs) == 1 and logs[0].model_name == 'vendor/brand-new'
        assert seen == [{'model': 'vendor/brand-new', 'kind': 'probe'}, {'model': 'vendor/brand-new', 'kind': 'learning'}]
    (out/'native-evidence.json').write_text(json.dumps({'at': datetime.now().astimezone().isoformat(),
        'scope': 'current built frontend / actual config, upload and learning routers / JWT / temporary SQLite / native OpenAI adapter',
        'provider': 'explicit localhost simulation', 'paid_calls': 0, 'uploaded_chunks': len(chunks),
        'persisted_cards': len(cards), 'persisted_map_nodes': len(tree.tree_data['nodes']),
        'selected_model_used': pack.config['model'], 'provider_calls': seen, 'api_logs': len(logs)}, ensure_ascii=False, indent=2), encoding='utf-8')
