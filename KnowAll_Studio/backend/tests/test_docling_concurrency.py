"""Docling work should not crowd the event loop's shared worker pool."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.core.parsing.docling_parser import DoclingParser, StructuredDocument


@pytest.mark.asyncio
async def test_queued_docling_parses_leave_default_workers_available(monkeypatch):
    loop = asyncio.get_running_loop()
    prior_executor = loop._default_executor
    shared_pool = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(shared_pool)
    entered = Event()
    release = Event()

    def slow_parse(_path, _file_type):
        entered.set()
        if not release.wait(3):
            raise TimeoutError("test did not release Docling worker")
        return StructuredDocument(text="ready", page_count=1)

    parser = DoclingParser()
    monkeypatch.setattr(parser, "_parse_sync", slow_parse)
    tasks = [asyncio.create_task(parser.parse("unused.pdf", "pdf")) for _ in range(3)]
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.sleep(0.05)
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "free"), 0.2) == "free"
    finally:
        release.set()
        await asyncio.gather(*tasks)
        loop._default_executor = prior_executor
        shared_pool.shutdown(wait=True)
