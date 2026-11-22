"""Small bounded external retrieval, with page evidence rather than search snippets.

No model calls and no upload text enters search queries, validation or citations.
An inconclusive result is always exposed as searching, never as a made-up source.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import threading
import time
import unicodedata
from datetime import timedelta
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.database import async_session as SessionLocal
from app.models.knowledge_authority import AuthoritySourceSearch
from app.models.knowledge_network import NetworkConcept, now
from app.core.parsing.docreader.ssrf_proxy import checked_address
from app.core.parsing.docreader.web_parser import _PinnedHTTPConnection, _PinnedHTTPSConnection
from app.core.tools.web_search import WebSearchTool

logger = logging.getLogger('knowall.knowledge_authority')
MAX_ATTEMPTS = 3
COOLDOWN = timedelta(minutes=30)
MAX_PENDING = 24
MAX_SOURCES = 3
MAX_CANDIDATES = 10
MAX_PAGE_BYTES = 1024 * 1024
PAGE_SECONDS = 18
SEARCH_SECONDS = 50
_tasks: dict[str, asyncio.Task] = {}
_semaphore = asyncio.Semaphore(2)

_INSTITUTIONS = {
    **dict.fromkeys(('mit.edu', 'harvard.edu', 'stanford.edu', 'berkeley.edu', 'yale.edu',
                     'princeton.edu', 'caltech.edu', 'cornell.edu', 'cmu.edu', 'uchicago.edu',
                     'ox.ac.uk', 'cam.ac.uk', 'ucl.ac.uk', 'imperial.ac.uk',
                     'tsinghua.edu.cn', 'pku.edu.cn', 'fudan.edu.cn', 'sjtu.edu.cn',
                     'zju.edu.cn', 'ustc.edu.cn', 'nju.edu.cn', 'ruc.edu.cn', 'whu.edu.cn',
                     'bnu.edu.cn', 'nankai.edu.cn', 'xmu.edu.cn'), '高校'),
    **dict.fromkeys(('cas.cn', 'amss.ac.cn', 'ict.ac.cn', 'iscas.ac.cn'), '科研机构'),
    **dict.fromkeys(('gov.cn', 'moe.gov.cn', 'most.gov.cn', 'gov.uk', 'nih.gov',
                     'cdc.gov', 'nasa.gov', 'nist.gov', 'energy.gov'), '政府机构'),
}
_PUBLISHERS = {
    'nature.com': 'Nature 学术出版', 'springer.com': 'Springer 学术出版',
    'springerlink.com': 'Springer 学术出版', 'cambridge.org': 'Cambridge University Press',
    'academic.oup.com': 'Oxford University Press', 'ieee.org': 'IEEE',
    'acm.org': 'ACM', 'nih.gov': '美国国立卫生研究院', 'arxiv.org': 'arXiv 学术预印本',
    'openstax.org': 'OpenStax 高校教材',
}


def authority_for_url(url: str) -> str | None:
    """Only exact domains / dot-delimited subdomains, standard HTTP ports."""
    try:
        p = urlsplit(url)
        host = (p.hostname or '').lower()
        if p.scheme not in {'http', 'https'} or p.username is not None or p.password is not None:
            return None
        if p.port not in {None, 80, 443} or not host or host.endswith('.') or '\\' in url:
            return None
        for domain, label in _PUBLISHERS.items():
            if host == domain or host.endswith('.' + domain):
                return label
        for domain, label in _INSTITUTIONS.items():
            if host == domain or host.endswith('.' + domain):
                return label
    except (TypeError, ValueError):
        return None
    return None


class _PageText(HTMLParser):
    IGNORE = {'script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript', 'form', 'template'}
    VOID = {'br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'track', 'col'}
    BLOCK = {'p', 'div', 'section', 'article', 'li', 'h1', 'h2', 'h3', 'h4', 'table', 'tr', 'br'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title = [], []
        self.ignored_depth = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if self.ignored_depth:
            if tag not in self.VOID:
                self.ignored_depth += 1
            return
        if tag in self.IGNORE:
            self.ignored_depth = 1
        elif tag == 'title':
            self.in_title = True
        elif tag in self.BLOCK:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.ignored_depth:
            if tag not in self.VOID:
                self.ignored_depth -= 1
        elif tag == 'title':
            self.in_title = False
        elif tag in self.BLOCK:
            self.parts.append('\n')

    def handle_data(self, data):
        if self.ignored_depth:
            return
        # HTML source wrapping is inline whitespace, not a paragraph boundary.
        (self.title if self.in_title else self.parts).append(re.sub(r'\s+', ' ', data))


def parse_page(html: str) -> dict:
    parser = _PageText()
    parser.feed(html)
    text = '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())
    return {'title': ' '.join(''.join(parser.title).split())[:250], 'text': text}


def _request_page(url, address, deadline):
    """Reuse DNS-pinned TLS connections; cap response bytes and elapsed reading."""
    p = urlsplit(url)
    cls = _PinnedHTTPSConnection if p.scheme == 'https' else _PinnedHTTPConnection
    connection = cls(p.hostname, address[1], address[0])
    connection.timeout = min(6, max(0.1, deadline - time.monotonic()))
    path = p.path or '/'
    if p.query:
        path += '?' + p.query
    try:
        connection.request('GET', path, headers={'Accept': 'text/html,application/xhtml+xml,text/plain,application/pdf', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status in {301, 302, 303, 307, 308}:
            return response.status, headers, b''
        length = headers.get('content-length')
        if length and int(length) > MAX_PAGE_BYTES:
            raise ValueError('Authority page exceeds byte limit')
        chunks, size = [], 0
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('Authority page timed out')
            chunk = response.read(min(16384, MAX_PAGE_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_PAGE_BYTES:
                raise ValueError('Authority page exceeds byte limit')
            chunks.append(chunk)
        return response.status, headers, b''.join(chunks)
    finally:
        connection.close()


def _fetch_authority_page(url: str, cancelled=None) -> dict:
    deadline = time.monotonic() + PAGE_SECONDS
    current = url
    for hop in range(4):
        if cancelled is not None and cancelled.is_set():
            raise ValueError('Authority page cancelled')
        if time.monotonic() >= deadline:
            raise TimeoutError('Authority page timed out')
        if not authority_for_url(current):
            raise ValueError('Not an authority URL')
        address = checked_address(current)
        # SSRF administrator overrides must not allow private citation fetches.
        if not ipaddress.ip_address(address[0]).is_global:
            raise ValueError('Authority URL must resolve to a public address')
        status, headers, body = _request_page(current, address, deadline)
        if status in {301, 302, 303, 307, 308}:
            if hop == 3 or not headers.get('location'):
                raise ValueError('Authority page redirect limit')
            current = urljoin(current, headers['location'])
            continue
        if not 200 <= status < 300:
            raise ValueError('Authority page unavailable')
        media = headers.get('content-type', '').split(';', 1)[0].lower().strip()
        if media not in {'text/html', 'application/xhtml+xml', 'text/plain', 'application/pdf'}:
            raise ValueError('Unsupported authority content type')
        if headers.get('content-encoding', 'identity').lower() != 'identity':
            raise ValueError('Compressed authority response rejected')
        if media == 'application/pdf':
            from app.core.knowledge_authority_pdf import parse_pdf
            result = parse_pdf(body, timeout=deadline - time.monotonic(), cancelled=cancelled)
            return {**result, 'url': current}
        message = Message()
        message['content-type'] = headers['content-type']
        try:
            text = body.decode(message.get_content_charset() or 'utf-8', errors='replace')
        except LookupError:
            text = body.decode('utf-8', errors='replace')
        result = parse_page(text) if media != 'text/plain' else {'title': '', 'text': text}
        return {**result, 'url': current}
    raise ValueError('Authority page redirect limit')


async def fetch_authority_page(url: str) -> dict:
    cancelled = threading.Event()
    try:
        return await asyncio.wait_for(asyncio.to_thread(_fetch_authority_page, url, cancelled), PAGE_SECONDS + 7)
    finally:
        # Cancellation of to_thread alone does not stop its native PDF process.
        cancelled.set()


def _contains(text, term):
    term = term.strip().casefold()
    if not term:
        return False
    if re.search(r'[\u3400-\u9fff]', term):
        return term in text.casefold()
    return bool(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text.casefold()))


def verified_excerpt(page: dict, concept: NetworkConcept) -> str | None:
    """Require a contextual definition sentence, keeping the exact page wording.

    This intentionally misses terse/formula-only pages instead of treating an
    authoritative hostname or a passing concept mention as supporting evidence.
    """
    text = page.get('text', '')
    context = [s for s in (concept.subject, concept.topic) if s.strip() and s.strip() != concept.name.strip()]
    if not text or not context or not any(_contains(text, term) for term in context):
        return None
    name = re.escape(concept.name.strip())
    for match in re.finditer(r'[^。！？.!?\n]+[。！？.!?]?', text):
        sentence = match.group().strip()
        if not 16 <= len(sentence) <= 700 or not _contains(sentence, concept.name):
            continue
        if any(not character.isprintable() and not character.isspace() for character in sentence):
            continue  # Broken PDF glyph mappings cannot prove the quoted wording.
        # The predicate must describe the concept itself, not a student/course
        # mentioned later in a news sentence that happens to contain its name.
        definition_name = name if re.search(r'[\u3400-\u9fff]', concept.name) else r'(?<!\w)' + name + r'(?:\s+of\s+(?:a|the)\s+function)?'
        predicate = definition_name + r'\s*(?:[（(][^）)]{0,80}[）)]\s*)?(?:(?:的)?定义(?:为|是)|是|指|表示|(?:又称[^，,。]{1,30}[，,])?是|\s+(?:is defined as|is|are|refers to|denotes|means)\b)'
        definition = re.search(predicate, sentence, re.I)
        if definition:
            tail = sentence[definition.end():]
            # A copula followed only by an article is an incomplete definition.
            if re.search(r'[\u3400-\u9fff]', concept.name):
                substantive = len(re.findall(r'[\u3400-\u9fff]', tail)) >= 4
            else:
                prefix = sentence[:definition.start()]
                if not re.search(r'(?:^|[,;:]\s*)(?:(?:the|an?)\s*)?$', prefix, re.I):
                    continue  # The concept must be the clause's subject, not an example's possessive/object.
                normalized_tail = unicodedata.normalize('NFKC', tail)
                if re.match(r'\s*(?:not\b|never\b|written\b|denoted\b)', normalized_tail, re.I):
                    continue
                if re.match(r'\s*(?:the|an?)\s+[a-z]\s+of\b', normalized_tail, re.I):
                    continue  # An unfilled textbook placeholder such as "the a of ...".
                words = [word for word in re.findall(r'[a-z]+', tail.casefold())
                         if word not in {'a', 'an', 'the', 'of', 'as', 'defined'}]
                substantive = len(words) >= 2 and sum(map(len, words)) >= 8
            if not substantive:
                continue
            nearby = text[max(0, match.start() - 200):match.end() + 200]
            # A bounded PDF page is itself a context boundary. HTML retains
            # proximity checking because it can contain many unrelated sections.
            if 'number' not in page and not any(_contains(nearby, term) for term in context):
                continue
            return sentence
    # Formal calculus definitions often put the name in a numbered heading,
    # then state conditions and a formula before naming the finite limit.
    # Keep that whole short block; a passing/news mention cannot qualify.
    header = r'(?:^|\n)定义\s*\d+[.．、]?\s*[（(]' + name + r'[）)]\s*\n'
    for match in re.finditer(header, text):
        block = re.split(r'\n(?:注[.．]|例\s*\d|定义\s*\d|定理\s*\d)',
                         text[match.end():], maxsplit=1)[0].strip()
        if not 16 <= len(block) <= 700 or not re.search(r'若[\s\S]*存在有限', block):
            continue
        if any(not character.isprintable() and not character.isspace() for character in block):
            continue
        reverse = (r'称(?:这个|该)?极限(?:值)?为[^。！？\n]{0,80}' + name +
                   r'(?:[（(][^）)]{0,30}[）)])?(?=[，,。；;]|$)')
        if re.search(reverse, block):
            return block
    return None


def search_queries(concept: NetworkConcept) -> list[str]:
    # Only identity metadata is used. No private definitions, upload titles or quotes.
    identity = ' '.join(re.sub(r'["\r\n]', ' ', part).strip() for part in (concept.name, concept.subject, concept.topic))
    return [identity + ' 定义 (site:edu.cn OR site:ac.cn)',
            identity + ' definition (site:edu OR site:ac.uk)',
            identity + ' 定义 (site:gov.cn OR site:gov)',
            identity + ' definition (site:cambridge.org OR site:academic.oup.com OR site:springer.com OR site:nature.com)']


async def retrieve_sources(concept: NetworkConcept) -> list[dict]:
    deadline = time.monotonic() + SEARCH_SECONDS
    tool, candidates, seen = WebSearchTool(), [], set()
    query_limit = asyncio.Semaphore(2)
    async def search(query):
        try:
            async with query_limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                result = await asyncio.wait_for(tool.execute(query=query, max_results=5), timeout=min(10, remaining))
                return result.output if result.success and isinstance(result.output, list) else []
        except Exception:
            return []
    results = await asyncio.gather(*(search(q) for q in search_queries(concept)))
    for items in results:
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get('url', '')
            if not isinstance(url, str) or not authority_for_url(url):
                continue
            parts = urlsplit(url)
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
            if url not in seen and len(candidates) < MAX_CANDIDATES:
                seen.add(url)
                candidates.append(url)
    page_limit = asyncio.Semaphore(2)
    async def fetch(url):
        async with page_limit:
            try:
                return await fetch_authority_page(url)
            except Exception:
                return None
    pending = {asyncio.create_task(fetch(url)) for url in candidates}
    all_tasks = set(pending)
    sources, final_urls = [], set()
    try:
        while pending and len(sources) < MAX_SOURCES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                page = task.result()
                if not page:
                    continue
                final_url = page.get('url', '')
                authority = authority_for_url(final_url)
                if not authority or final_url in final_urls:
                    continue
                # Each PDF page proves its own context; no cross-page borrowing.
                for part in page.get('pages', [page]):
                    excerpt = verified_excerpt(part, concept)
                    if not excerpt:
                        continue
                    title = page.get('title') or concept.name + ' — ' + authority
                    source_url = final_url
                    if 'number' in part:
                        components = urlsplit(final_url)
                        source_url = urlunsplit((*components[:4], f"page={part['number']}"))
                        title += f"（PDF 第{part['number']}页）"
                    if len(sources) < MAX_SOURCES:
                        sources.append({'title': title, 'url': source_url, 'authority': authority, 'excerpt': excerpt})
                        final_urls.add(final_url)
                    break
    finally:
        for task in all_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)
    return sources


async def source_search_response(db, concept_id: str) -> dict:
    row = await db.get(AuthoritySourceSearch, concept_id, populate_existing=True)
    sources = list(row.sources or []) if row else []
    return {'status': 'found' if sources else 'searching', 'sources': sources}


async def _search_concept(concept_id: str, user_id: str, manual=False):
    from app.api.knowledge_network import concept_context
    async with _semaphore:
        async with SessionLocal() as db:
            try:
                concept, _, _ = await concept_context(db, concept_id, user_id)
                row = await db.get(AuthoritySourceSearch, concept_id)
                if not row:
                    try:
                        async with db.begin_nested():
                            db.add(AuthoritySourceSearch(concept_id=concept_id))
                            await db.flush()
                    except IntegrityError:
                        pass
                    row = await db.get(AuthoritySourceSearch, concept_id)
                current = now()
                if not row or row.sources or (not manual and row.attempts >= MAX_ATTEMPTS) or (row.last_attempt_at and current - row.last_attempt_at < COOLDOWN):
                    return
                claimed = await db.execute(update(AuthoritySourceSearch).where(
                    AuthoritySourceSearch.concept_id == concept_id,
                    AuthoritySourceSearch.attempts == row.attempts).values(
                    attempts=row.attempts + 1, last_attempt_at=current, updated_at=current))
                await db.commit()
                if not claimed.rowcount:
                    return
                # Entire search is bounded even when every candidate fails.
                sources = await retrieve_sources(concept)
                await db.rollback()
                await concept_context(db, concept_id, user_id)  # recheck after network I/O
                await db.execute(update(AuthoritySourceSearch).where(AuthoritySourceSearch.concept_id == concept_id)
                                 .values(sources=sources, updated_at=now()))
                await db.commit()
            except HTTPException:
                await db.rollback()
            except Exception as exc:
                await db.rollback()
                logger.info('Authority retrieval unavailable for %s (%s)', concept_id, type(exc).__name__)


async def schedule_source_searches(ids, user_id: str, manual=False):
    if not user_id:
        return
    for cid in dict.fromkeys(ids):
        if len(_tasks) >= MAX_PENDING:
            break
        if cid not in _tasks:
            task = asyncio.create_task(_search_concept(cid, user_id, manual))
            _tasks[cid] = task
            task.add_done_callback(lambda finished, key=cid: _tasks.pop(key, None))


async def close_source_searches():
    """Drain work at shutdown; cancel pending requests after a bounded grace period."""
    tasks = list(_tasks.values())
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=30)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
