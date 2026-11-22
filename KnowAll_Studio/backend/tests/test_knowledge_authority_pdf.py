"""Real digital PDF bytes and process/HTTP/SQL boundaries; no paid calls."""
import asyncio
import json
import multiprocessing
import os
import time
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from app.core import knowledge_authority as core
from tests.test_knowledge_authority import authority, response, run_search


def pdf_bytes(texts, *, encrypted=False):
    with fitz.open() as doc:
        doc.set_metadata({'title': 'Calculus lecture'})
        for text in texts:
            page = doc.new_page()
            page.insert_textbox(fitz.Rect(40, 40, 550, 700), text,
                                fontname='china-s' if any('\u3400' <= c <= '\u9fff' for c in text) else 'helv', fontsize=11)
        return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='password') if encrypted else doc.tobytes()


def serve(monkeypatch, body, media='application/pdf'):
    monkeypatch.setattr(core, 'checked_address', lambda url: ('93.184.216.34', 443))
    monkeypatch.setattr(core, '_request_page', lambda *args: (
        200, {'content-type': media, 'content-encoding': 'identity'}, body))


def test_fetch_accepts_real_pdf_and_keeps_actual_page_numbers(monkeypatch):
    body = pdf_bytes(['Introduction to calculus.',
                      'Calculus studies change. The derivative of a function is its instantaneous rate of change.'])
    serve(monkeypatch, body)
    page = core._fetch_authority_page('https://math.mit.edu/lecture.pdf')
    assert [p['number'] for p in page['pages']] == [1, 2]
    assert 'instantaneous' in page['pages'][1]['text']
    assert page['title'] == 'Calculus lecture'


def test_pdf_definition_uses_context_on_the_same_physical_page(monkeypatch):
    body = pdf_bytes(['The derivative of a function is its instantaneous rate of change.\n' +
                      'Study exercises. ' * 30 + '\nMathematics and calculus.'])
    serve(monkeypatch, body)
    page = core._fetch_authority_page('https://math.mit.edu/lecture.pdf')
    concept = SimpleNamespace(name='derivative', subject='mathematics', topic='calculus')
    assert core.verified_excerpt(page['pages'][0], concept) == 'The derivative of a function is its instantaneous rate of change.'


def test_html_keeps_its_nearby_context_requirement():
    text = ('The derivative of a function is a fictional character in a novel.\n' +
            'Unrelated announcement. ' * 30 + '\nMathematics and calculus.')
    concept = SimpleNamespace(name='derivative', subject='mathematics', topic='calculus')
    assert core.verified_excerpt({'text': text}, concept) is None


async def test_exact_pdf_page_is_persisted_as_external_source(authority, monkeypatch):
    a = authority
    url = 'https://math.mit.edu/lecture.pdf'
    body = pdf_bytes(['数学微分课程。', '数学微分中，导数是函数在一点的瞬时变化率，由差商极限定义。'])
    serve(monkeypatch, body)
    monkeypatch.setattr(core, 'fetch_authority_page', lambda url: asyncio.to_thread(core._fetch_authority_page, url))
    a.results.append({'url': url, 'snippet': '假的上传摘要不能成为出处'})
    await run_search(a)
    result = await response(a)
    assert result['status'] == 'found' and len(result['sources']) == 1
    source = result['sources'][0]
    assert source['url'] == url + '#page=2' and '2' in source['title']
    assert '差商极限' in source['excerpt'] and '假的' not in source['excerpt']
    async with a.factory() as db:
        assert (await db.get(a.model, 'public')).sources == result['sources']


async def test_pdf_page_context_cannot_be_borrowed_from_another_page(authority, monkeypatch):
    a = authority
    body = pdf_bytes(['数学微分课程。', '导数是某故事中一个虚构人物的称号。'])
    serve(monkeypatch, body)
    monkeypatch.setattr(core, 'fetch_authority_page', lambda url: asyncio.to_thread(core._fetch_authority_page, url))
    a.results.append({'url': 'https://math.mit.edu/lecture.pdf'})
    await run_search(a)
    assert await response(a) == {'status': 'searching', 'sources': []}


@pytest.mark.parametrize('kind,match', [('corrupt', 'invalid'), ('encrypted', 'password'), ('empty', 'readable')])
def test_pdf_failure_never_returns_partial_or_fake_evidence(monkeypatch, kind, match):
    body = (b'%PDF-1.7\nnot a PDF' if kind == 'corrupt' else
            pdf_bytes(['hello'], encrypted=True) if kind == 'encrypted' else pdf_bytes(['']))
    serve(monkeypatch, body)
    with pytest.raises(ValueError, match=match):
        core._fetch_authority_page('https://math.mit.edu/lecture.pdf')


@pytest.mark.parametrize('kind,match', [('pages', 'page limit'), ('text', 'text limit'), ('bytes', 'byte limit')])
def test_pdf_budgets_reject_the_whole_document(monkeypatch, kind, match):
    from app.core import knowledge_authority_pdf as parser
    body = pdf_bytes(['Calculus is mathematics.'] * 3)
    limits = {'max_pages': 2} if kind == 'pages' else {'max_chars': 5} if kind == 'text' else {'max_bytes': 5}
    with pytest.raises(ValueError, match=match):
        parser.parse_pdf(body, timeout=8, **limits)


def test_pdf_media_type_requires_actual_pdf_signature(monkeypatch):
    serve(monkeypatch, b'<html>Calculus: derivative is a made-up definition.</html>')
    with pytest.raises(ValueError, match='signature'):
        core._fetch_authority_page('https://math.mit.edu/lecture.pdf')


def test_pdf_redirect_cannot_reach_a_private_address(monkeypatch):
    serve(monkeypatch, pdf_bytes(['Calculus.']))
    monkeypatch.setattr(core, '_request_page', lambda *args: (302, {'location': 'http://127.0.0.1/file.pdf'}, b''))
    with pytest.raises(ValueError):
        core._fetch_authority_page('https://math.mit.edu/lecture.pdf')


def stalled_worker(sender, body, max_pages, max_chars):
    """Actual spawned test process: prove it started before timing out."""
    Path(os.environ['KNOWLEDGE_PDF_TEST_PID']).write_text(str(os.getpid()))
    time.sleep(60)


def test_pdf_timeout_terminates_worker_and_next_parse_recovers(monkeypatch, tmp_path):
    from app.core import knowledge_authority_pdf as parser
    before = {child.pid for child in multiprocessing.active_children()}
    pid_file = tmp_path / 'started.pid'
    monkeypatch.setenv('KNOWLEDGE_PDF_TEST_PID', str(pid_file))
    monkeypatch.setattr(parser, '_worker', stalled_worker)
    with pytest.raises(TimeoutError):
        parser.parse_pdf(pdf_bytes(['Calculus.']), timeout=5)
    assert pid_file.exists(), 'The timeout must occur after the actual worker started.'
    assert {child.pid for child in multiprocessing.active_children()} <= before
    monkeypatch.undo()
    result = parser.parse_pdf(pdf_bytes(['Calculus is mathematics.']), timeout=8)
    assert result['pages'][0]['number'] == 1
    assert {child.pid for child in multiprocessing.active_children()} <= before


def test_cancel_signal_terminates_pdf_worker(monkeypatch, tmp_path):
    import threading
    from app.core import knowledge_authority_pdf as parser
    before = {child.pid for child in multiprocessing.active_children()}
    pid_file = tmp_path / 'cancel.pid'
    monkeypatch.setenv('KNOWLEDGE_PDF_TEST_PID', str(pid_file))
    monkeypatch.setattr(parser, '_worker', stalled_worker)
    cancelled = threading.Event()
    def cancel_after_start():
        until = time.monotonic() + 6
        while not pid_file.exists() and time.monotonic() < until:
            time.sleep(.02)
        cancelled.set()
    thread = threading.Thread(target=cancel_after_start)
    thread.start()
    try:
        with pytest.raises(ValueError, match='cancelled'):
            parser.parse_pdf(pdf_bytes(['Calculus.']), timeout=8, cancelled=cancelled)
    finally:
        thread.join(7)
    assert pid_file.exists() and not thread.is_alive()
    assert {child.pid for child in multiprocessing.active_children()} <= before


async def test_async_fetch_cancellation_reclaims_actual_pdf_process(monkeypatch, tmp_path):
    from app.core import knowledge_authority_pdf as parser
    before = {child.pid for child in multiprocessing.active_children()}
    pid_file = tmp_path / 'async-cancel.pid'
    monkeypatch.setenv('KNOWLEDGE_PDF_TEST_PID', str(pid_file))
    monkeypatch.setattr(parser, '_worker', stalled_worker)
    serve(monkeypatch, pdf_bytes(['Calculus.']))
    task = asyncio.create_task(core.fetch_authority_page('https://math.mit.edu/lecture.pdf'))
    try:
        until = time.monotonic() + 8
        while not pid_file.exists() and time.monotonic() < until:
            await asyncio.sleep(.02)
        assert pid_file.exists(), 'Cancellation must exercise the real started PDF process.'
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    until = time.monotonic() + 3
    while {child.pid for child in multiprocessing.active_children()} - before and time.monotonic() < until:
        await asyncio.sleep(.03)
    assert {child.pid for child in multiprocessing.active_children()} <= before


def malformed_worker(sender, body, max_pages, max_chars):
    try:
        sender.send_bytes(b'x' * (1024 * 1024 + 1) if body.endswith(b'OVERSIZED') else b'not-json')
    except (BrokenPipeError, OSError):
        pass  # The parent deliberately rejects and closes this invalid test channel.
    finally:
        sender.close()


@pytest.mark.parametrize('body', [b'%PDF-1.7\ninvalid', b'%PDF-1.7\nOVERSIZED'])
def test_malformed_or_oversized_process_result_is_rejected_and_reclaimed(monkeypatch, body):
    from app.core import knowledge_authority_pdf as parser
    before = {child.pid for child in multiprocessing.active_children()}
    monkeypatch.setattr(parser, '_worker', malformed_worker)
    with pytest.raises(ValueError, match='invalid result'):
        parser.parse_pdf(body, timeout=8)
    assert {child.pid for child in multiprocessing.active_children()} <= before


def test_scan_only_pdf_cannot_use_image_metadata_as_definition(monkeypatch):
    with fitz.open() as source:
        page = source.new_page()
        page.insert_text((40, 50), 'Calculus. The derivative is the instantaneous rate of change.')
        image = page.get_pixmap().tobytes('png')
    with fitz.open() as scan:
        page = scan.new_page()
        page.insert_image(page.rect, stream=image)
        body = scan.tobytes(deflate=True)
    serve(monkeypatch, body)
    with pytest.raises(ValueError, match='readable'):
        core._fetch_authority_page('https://math.mit.edu/lecture.pdf')
