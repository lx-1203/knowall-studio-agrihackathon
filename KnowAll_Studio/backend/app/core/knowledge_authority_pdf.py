"""Bounded digital PDF text in an isolated native-library process.

No database, uploads, models, OCR, URL fetching, or embedded action execution.
"""
import json
import multiprocessing
import time

MAX_PDF_BYTES = 1024 * 1024
MAX_PDF_PAGES = 64
MAX_PDF_CHARS = 200000
MAX_RESULT_BYTES = 1024 * 1024


def _extract(body, max_pages, max_chars):
    import fitz
    try:
        document = fitz.open(stream=body, filetype='pdf')
    except Exception as exc:
        raise ValueError('Authority PDF is invalid') from exc
    with document:
        if not document.is_pdf:
            raise ValueError('Authority PDF is invalid')
        if document.needs_pass:
            raise ValueError('Authority PDF requires a password')
        if not 1 <= document.page_count <= max_pages:
            raise ValueError('Authority PDF exceeds page limit')
        title = ' '.join((document.metadata.get('title') or '').split())[:250]
        pages, characters = [], 0
        for index, page in enumerate(document):
            # Preserve paragraph/page boundaries; never include image metadata.
            blocks = [' '.join(block[4].split()) for block in page.get_text('blocks', sort=True)
                      if block[6] == 0 and block[4].strip()]
            text = '\n'.join(blocks)
            characters += len(text)
            if characters > max_chars:
                raise ValueError('Authority PDF exceeds text limit')
            pages.append({'number': index + 1, 'text': text})
        if not any(page['text'] for page in pages):
            raise ValueError('Authority PDF has no readable digital text')
        return {'title': title, 'pages': pages}


def _worker(sender, body, max_pages, max_chars):
    try:
        try:
            result = {'result': _extract(body, max_pages, max_chars)}
        except Exception as exc:
            result = {'error': str(exc) if isinstance(exc, ValueError) else 'Authority PDF is invalid'}
        payload = json.dumps(result, ensure_ascii=False).encode('utf-8')
        if len(payload) > MAX_RESULT_BYTES:
            payload = b'{"error":"Authority PDF exceeds result limit"}'
        sender.send_bytes(payload)
    finally:
        sender.close()


def parse_pdf(body, *, timeout, cancelled=None, max_bytes=MAX_PDF_BYTES,
              max_pages=MAX_PDF_PAGES, max_chars=MAX_PDF_CHARS):
    """Spawn per document and reclaim it on success, error, cancellation or timeout."""
    if len(body) > max_bytes:
        raise ValueError('Authority PDF exceeds byte limit')
    if not body.startswith(b'%PDF-'):
        raise ValueError('Authority PDF signature is invalid')
    if timeout <= 0:
        raise TimeoutError('Authority PDF timed out')
    if cancelled is not None and cancelled.is_set():
        raise ValueError('Authority PDF cancelled')
    deadline = time.monotonic() + timeout
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, body, max_pages, max_chars), daemon=True)
    try:
        process.start()
        sender.close()
        while True:
            if cancelled is not None and cancelled.is_set():
                raise ValueError('Authority PDF cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Authority PDF timed out')
            if receiver.poll(min(.1, remaining)):
                try:
                    result = json.loads(receiver.recv_bytes(MAX_RESULT_BYTES))
                except (EOFError, OSError, ValueError) as exc:
                    raise ValueError('Authority PDF worker returned an invalid result') from exc
                if 'error' in result:
                    raise ValueError(result['error'])
                return result['result']
    finally:
        receiver.close()
        sender.close()
        if process.pid is not None:
            process.join(.2)
            if process.is_alive():
                process.terminate()
                process.join(1)
            if process.is_alive():
                process.kill()
                process.join(1)
            process.close()
