"""Real routes, temporary SQLite and original APKG encoder; no model calls."""
import asyncio
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import update

from app.api import flashcards
from app.core.auth import get_optional_user
from app.database import Base, get_db
from app.models import Deck, Flashcard


@pytest.fixture
async def exports(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'cards.sqlite').as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([Deck(id='english', name='biology', card_count=1), Deck(id='chinese', name='细胞结构', card_count=1), Deck(id='slashes', name='../课程/章节', card_count=1)])
        await db.flush()
        db.add_all([Flashcard(id=id + '-card', deck_id=id, card_type='qa', front=id + '-private-question', back='线粒体供能', tags=['biology']) for id in ['english', 'chinese', 'slashes']])
        await db.commit()
    async def database():
        async with factory() as db:
            yield db
    paths = []
    original = flashcards.export_apkg
    def observe(cards, name, output_path=None, **kwargs):
        result = original(cards, name, output_path, **kwargs)
        paths.append(Path(result))
        return result
    monkeypatch.setattr(flashcards, 'export_apkg', observe)
    monkeypatch.setattr('app.core.anki_export.settings.export_dir', str(tmp_path / 'exports'))
    app = FastAPI()
    app.include_router(flashcards.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_optional_user] = lambda: SimpleNamespace(id='temporary-learner')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://export') as client:
        yield client, paths, tmp_path
    await engine.dispose()


def validate_apkg(body, tmp_path, identifier):
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert set(archive.namelist()) == {'collection.anki2', 'media'}
        assert json.loads(archive.read('media')) == {}
        database = tmp_path / (identifier + '.anki2')
        database.write_bytes(archive.read('collection.anki2'))
    with sqlite3.connect(database) as db:
        assert db.execute('select flds from notes').fetchone()[0] == identifier + '-private-question\x1f<p>线粒体供能</p>\n'
        assert db.execute('select count(*) from cards').fetchone()[0] == 1


@pytest.mark.parametrize('identifier,name', [('english', 'biology'), ('chinese', '细胞结构'), ('slashes', '../课程/章节')])
async def test_actual_export_valid_content_download_name_and_cleanup(exports, identifier, name):
    client, paths, tmp_path = exports
    response = await client.get('/api/v1/flashcards/export/anki/' + identifier)
    assert response.status_code == 200, response.text
    assert name + '.apkg' in unquote(response.headers['content-disposition'])
    validate_apkg(response.content, tmp_path, identifier)
    assert paths and all(not path.exists() for path in paths), 'Successful export must remove temporary APKG after streaming'


async def test_parallel_same_deck_exports_have_independent_paths_and_cleanup(exports):
    client, paths, tmp_path = exports
    results = await asyncio.gather(*[client.get('/api/v1/flashcards/export/anki/english') for _ in range(3)])
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert len(set(paths)) == 3, 'Concurrent exports must not overwrite or unlink another request file'
    assert all(not p.exists() for p in paths)
    for response in results:
        validate_apkg(response.content, tmp_path, 'english')


async def test_successful_response_removes_export_file(exports):
    client, paths, _ = exports
    response = await client.get('/api/v1/flashcards/export/anki/english')
    assert response.status_code == 200
    assert paths and all(not p.exists() for p in paths), 'Successful response left APKG on disk'


async def test_export_failure_removes_partial_file_and_preserves_server_error(exports, monkeypatch):
    client, _, _ = exports
    partial = []
    def fail(cards, name, output_path=None, **kwargs):
        if output_path:
            path = Path(output_path); path.write_bytes(b'partial'); partial.append(path)
        raise RuntimeError('fixture encoder failure')
    monkeypatch.setattr(flashcards, 'export_apkg', fail)
    response = await client.get('/api/v1/flashcards/export/anki/english')
    assert response.status_code == 500
    assert partial and not partial[0].exists()


async def test_missing_deck_does_not_invoke_encoder(exports):
    client, paths, _ = exports
    assert (await client.get('/api/v1/flashcards/export/anki/missing')).status_code == 404
    assert not paths


async def test_pending_encoder_does_not_block_other_cloud_requests(exports, monkeypatch):
    import threading
    client, _, _ = exports
    loop = asyncio.get_running_loop()
    started, release = asyncio.Event(), threading.Event()
    original = flashcards.export_apkg
    def held_encoder(*args, **kwargs):
        loop.call_soon_threadsafe(started.set)
        if not release.wait(timeout=2):
            raise RuntimeError('Event loop blocked while encoder was pending')
        return original(*args, **kwargs)
    monkeypatch.setattr(flashcards, 'export_apkg', held_encoder)
    exporting = asyncio.create_task(client.get('/api/v1/flashcards/export/anki/english'))
    try:
        await asyncio.wait_for(started.wait(), 4)
        assert not exporting.done(), 'Encoder must yield the cloud event loop'
        assert (await client.get('/api/v1/flashcards/decks')).status_code == 200
    finally:
        release.set()
        response = await exporting
    assert response.status_code == 200


async def test_real_route_parallel_rich_answers_are_consumed_by_anki(exports):
    from anki.collection import Collection, ImportAnkiPackageRequest
    from lxml.html import fromstring
    client, paths, tmp_path = exports
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'cards.sqlite').as_posix())
    try:
        async with async_sessionmaker(engine)() as db:
            await db.execute(update(Flashcard).where(Flashcard.id == 'chinese-card').values(
                card_type='compare', back='| 结构 | 功能 |\n|---|---|\n| 线粒体 | **供能** |\n\n```python\n    cell_id = 1\n```'))
            await db.commit()
    finally:
        await engine.dispose()
    responses = await asyncio.gather(*[client.get('/api/v1/flashcards/export/anki/chinese') for _ in range(3)])
    assert all(response.status_code == 200 for response in responses)
    assert len(set(paths)) == 3 and all(not path.exists() for path in paths)
    for i, response in enumerate(responses):
        package = tmp_path / f'route-rich-{i}.apkg'; package.write_bytes(response.content)
        col = Collection(str(tmp_path / f'route-consumer-{i}.anki2'))
        try:
            for _ in range(2):
                col.import_anki_package(ImportAnkiPackageRequest(package_path=str(package)))
            assert col.note_count() == col.card_count() == 1
            dom = fromstring(col.get_card(col.find_cards('')[0]).answer())
            assert [x.text_content() for x in dom.xpath('//table//td')] == ['线粒体', '供能']
            assert dom.xpath('//strong')[0].text_content() == '供能'
            assert dom.xpath('//pre/code')[0].text_content() == '    cell_id = 1\n'
        finally:
            col.close()
