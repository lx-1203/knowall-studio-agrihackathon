"""Temporary real cloud service for desktop integration; never a production entry.

Registers synthetic accounts and uploads a Markdown document over HTTP. Only
content generation is deterministic; auth, routing, workers and persistence use
the production code. All data lives in a fresh temporary directory.
"""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PREFIX = 'KNOWALL_INTEGRATION '
PASSWORD = 'integration-only-123'
DOCUMENT = '# 细胞结构\n\n细胞核保存遗传物质；线粒体参与细胞呼吸并为细胞活动提供能量。\n'
MARKDOWN_FLASHCARD = False
MARKDOWN_ANSWER = '''**供能要点**

| 结构 | 主要功能 |
|---|---|
| 线粒体 | 供能 |
| 细胞核 | 遗传物质 |

```python
if energy < 2:
    print("供能")
```

- 细胞呼吸
- 能量供应

[安全资料](https://example.test/biology)
[危险](javascript:alert%281%29)
![远程图片](https://example.test/tracker.png)
<script>alert(1)</script>
'''


def emit(event, **data):
    print(PREFIX + json.dumps({'event': event, **data}, ensure_ascii=True), flush=True)


async def generated_content(section, question_count, card_count, config):
    return {
        'notes': '## 细胞结构\n\n细胞核保存遗传物质，线粒体参与细胞呼吸。',
        'questions': [{
            'question_type': 'matching', 'question_text': '匹配细胞结构与功能',
            'options': [
                {'label': 'A', 'text': '细胞核', 'choices': ['遗传物质', '能量供应']},
                {'label': 'B', 'text': '线粒体', 'choices': ['遗传物质', '能量供应']},
            ],
            'answer': json.dumps({'A': '遗传物质', 'B': '能量供应'}, ensure_ascii=False),
            'analysis': '根据来源原文，细胞核保存遗传物质，线粒体参与供能。',
        } for _ in range(question_count)],
        'flashcards': [{'front': '线粒体的主要作用是什么？', 'back': MARKDOWN_ANSWER if MARKDOWN_FLASHCARD else '参与细胞呼吸，为细胞活动提供能量。'}
                      for _ in range(card_count)],
    }


async def prepare(origin, root):
    import httpx
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.cloud_workspaces import workspace_directory
    from app.core.study_pack_service import generate_pack

    users = []
    async with httpx.AsyncClient(base_url=origin, trust_env=False, timeout=70) as client:
        assert (await client.get('/api/v1/documents/')).status_code == 401
        for username in ['integration_alice', 'integration_bob']:
            response = await client.post('/api/v1/auth/register', json={
                'username': username, 'email': username + '@example.test',
                'password': PASSWORD, 'confirm_password': PASSWORD,
            })
            assert response.status_code == 201, response.text
            account = response.json()
            headers = {'Authorization': 'Bearer ' + account['access_token']}
            response = await client.get('/api/v1/documents/', headers=headers)
            assert response.status_code == 200 and response.json() == [], response.text
            users.append({'username': username, 'user_id': account['user']['id'], 'headers': headers})
        response = await client.post('/api/v1/documents/upload', headers=users[0]['headers'],
                                     files={'file': ('integration-biology.md', DOCUMENT.encode(), 'text/markdown')})
        assert response.status_code == 200, response.text
        uploaded = response.json()
        assert uploaded['total_chunks'] > 0, uploaded
        document_id = uploaded['document_id']
        private_db = workspace_directory(root / 'workspaces', users[0]['user_id']) / 'learning.sqlite'
        engine = create_async_engine('sqlite+aiosqlite:///' + private_db.as_posix())
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                pack = await generate_pack(db, document_id, users[0]['user_id'], {
                    'question_count': 1, 'card_count': 1, 'model': 'integration-fixture',
                    'question_types': ['matching'], 'concurrency': 1, 'max_section_chars': 6000,
                }, generator=generated_content)
                assert pack['quality']['blocked'] == 0 and pack['coverage']['failed_sections'] == 0, pack
        finally:
            await engine.dispose()
        assert (await client.get('/api/v1/documents/' + document_id, headers=users[1]['headers'])).status_code == 404
        assert (await client.get('/api/v1/learning/packs/' + pack['id'] + '/export', headers=users[1]['headers'])).status_code == 404
        assert (await client.get('/api/v1/documents/', headers=users[1]['headers'])).json() == []
    return users, pack, document_id


async def check_learning(origin, users, pack):
    import httpx
    q = pack['sections'][0]['questions'][0]
    card = pack['sections'][0]['flashcards'][0]
    async with httpx.AsyncClient(base_url=origin, trust_env=False, timeout=70, headers=users[0]['headers']) as client:
        base = '/api/v1/learning/packs/' + pack['id']
        response = await client.post(base + '/session', json={'mode': 'practice'})
        assert response.status_code == 200 and response.json()['items'][0]['question']['id'] == q['id'], response.text
        assert 'answer' not in response.json()['items'][0]['question']
        response = await client.post(base + '/answers', json={
            'question_id': q['id'], 'answer': json.dumps({'A': '遗传物质', 'B': '遗传物质'}),
        })
        assert response.status_code == 200 and response.json()['score_ratio'] == .5, response.text
        response = await client.post(base + '/session', json={'mode': 'smart'})
        assert response.status_code == 200
        assert {x['reason'] for x in response.json()['items']} == {'wrong_answer', 'due'}, response.text
        response = await client.post(base + '/review', json={'card_id': card['id'], 'rating': 4})
        assert response.status_code == 200 and response.json()['review_count'] == 1, response.text
        for format in ['json', 'markdown', 'csv']:
            response = await client.get(base + '/export', params={'format': format})
            assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
        return q['id'], card['id']


async def persisted_records(root, user, pack):
    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.cloud_workspaces import workspace_directory
    from app.models import AnswerRecord, ReviewSchedule
    private_db = workspace_directory(root / 'workspaces', user['user_id']) / 'learning.sqlite'
    engine = create_async_engine('sqlite+aiosqlite:///' + private_db.as_posix())
    try:
        async with async_sessionmaker(engine)() as db:
            question_id = pack['sections'][0]['questions'][0]['id']
            answer_query = select(func.count(AnswerRecord.id)).where(AnswerRecord.question_id == question_id)
            answers = await db.scalar(answer_query)
            correct = await db.scalar(answer_query.where(AnswerRecord.is_correct.is_(True)))
            card_id = pack['sections'][0]['flashcards'][0]['id']
            reviews = await db.scalar(select(ReviewSchedule.review_count).where(ReviewSchedule.card_id == card_id))
            return dict(answers=answers, correct_answers=correct, review_count=reviews)
    finally:
        await engine.dispose()


async def run(check_only):
    # Drop developer credentials before importing settings or spawning workers.
    infrastructure = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'LANG', 'LC_ALL', 'VIRTUAL_ENV', 'LD_LIBRARY_PATH'}
    retained = {key: value for key, value in os.environ.items() if key.upper() in infrastructure}
    os.environ.clear()
    os.environ.update(retained)
    with tempfile.TemporaryDirectory(prefix='knowall-cloud-integration-') as temporary:
        root = Path(temporary)
        os.environ.update(KNOWALL_CLOUD_DATA_ROOT=str(root), JWT_SECRET=secrets.token_urlsafe(64),
                          KNOWALL_IGNORE_ENV_FILE='true', PYTHONUNBUFFERED='1')
        from cloud_entry import app, pool
        from app import database
        from app.core import cloud_workspaces
        import httpx
        import uvicorn
        launches = []
        original_popen = cloud_workspaces.subprocess.Popen

        def tracked_popen(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            if 'cloud_worker:app' in args[0]:
                launches.append((process, time.monotonic(), kwargs['stdout'].name))
            return process

        cloud_workspaces.subprocess.Popen = tracked_popen
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        origin = 'http://127.0.0.1:' + str(sock.getsockname()[1])
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            for _ in range(150):
                if server.started:
                    break
                if serving.done():
                    await serving
                await asyncio.sleep(.1)
            assert server.started, 'Cloud entry startup timeout'
            users, pack, document_id = await prepare(origin, root)
            if check_only:
                await check_learning(origin, users, pack)
                await pool.close()
                async with httpx.AsyncClient(base_url=origin, trust_env=False, timeout=70, headers=users[0]['headers']) as client:
                    session = await client.post('/api/v1/learning/packs/' + pack['id'] + '/session', json={'mode': 'smart'})
                    assert session.status_code == 200
                    assert [x['reason'] for x in session.json()['items']] == ['wrong_answer'], session.text
                assert await persisted_records(root, users[0], pack) == dict(answers=1, correct_answers=0, review_count=1)
                emit('CHECKED', auth=True, upload=True, private_spaces=True, partial_score=.5,
                     wrong_answer=True, card_review=True, exports=3, worker_restart=True)
            else:
                emit('READY', origin=origin, document_id=document_id, pack_id=pack['id'], deck_id=pack['deck_id'],
                     question_id=pack['sections'][0]['questions'][0]['id'],
                     card_id=pack['sections'][0]['flashcards'][0]['id'],
                     accounts=[{'username': u['username'], 'password': PASSWORD} for u in users])
                queue = asyncio.Queue()
                loop = asyncio.get_running_loop()

                def read_commands():
                    for line in sys.stdin:
                        loop.call_soon_threadsafe(queue.put_nowait, line.strip())
                    loop.call_soon_threadsafe(queue.put_nowait, 'quit')

                threading.Thread(target=read_commands, daemon=True).start()
                while True:
                    command = await asyncio.wait_for(queue.get(), timeout=300)
                    if command == 'quit':
                        break
                    if command == 'recycle':
                        await pool.close()
                        emit('RECYCLED')
                    elif command == 'verify-flashcard':
                        values = await persisted_records(root, users[0], pack)
                        assert values == dict(answers=0, correct_answers=0, review_count=1), values
                        emit('FLASHCARD_PERSISTENCE', **values)
                    elif command == 'verify':
                        values = await persisted_records(root, users[0], pack)
                        assert values == dict(answers=2, correct_answers=1, review_count=1), values
                        emit('PERSISTENCE', **values)
                    elif command == 'diagnostics':
                        logs = []
                        for log in root.glob('workspaces/*/worker.log'):
                            tail = log.read_text(encoding='utf-8', errors='replace')[-8000:]
                            logs.append(tail.replace(os.environ['JWT_SECRET'], '<redacted>'))
                        emit('DIAGNOSTICS', worker_logs=logs, launches=[
                            dict(pid=process.pid, returncode=process.poll(), seconds=round(time.monotonic() - started, 2), log=log)
                            for process, started, log in launches])
                    else:
                        raise ValueError('Unknown test control command')
        except Exception:
            # Only test-created logs; redact the shared test signing secret.
            secret = os.environ.get('JWT_SECRET', '')
            for log in root.glob('workspaces/*/worker.log'):
                tail = log.read_text(encoding='utf-8', errors='replace')[-5000:]
                print(tail.replace(secret, '<redacted>') if secret else tail, file=sys.stderr)
            raise
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(serving, timeout=25)
            finally:
                await pool.close()
                await database.engine.dispose()
                sock.close()
                cloud_workspaces.subprocess.Popen = original_popen
                logging.shutdown()
    emit('CLOSED', workers=0, temporary_data_removed=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Run HTTP persistence checks and exit')
    parser.add_argument('--markdown-flashcard', action='store_true', help='Opt in to deterministic rich Anki answer test content')
    args = parser.parse_args()
    MARKDOWN_FLASHCARD = args.markdown_flashcard
    asyncio.run(run(args.check))
