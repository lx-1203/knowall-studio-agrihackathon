"""Same-file uploads must share a result even when their database reads race."""

import asyncio
import builtins
import hashlib
import os
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import documents as documents_api
from app.config import settings
from app.core.parsing import ParsedDocument
from app.database import Base
from app.models import Document, DocumentChunk


@pytest.mark.asyncio
async def test_upload_recovers_when_another_worker_commits_after_lookup(tmp_path, monkeypatch):
    """A winner arriving during parsing must be returned instead of a save 500."""
    from app.core.glossary import extractor

    content = b"shared document bytes"
    digest = hashlib.sha256(content).hexdigest()
    winner_path = tmp_path / "uploads" / digest[:2] / digest
    winner_id = None
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'race.sqlite').as_posix()}"
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def skip_glossary(*args, **kwargs):
        return None

    async def parse_after_winner_commits(local_path, *args, **kwargs):
        nonlocal winner_id
        assert Path(local_path) == winner_path
        async with session_factory() as winner_db:
            winner = Document(
                filename="winner.txt", file_type="txt", file_size=len(content),
                sha256=digest, local_path=local_path,
                status="ready", page_count=1, metadata_={},
            )
            winner_db.add(winner)
            await winner_db.flush()
            winner_db.add(DocumentChunk(
                doc_id=winner.id, chunk_index=0, content_hash=digest,
                text_content="winner text", token_count=2, page_range="p1",
            ))
            await winner_db.commit()
            winner_id = winner.id
        return ParsedDocument(text="losing parse text", page_count=1)

    monkeypatch.setattr(settings, "document_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "kaf_publish_enabled", False)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)
    monkeypatch.setattr(documents_api.parser, "parse", parse_after_winner_commits)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as loser_db:
            response = await documents_api.upload_document(
                UploadFile(file=BytesIO(content), filename="loser.txt"), db=loser_db
            )
            count = (await loser_db.execute(
                select(func.count()).select_from(Document)
            )).scalar_one()
    finally:
        await engine.dispose()

    assert response["document_id"] == winner_id
    assert response["duplicate"] is True
    assert response["total_chunks"] == 1
    assert response["chunks"][0]["preview"] == "winner text"
    assert count == 1
    assert winner_path.read_bytes() == content
    assert list(winner_path.parent.iterdir()) == [winner_path]


@pytest.mark.asyncio
async def test_simultaneous_uploads_parse_same_file_once(tmp_path, monkeypatch):
    """Same-process requests should share the committed parse result."""
    from app.core.glossary import extractor

    content = b"same file from two requests"
    parse_entered = asyncio.Event()
    release_parse = asyncio.Event()
    parse_calls = 0

    async def parse_file(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        parse_entered.set()
        await release_parse.wait()
        return ParsedDocument(text="A complete document with useful text.", page_count=1)

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(settings, "document_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "kaf_publish_enabled", False)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)
    monkeypatch.setattr(documents_api.parser, "parse", parse_file)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'concurrent.sqlite').as_posix()}"
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as first_db, session_factory() as second_db:
            first_task = asyncio.create_task(documents_api.upload_document(
                UploadFile(file=BytesIO(content), filename="first.txt"), db=first_db
            ))
            await asyncio.wait_for(parse_entered.wait(), timeout=5)
            second_task = asyncio.create_task(documents_api.upload_document(
                UploadFile(file=BytesIO(content), filename="second.txt"), db=second_db
            ))
            try:
                await asyncio.sleep(0.05)
            finally:
                release_parse.set()
            first, second = await asyncio.wait_for(
                asyncio.gather(first_task, second_task), timeout=10
            )
            count = (await first_db.execute(
                select(func.count()).select_from(Document)
            )).scalar_one()
    finally:
        await engine.dispose()

    assert first["document_id"] == second["document_id"]
    assert second["duplicate"] is True
    assert parse_calls == 1
    assert count == 1


@pytest.mark.asyncio
async def test_different_uploads_can_parse_concurrently(tmp_path, monkeypatch):
    """The same-file guard must not queue unrelated files behind one OCR job."""
    from app.core.glossary import extractor

    all_parsers_entered = asyncio.Event()
    release_parse = asyncio.Event()
    parse_calls = 0

    async def parse_file(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        if parse_calls == 2:
            all_parsers_entered.set()
        await release_parse.wait()
        return ParsedDocument(text="Independent document content.", page_count=1)

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(settings, "document_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "kaf_publish_enabled", False)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)
    monkeypatch.setattr(documents_api.parser, "parse", parse_file)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'independent.sqlite').as_posix()}"
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as first_db, session_factory() as second_db:
            tasks = [
                asyncio.create_task(documents_api.upload_document(
                    UploadFile(file=BytesIO(payload), filename=name), db=session
                ))
                for payload, name, session in (
                    (b"first bytes", "first.txt", first_db),
                    (b"second bytes", "second.txt", second_db),
                )
            ]
            try:
                await asyncio.wait_for(all_parsers_entered.wait(), timeout=5)
            finally:
                release_parse.set()
                responses = await asyncio.gather(*tasks)
    finally:
        await engine.dispose()

    assert parse_calls == 2
    assert responses[0]["document_id"] != responses[1]["document_id"]


@pytest.mark.asyncio
async def test_upload_keeps_existing_source_complete_while_writing(tmp_path, monkeypatch):
    """A second process reading the hash path must never see a truncated file."""
    from app.core.glossary import extractor

    content = b"complete bytes for the parser"
    digest = hashlib.sha256(content).hexdigest()
    upload_dir = tmp_path / "uploads"
    file_dir = upload_dir / digest[:2]
    file_dir.mkdir(parents=True)
    canonical = file_dir / digest
    old_content = b"older complete content"
    canonical.write_bytes(old_content)
    real_open = builtins.open
    real_fdopen = os.fdopen
    observed_writes = 0

    class ObservedWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, payload):
            nonlocal observed_writes
            observed_writes += 1
            assert canonical.read_bytes() == old_content
            return self.stream.write(payload)

        def __getattr__(self, name):
            return getattr(self.stream, name)

    def observed_open(path, mode="r", *args, **kwargs):
        stream = real_open(path, mode, *args, **kwargs)
        if mode == "wb" and Path(path).parent == file_dir:
            return ObservedWriter(stream)
        return stream

    def observed_fdopen(fd, mode="r", *args, **kwargs):
        stream = real_fdopen(fd, mode, *args, **kwargs)
        return ObservedWriter(stream) if mode == "wb" else stream

    async def parse_file(*args, **kwargs):
        return ParsedDocument(text="Stored file is complete.", page_count=1)

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(settings, "document_dir", str(upload_dir))
    monkeypatch.setattr(settings, "kaf_publish_enabled", False)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)
    monkeypatch.setattr(documents_api.parser, "parse", parse_file)
    monkeypatch.setattr(builtins, "open", observed_open)
    monkeypatch.setattr(os, "fdopen", observed_fdopen)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as db:
            response = await documents_api.upload_document(
                UploadFile(file=BytesIO(content), filename="source.txt"), db=db
            )
    finally:
        await engine.dispose()

    assert response["status"] == "ready"
    assert observed_writes == 1
    assert canonical.read_bytes() == content
    assert list(file_dir.iterdir()) == [canonical]
