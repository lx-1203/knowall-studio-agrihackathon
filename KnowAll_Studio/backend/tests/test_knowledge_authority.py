"""Authority retrieval boundaries using real SQLite and deterministic external I/O."""
import importlib.util
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_authority_module_exists():
    assert importlib.util.find_spec('app.core.knowledge_authority') is not None, 'Authority retrieval is missing'


@pytest.fixture
async def authority(tmp_path, monkeypatch):
    from app.api import knowledge_authority as api
    from app.core import knowledge_authority as core
    from app.core.auth import get_current_user
    from app.database import Base, get_db
    from app.models import Document, DocumentChunk
    from app.models.knowledge_network import NetworkConcept, ConceptSource
    from app.models.knowledge_authority import AuthoritySourceSearch
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'authority.sqlite').as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(core, 'SessionLocal', factory)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add_all([
            NetworkConcept(id='public', name='导数', topic='微分', subject='数学', public_content={'definition': '公开定义'}),
            NetworkConcept(id='private', name='私有概念', topic='私有主题', subject='数学'),
            Document(id='upload', filename='用户上传文件.pdf', sha256='x' * 64, file_type='pdf', status='ready', metadata_={'knowledge_owner_id': 'alice'}),
        ])
        await db.flush()
        db.add(DocumentChunk(id='chunk', doc_id='upload', chunk_index=0, text_content='私有解释与引文', page_range='1'))
        await db.flush()
        db.add(ConceptSource(concept_id='private', user_id='alice', document_id='upload', chunk_id='chunk', quote='私有解释与引文', quote_hash='x' * 64,
                             content={'definition': '绝不能发送到搜索服务的私有解释'}))
        await db.commit()
    queries, fetched = [], []
    search_results = []
    pages = {}
    async def search(self, **kwargs):
        queries.append(kwargs['query'])
        return SimpleNamespace(success=True, output=list(search_results))
    async def fetch(url):
        fetched.append(url)
        value = pages.get(url)
        if isinstance(value, Exception):
            raise value
        return value or {'url': url, 'title': '空页面', 'text': ''}
    monkeypatch.setattr(core.WebSearchTool, 'execute', search)
    monkeypatch.setattr(core, 'fetch_authority_page', fetch)
    app = FastAPI()
    app.include_router(api.router)
    async def database():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    account = {'id': 'bob'}
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=account['id'])
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield SimpleNamespace(core=core, model=AuthoritySourceSearch, factory=factory, client=client,
                              queries=queries, fetched=fetched, results=search_results, pages=pages, account=account)
    await core.close_source_searches()
    await engine.dispose()


async def response(a, cid='public'):
    async with a.factory() as db:
        return await a.core.source_search_response(db, cid)


async def run_search(a, cid='public', user='bob'):
    await a.core.schedule_source_searches([cid], user)
    await a.core.close_source_searches()


async def test_no_results_and_search_failure_remain_searching(authority, monkeypatch):
    a = authority
    assert await response(a) == {'status': 'searching', 'sources': []}
    await run_search(a)
    assert await response(a) == {'status': 'searching', 'sources': []}
    async with a.factory() as db:
        row = await db.get(a.model, 'public')
        row.last_attempt_at -= timedelta(hours=1)
        await db.commit()
    async def failing(*args, **kwargs):
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(a.core.WebSearchTool, 'execute', failing)
    await run_search(a)
    assert await response(a) == {'status': 'searching', 'sources': []}


async def test_only_verified_page_excerpt_is_persisted(authority):
    a = authority
    good = 'https://math.mit.edu/derivative'
    bad = 'https://news.mit.edu/student-story'
    a.results.extend([
        {'title': '伪造搜索标题', 'url': good, 'snippet': '不能作为来源的摘要'},
        {'title': '标题有导数', 'url': bad, 'snippet': '微分中导数是瞬时变化率。'},
        {'title': '域名伪装', 'url': 'https://mit.edu.evil.example/derivative', 'snippet': '导数是瞬时变化率'},
        {'title': '上传', 'url': '/uploads/用户上传文件.pdf'},
    ])
    text = '微分是数学中的基础主题。导数是函数在一点的瞬时变化率，由差商的极限定义。'
    a.pages[good] = {'url': good, 'title': '大学微分讲义', 'text': text}
    a.pages[bad] = {'url': bad, 'title': '学生新闻', 'text': '微分课程学生参加导数竞赛，欢迎报名。'}
    await run_search(a)
    result = await response(a)
    assert result['status'] == 'found' and len(result['sources']) == 1
    source = result['sources'][0]
    assert source['url'] == good and source['title'] == '大学微分讲义'
    assert source['excerpt'] in text and '导数' in source['excerpt']
    assert source['authority']
    assert '不能作为来源' not in str(result)
    assert len(a.queries) >= 3 and len(a.fetched) == 2


async def test_snippet_domain_and_concept_mention_do_not_prove_definition(authority):
    a = authority
    for path, text in [('unrelated', '微分课程要求。导数课程报名表。'),
                       ('news', '微分课程公告。导数比赛的获奖者是数学专业优秀学生。'),
                       ('other-subject', '文学中导数是一个虚构人物称号。'),
                       ('failed', RuntimeError('page unavailable'))]:
        url = 'https://math.mit.edu/' + path
        a.results.append({'url': url, 'title': '导数定义', 'snippet': '导数是变化率'})
        a.pages[url] = text if isinstance(text, Exception) else {'url': url, 'title': '页面', 'text': text}
    await run_search(a)
    assert await response(a) == {'status': 'searching', 'sources': []}


async def test_private_concept_search_is_owned_and_never_uses_upload_words(authority):
    a = authority
    path = '/api/v1/knowledge-network/concepts/private/sources/search'
    assert (await a.client.post(path)).status_code == 404
    await run_search(a, 'private')
    assert not a.queries
    a.account['id'] = 'alice'
    result = await a.client.post(path)
    assert result.status_code == 200 and result.json() == {'status': 'searching', 'sources': []}
    await a.core.close_source_searches()
    assert a.queries and all('私有解释' not in q and '用户上传' not in q and '引文' not in q for q in a.queries)
    a.account['id'] = 'bob'
    assert (await a.client.post(path)).status_code == 404


async def test_retry_cooldown_and_attempt_limit_bound_work(authority):
    a = authority
    await run_search(a)
    first = len(a.queries)
    await run_search(a)
    assert len(a.queries) == first
    async with a.factory() as db:
        row = await db.get(a.model, 'public')
        row.attempts = a.core.MAX_ATTEMPTS
        row.last_attempt_at -= timedelta(days=1)
        await db.commit()
    await run_search(a)
    assert len(a.queries) == first


async def test_manual_search_can_retry_after_automatic_limit_and_cooldown(authority):
    a = authority
    async with a.factory() as db:
        db.add(a.model(concept_id='public', attempts=a.core.MAX_ATTEMPTS, last_attempt_at=a.core.now()))
        await db.commit()
    path = '/api/v1/knowledge-network/concepts/public/sources/search'
    assert (await a.client.post(path)).status_code == 200
    await a.core.close_source_searches()
    assert a.queries == []
    async with a.factory() as db:
        row = await db.get(a.model, 'public')
        row.last_attempt_at -= timedelta(hours=1)
        await db.commit()
    assert (await a.client.post(path)).status_code == 200
    await a.core.close_source_searches()
    assert a.queries
    assert await response(a) == {'status': 'searching', 'sources': []}


@pytest.mark.parametrize('url', ['file:///tmp/doc', 'https://mit.edu.evil.org/x', 'https://evilmit.edu/x',
                                 'http://localhost/x', 'https://math.mit.edu:6379/x', 'https://user:pass@math.mit.edu/x',
                                 'https://math.mit.edu./x', 'https://127.0.0.1/x'])
def test_authority_url_rejects_lookalikes_credentials_and_unsafe_addresses(url):
    from app.core.knowledge_authority import authority_for_url
    assert authority_for_url(url) is None


def test_fetch_revalidates_redirects_and_pins_public_dns(monkeypatch):
    from app.core import knowledge_authority as core
    checked, requested = [], []
    def address(url):
        checked.append(url)
        return ('93.184.216.34', 443)
    def request(url, addr, deadline):
        requested.append((url, addr))
        return 302, {'location': 'https://math.mit.edu/target'}, b''
    monkeypatch.setattr(core, 'checked_address', address)
    monkeypatch.setattr(core, '_request_page', request)
    with pytest.raises(ValueError, match='redirect'):
        core._fetch_authority_page('https://math.mit.edu/start')
    assert len(checked) == len(requested) and len(checked) <= 4
    monkeypatch.setattr(core, '_request_page', lambda *args: (302, {'location': 'http://127.0.0.1/secret'}, b''))
    with pytest.raises(ValueError):
        core._fetch_authority_page('https://math.mit.edu/start')
    monkeypatch.setattr(core, 'checked_address', lambda url: ('127.0.0.1', 443))
    with pytest.raises(ValueError, match='public'):
        core._fetch_authority_page('https://math.mit.edu/start')


def test_html_parser_excludes_scripts_and_links_from_evidence():
    from app.core.knowledge_authority import parse_page
    page = parse_page('<title>讲义</title><nav>导数是导航</nav><script>导数是假的</script><p>数学微分中，导数是瞬时变化率。</p>')
    assert page['title'] == '讲义'
    assert page['text'] == '数学微分中，导数是瞬时变化率。'


def test_page_byte_limit_rejects_oversized_content_before_parsing(monkeypatch):
    from app.core import knowledge_authority as core
    class Response:
        status = 200
        def getheaders(self):
            return [('content-type', 'text/html'), ('content-length', str(core.MAX_PAGE_BYTES + 1))]
    class Connection:
        def __init__(self, *args):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(core, '_PinnedHTTPSConnection', Connection)
    with pytest.raises(ValueError, match='byte limit'):
        core._request_page('https://math.mit.edu/page', ('93.184.216.34', 443), core.time.monotonic() + 10)


async def test_search_deadline_preserves_already_verified_sources(authority, monkeypatch):
    a = authority
    fast, slow = 'https://math.mit.edu/fast', 'https://math.mit.edu/slow'
    a.results.extend([{'url': fast}, {'url': slow}])
    async def fetch(url):
        if url == slow:
            await asyncio.sleep(0.15)
        return {'url': url, 'title': '讲义', 'text': '微分属于数学。导数是函数在一点的瞬时变化率，由差商极限定义。'}
    monkeypatch.setattr(a.core, 'fetch_authority_page', fetch)
    monkeypatch.setattr(a.core, 'SEARCH_SECONDS', 0.03, raising=False)
    result = await a.core.retrieve_sources(SimpleNamespace(name='导数', topic='微分', subject='数学'))
    assert [s['url'] for s in result] == [fast]


def test_html_source_linebreaks_preserve_complete_definition():
    from app.core.knowledge_authority import parse_page, verified_excerpt
    page = parse_page('<p>The essence of calculus is the derivative. The\n'
                      '<b>derivative</b> is the\ninstantaneous rate of change of a function '
                      'with respect to one of its\nvariables.</p>')
    concept = SimpleNamespace(name='derivative', subject='mathematics', topic='calculus')
    assert verified_excerpt(page, concept) == ('The derivative is the instantaneous rate of '
                                               'change of a function with respect to one of its variables.')


@pytest.mark.parametrize('tail', ['the', '', 'a', 'defined as'])
def test_incomplete_definition_is_not_page_evidence(tail):
    from app.core.knowledge_authority import verified_excerpt
    concept = SimpleNamespace(name='derivative', subject='mathematics', topic='calculus')
    page = {'text': f'We study calculus. The derivative is {tail}.'}
    assert verified_excerpt(page, concept) is None


def test_formal_numbered_definition_preserves_conditions_and_equation():
    from app.core.knowledge_authority import parse_page, verified_excerpt
    page = parse_page('<h1>一元函数微分学</h1><h2>定义 1. (导数)</h2>'
                      '<p>设函数在一点的邻域内有定义，若差商极限 '
                      r'\[\lim_{h\to0}(f(x+h)-f(x))/h\] '
                      '存在有限，则称这个极限值为函数在该点的导数。</p><h2>例 1</h2>')
    concept = SimpleNamespace(name='导数', subject='数学', topic='微分')
    excerpt = verified_excerpt(page, concept)
    assert excerpt and '存在有限' in excerpt and r'\lim' in excerpt
    assert excerpt in page['text'] and '例 1' not in excerpt


@pytest.mark.parametrize('text', [
    '数学微分新闻。称这次竞赛为导数。',
    '数学微分新闻。定义 1. (导数)\n主办方称这次竞赛为导数。',
    '数学微分。定义 1. (右导数)\n若差商极限存在有限，则称这个极限值为函数的右导数。',
])
def test_reverse_definition_requires_exact_header_and_mathematical_condition(text):
    from app.core.knowledge_authority import verified_excerpt
    assert verified_excerpt({'text': text}, SimpleNamespace(name='导数', subject='数学', topic='微分')) is None


def test_reliable_html_between_256k_and_1m_is_read_fully(monkeypatch):
    from app.core import knowledge_authority as core
    body = b' ' * (300 * 1024) + '<p>数学微分。导数是函数的瞬时变化率。</p>'.encode()
    class Response:
        status = 200
        def __init__(self):
            import io
            self.stream = io.BytesIO(body)
        def getheaders(self):
            return [('content-type', 'text/html; charset=utf-8'), ('content-length', str(len(body)))]
        def read(self, size):
            return self.stream.read(size)
    class Connection:
        def __init__(self, *args):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(core, '_PinnedHTTPSConnection', Connection)
    monkeypatch.setattr(core, 'checked_address', lambda url: ('93.184.216.34', 443))
    page = core._fetch_authority_page('https://staff.ustc.edu.cn/lecture')
    assert page['text'] == '数学微分。导数是函数的瞬时变化率。'


def test_function_qualifier_in_an_english_definition_is_verified():
    from app.core.knowledge_authority import verified_excerpt
    text = 'Calculus studies change. The derivative of a function is its instantaneous rate of change.'
    concept = SimpleNamespace(name='derivative', subject='mathematics', topic='calculus')
    assert verified_excerpt({'text': text}, concept) == 'The derivative of a function is its instantaneous rate of change.'


@pytest.mark.parametrize('sentence', [
    'In this example the derivative is not defined at the instant when t = 3.',
    'The derivative is not defined at this point in the example.',
    'The derivative is the a of df/dt as dt approaches b.',
    'Its derivative is 1 + 1 + 1 (also x terms).',
    'By linearity the derivative is 3 + 4 cos x.',
    'Asking for a derivative is more than asking for continuity.',
    'The derivative is the rate\x04of change of a function.',
    'The derivative is written v or e or f.',
])
def test_english_example_negation_placeholder_and_bad_glyph_are_not_definitions(sentence):
    from app.core.knowledge_authority import verified_excerpt
    concept = SimpleNamespace(name='derivative', subject='mathematics', topic='calculus')
    assert verified_excerpt({'text': 'Calculus context. ' + sentence, 'number': 5}, concept) is None


def test_formal_definition_with_a_broken_formula_glyph_is_not_evidence():
    from app.core.knowledge_authority import verified_excerpt
    text = ('数学微分。\n定义 1. (导数)\n若差商极限 lim\x04(f(x+h)-f(x))/h '
            '存在有限，则称这个极限值为函数在该点的导数。')
    concept = SimpleNamespace(name='导数', subject='数学', topic='微分')
    assert verified_excerpt({'text': text, 'number': 2}, concept) is None
