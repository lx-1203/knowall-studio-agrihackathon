"""The web importer must validate every redirect and pin the checked address."""

import ipaddress
import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import documents as documents_api
from app.config import settings
from app.core.parsing import ParsedDocument
from app.core.parsing import DocumentParser
from app.core.parsing.docreader.document import Document
from app.core.parsing.docreader.ssrf import SSRFBlockedError
from app.core.parsing.docreader import ssrf, ssrf_proxy
from app.database import Base
from app.models import Document as StoredDocument


def _empty_document_db():
    """A database stub for tests that intentionally stop before persistence."""
    return SimpleNamespace(execute=AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
    ))


def test_checked_address_resolves_domain_once(monkeypatch):
    resolutions = []

    def resolve(host):
        resolutions.append(host)
        return ((ipaddress.ip_address("93.184.215.14"),), None)

    monkeypatch.setattr(ssrf, "_resolve_host_ips", resolve)

    assert ssrf_proxy.checked_address("https://example.com/article") == ("93.184.215.14", 443)
    assert resolutions == ["example.com"]


@pytest.mark.parametrize("url", ["http://[::1", "http://example.com:bad/"])
def test_malformed_web_url_is_rejected_as_bad_input(url):
    with pytest.raises(SSRFBlockedError):
        ssrf.assert_url_allowed(url)
    with pytest.raises(HTTPException) as excinfo:
        documents_api._validate_url_safe(url)
    assert excinfo.value.status_code == 400


def test_web_fetch_rejects_private_redirect_before_request(monkeypatch):
    from app.core.parsing.docreader import web_parser

    requested = []

    def checked(url):
        if url == "http://2130706433/secret":
            raise SSRFBlockedError("internal address")
        return "93.184.215.14", 443

    def request_once(url, address):
        requested.append(url)
        return 302, {"location": "http://2130706433/secret"}, b""

    monkeypatch.setattr(web_parser, "checked_address", checked)
    monkeypatch.setattr(web_parser, "_request_once", request_once)

    with pytest.raises(SSRFBlockedError):
        web_parser.fetch_web_page("https://example.com/start")
    assert requested == ["https://example.com/start"]


def test_web_fetch_preserves_headings_after_public_redirect(monkeypatch):
    from app.core.parsing.docreader import web_parser

    checked = []
    requested = []

    def check(url):
        checked.append(url)
        return "93.184.215.14", 443

    def request_once(url, address):
        requested.append((url, address))
        if url.endswith("/start"):
            return 302, {"location": "/article"}, b""
        return 200, {"content-type": "text/html; charset=utf-8"}, (
            "<html><body><h1>计算机网络</h1><h2>传输层</h2>"
            "<p>TCP 通过三次握手建立连接，学习时要区分 SYN 和 ACK。</p></body></html>"
        ).encode("utf-8")

    monkeypatch.setattr(web_parser, "checked_address", check)
    monkeypatch.setattr(web_parser, "_request_once", request_once)

    document = web_parser.fetch_web_page("https://example.com/start")

    assert checked == ["https://example.com/start", "https://example.com/article"]
    assert requested == [
        ("https://example.com/start", ("93.184.215.14", 443)),
        ("https://example.com/article", ("93.184.215.14", 443)),
    ]
    assert "# 计算机网络" in document.content
    assert "## 传输层" in document.content
    assert "TCP 通过三次握手" in document.content


def test_web_fetch_normalizes_url_without_scheme(monkeypatch):
    from app.core.parsing.docreader import web_parser

    seen = []

    def checked(url):
        seen.append(url)
        return "93.184.215.14", 443

    def request_once(url, address):
        seen.append(url)
        return 200, {"content-type": "text/html; charset=utf-8"}, b"<h1>Course</h1><p>Lesson text.</p>"

    monkeypatch.setattr(web_parser, "checked_address", checked)
    monkeypatch.setattr(web_parser, "_request_once", request_once)

    web_parser.fetch_web_page("example.com/article")
    assert seen == ["https://example.com/article", "https://example.com/article"]


def test_real_http_fetch_uses_checked_numeric_address_and_validates_redirect(monkeypatch):
    from app.core.parsing.docreader import web_parser

    visited = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            visited.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/article")
                self.end_headers()
                return
            body = "<h1>本机课程</h1><p>真正通过 HTTP 读取的正文。</p>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SSRF_WHITELIST", "127.0.0.1")
    ssrf.reset_ssrf_whitelist_cache_for_test()
    try:
        document = web_parser.fetch_web_page(f"http://127.0.0.1:{server.server_port}/start")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        ssrf.reset_ssrf_whitelist_cache_for_test()

    assert visited == ["/start", "/article"]
    assert "# 本机课程" in document.content
    assert "真正通过 HTTP 读取" in document.content


@pytest.mark.asyncio
async def test_url_parser_uses_safe_fetcher(monkeypatch):
    from app.core.parsing.docreader import web_parser

    urls = []

    def fetch(url):
        urls.append(url)
        return Document(content="# 网络课程\n\n这是课程正文。", metadata={"source_format": "web"})

    monkeypatch.setattr(web_parser, "fetch_web_page", fetch)
    parsed = await DocumentParser().parse("https://example.com/course", "url")

    assert urls == ["https://example.com/course"]
    assert parsed.text.startswith("# 网络课程")
    assert parsed.metadata["parser"] == "docreader.web"


@pytest.mark.asyncio
async def test_url_import_reports_blocked_redirect_as_bad_request(monkeypatch):
    async def blocked(*args, **kwargs):
        raise SSRFBlockedError("redirect reached a private address")

    monkeypatch.setattr(documents_api, "_validate_url_safe", lambda url: None)
    monkeypatch.setattr(documents_api.parser, "parse", blocked)

    with pytest.raises(HTTPException) as excinfo:
        await documents_api.import_from_url(
            documents_api.URLImportRequest(url="https://example.com/start"), db=_empty_document_db()
        )
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_url_validation_does_not_block_event_loop(monkeypatch):
    started = threading.Event()

    def slow_validation(url):
        started.set()
        time.sleep(0.2)  # DNS resolution can be slow on an untrusted hostname.

    async def stop_after_validation(*args, **kwargs):
        raise SSRFBlockedError("test stops before saving")

    monkeypatch.setattr(documents_api, "_validate_url_safe", slow_validation)
    monkeypatch.setattr(documents_api.parser, "parse", stop_after_validation)

    task = asyncio.create_task(documents_api.import_from_url(
        documents_api.URLImportRequest(url="https://example.com/"), db=_empty_document_db()
    ))
    await asyncio.sleep(0.02)
    assert started.is_set()
    blocked_loop = task.done()
    with pytest.raises(HTTPException):
        await task
    assert not blocked_loop, "a synchronous DNS check blocked the event loop"


@pytest.mark.asyncio
async def test_importing_same_url_twice_reuses_existing_document(tmp_path, monkeypatch):
    from app.core.glossary import extractor

    parse_calls = 0

    async def parse_page(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        return ParsedDocument(text="# 网络课程\n\nTCP 三次握手建立连接。", page_count=1)

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(documents_api, "_validate_url_safe", lambda url: None)
    monkeypatch.setattr(documents_api.parser, "parse", parse_page)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)
    monkeypatch.setattr(settings, "document_dir", str(tmp_path))

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        request = documents_api.URLImportRequest(url="https://example.com/course")
        async with session_factory() as session:
            first = await documents_api.import_from_url(request, db=session)
        async with session_factory() as session:
            second = await documents_api.import_from_url(request, db=session)
            count = (await session.execute(select(func.count()).select_from(StoredDocument))).scalar_one()
    finally:
        await engine.dispose()

    assert second["document_id"] == first["document_id"]
    assert second["chunks"] == first["chunks"]
    assert second["duplicate"] is True
    assert parse_calls == 1
    assert count == 1


@pytest.mark.asyncio
async def test_different_urls_can_store_the_same_chunk_text(tmp_path, monkeypatch):
    from app.core.glossary import extractor

    async def parse_page(*args, **kwargs):
        return ParsedDocument(text="# 通用说明\n\n相同的课程资料正文。", page_count=1)

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(documents_api, "_validate_url_safe", lambda url: None)
    monkeypatch.setattr(documents_api.parser, "parse", parse_page)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)
    monkeypatch.setattr(settings, "document_dir", str(tmp_path))

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            first = await documents_api.import_from_url(
                documents_api.URLImportRequest(url="https://example.com/a"), db=session
            )
        async with session_factory() as session:
            second = await documents_api.import_from_url(
                documents_api.URLImportRequest(url="https://example.com/b"), db=session
            )
            count = (await session.execute(select(func.count()).select_from(StoredDocument))).scalar_one()
    finally:
        await engine.dispose()

    assert first["document_id"] != second["document_id"]
    assert count == 2


@pytest.mark.asyncio
async def test_concurrent_same_url_import_keeps_raw_file_in_sync(tmp_path, monkeypatch):
    from app.core.glossary import extractor

    url = "https://example.com/concurrent"
    first_parsing = asyncio.Event()
    release_first = asyncio.Event()
    parse_calls = 0

    async def parse_page(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        if parse_calls == 1:
            first_parsing.set()
            await release_first.wait()
            return ParsedDocument(text="# 网络课程\n\n第一个网页版本。", page_count=1)
        return ParsedDocument(text="# 网络课程\n\n第二个网页版本。", page_count=1)

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(documents_api, "_validate_url_safe", lambda url: None)
    monkeypatch.setattr(documents_api.parser, "parse", parse_page)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)
    monkeypatch.setattr(settings, "document_dir", str(tmp_path))

    db_path = (tmp_path / "documents.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        request = documents_api.URLImportRequest(url=url)
        async with session_factory() as first_db, session_factory() as second_db:
            first_task = asyncio.create_task(documents_api.import_from_url(request, db=first_db))
            await asyncio.wait_for(first_parsing.wait(), 5)
            try:
                second = await asyncio.wait_for(
                    documents_api.import_from_url(request, db=second_db), 5
                )
            finally:
                release_first.set()
            first = (await asyncio.gather(first_task, return_exceptions=True))[0]
        async with session_factory() as check_db:
            document = (await check_db.execute(select(StoredDocument))).scalar_one()
            count = (await check_db.execute(select(func.count()).select_from(StoredDocument))).scalar_one()
            raw_text = Path(document.local_path).read_text(encoding="utf-8")
    finally:
        await engine.dispose()

    assert raw_text == f"URL: {url}\n\n# 网络课程\n第二个网页版本。"
    assert isinstance(first, dict), first
    assert first["document_id"] == second["document_id"] == document.id
    assert first["duplicate"] is True
    assert count == 1


@pytest.mark.asyncio
async def test_url_and_file_with_same_bytes_get_distinct_documents(tmp_path, monkeypatch):
    from app.core.glossary import extractor

    url = "https://example.com/course"

    async def parse_source(path, file_type):
        if file_type == "url":
            return ParsedDocument(text="# 网页课程\n\n网页专有正文。", page_count=1)
        return ParsedDocument(text="# 本地文件\n\n文件专有正文。", page_count=1)

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(documents_api, "_validate_url_safe", lambda url: None)
    monkeypatch.setattr(documents_api.parser, "parse", parse_source)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)
    monkeypatch.setattr(settings, "document_dir", str(tmp_path))
    monkeypatch.setattr(settings, "kaf_publish_enabled", False)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            imported = await documents_api.import_from_url(
                documents_api.URLImportRequest(url=url), db=session
            )
        async with session_factory() as session:
            uploaded = await documents_api.upload_document(
                UploadFile(filename="source.txt", file=BytesIO(url.encode())), db=session
            )
            documents = (await session.execute(select(StoredDocument))).scalars().all()
    finally:
        await engine.dispose()

    assert imported["document_id"] != uploaded["document_id"]
    assert len(documents) == 2
    imported_doc = next(d for d in documents if d.id == imported["document_id"])
    uploaded_doc = next(d for d in documents if d.id == uploaded["document_id"])
    assert imported_doc.sha256.startswith("u") and len(imported_doc.sha256) == 64
    assert uploaded_doc.sha256 == hashlib.sha256(url.encode()).hexdigest()
    assert Path(imported_doc.local_path).read_text(encoding="utf-8").endswith("网页专有正文。")
    assert Path(uploaded_doc.local_path).read_text(encoding="utf-8") == url


@pytest.mark.asyncio
async def test_url_import_reuses_legacy_url_hash(tmp_path, monkeypatch):
    url = "https://example.com/legacy"

    async def unexpected_parse(*args, **kwargs):
        pytest.fail("An existing URL document must not be fetched again")

    monkeypatch.setattr(documents_api, "_validate_url_safe", lambda url: None)
    monkeypatch.setattr(documents_api.parser, "parse", unexpected_parse)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            legacy = StoredDocument(
                filename=url,
                file_type="url",
                sha256=hashlib.sha256(url.encode()).hexdigest(),
                status="ready",
                metadata_={"source_url": url},
            )
            session.add(legacy)
            await session.commit()
            legacy_id = legacy.id
        async with session_factory() as session:
            result = await documents_api.import_from_url(
                documents_api.URLImportRequest(url=url), db=session
            )
            count = (await session.execute(select(func.count()).select_from(StoredDocument))).scalar_one()
    finally:
        await engine.dispose()

    assert result["duplicate"] is True
    assert result["document_id"] == legacy_id
    assert count == 1


@pytest.mark.asyncio
async def test_upload_does_not_overwrite_legacy_url_and_reuses_file_key_after_deletion(tmp_path, monkeypatch):
    from app.core.glossary import extractor

    url = "https://example.com/legacy-collision"
    raw_sha256 = hashlib.sha256(url.encode()).hexdigest()
    legacy_path = tmp_path / raw_sha256[:2] / raw_sha256
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(f"URL: {url}\n\n旧网页内容", encoding="utf-8")
    parse_calls = 0

    async def parse_file(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        return ParsedDocument(text="# 本地文件\n\n独立上传内容。", page_count=1)

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(documents_api.parser, "parse", parse_file)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)
    monkeypatch.setattr(settings, "document_dir", str(tmp_path))
    monkeypatch.setattr(settings, "kaf_publish_enabled", False)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            legacy = StoredDocument(
                filename=url,
                file_type="url",
                sha256=raw_sha256,
                local_path=str(legacy_path),
                status="ready",
                metadata_={"source_url": url},
            )
            session.add(legacy)
            await session.commit()
            legacy_id = legacy.id
        async with session_factory() as session:
            first = await documents_api.upload_document(
                UploadFile(filename="source.txt", file=BytesIO(url.encode())), db=session
            )
            assert first["document_id"] != legacy_id
            first_doc = (await session.execute(
                select(StoredDocument).where(StoredDocument.id == first["document_id"])
            )).scalar_one()
            assert first_doc.sha256.startswith("f") and len(first_doc.sha256) == 64
            assert Path(first_doc.local_path).read_text(encoding="utf-8") == url
            assert legacy_path.read_text(encoding="utf-8") == f"URL: {url}\n\n旧网页内容"

            await session.delete(legacy)
            await session.commit()
        async with session_factory() as session:
            second = await documents_api.upload_document(
                UploadFile(filename="source.txt", file=BytesIO(url.encode())), db=session
            )
            Path(first_doc.local_path).unlink()
            restored = await documents_api.upload_document(
                UploadFile(filename="source.txt", file=BytesIO(url.encode())), db=session
            )
            count = (await session.execute(select(func.count()).select_from(StoredDocument))).scalar_one()
    finally:
        await engine.dispose()

    assert second["document_id"] == first["document_id"]
    assert second["duplicate"] is True
    assert restored["document_id"] == first["document_id"]
    assert Path(first_doc.local_path).read_text(encoding="utf-8") == url
    assert parse_calls == 1
    assert count == 1


@pytest.mark.asyncio
async def test_failed_url_commit_does_not_delete_later_winners_raw_file(tmp_path, monkeypatch):
    from app.core.glossary import extractor

    url = "https://example.com/commit-race"
    parse_calls = 0

    async def parse_page(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        return ParsedDocument(
            text=f"# 网络课程\n\n第{parse_calls}个网页版本。", page_count=1
        )

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(documents_api, "_validate_url_safe", lambda url: None)
    monkeypatch.setattr(documents_api.parser, "parse", parse_page)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)
    monkeypatch.setattr(settings, "document_dir", str(tmp_path))

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        request = documents_api.URLImportRequest(url=url)
        async with session_factory() as failed_db, session_factory() as winner_db:
            real_rollback = failed_db.rollback
            winner = None

            async def fail_commit():
                raise RuntimeError("simulated commit failure")

            async def rollback_then_import():
                nonlocal winner
                await real_rollback()
                winner = await documents_api.import_from_url(request, db=winner_db)

            monkeypatch.setattr(failed_db, "commit", fail_commit)
            monkeypatch.setattr(failed_db, "rollback", rollback_then_import)
            with pytest.raises(RuntimeError, match="simulated commit failure"):
                await documents_api.import_from_url(request, db=failed_db)

            document = (await winner_db.execute(select(StoredDocument))).scalar_one()
            assert winner["document_id"] == document.id
            raw_path = Path(document.local_path)
            assert raw_path.read_text(encoding="utf-8") == (
                f"URL: {url}\n\n# 网络课程\n第2个网页版本。"
            )
    finally:
        await engine.dispose()
