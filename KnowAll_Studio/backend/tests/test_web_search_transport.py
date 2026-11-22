"""Actual HTTP response contracts, no live traffic or provider credentials."""
import httpx
import pytest

from app.core.tools.web_search import WebSearchTool


@pytest.mark.parametrize('status,html', [
    (202, '<form id="challenge-form">verification required</form>'),
    (200, '<div class="anomaly-modal">verification required</div>'),
])
async def test_verification_page_is_provider_failure_not_successful_empty_search(monkeypatch, status, html):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=html))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    result = await WebSearchTool().execute(query='导数 数学 微分')
    assert result.success is False
    assert result.output is None
    assert result.metadata['reason'] == 'verification_required'
    assert result.error and 'verification' in result.error


async def test_genuine_empty_search_keeps_successful_empty_result(monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='<p>No results.</p>'))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    result = await WebSearchTool().execute(query='rare-public-concept')
    assert result.success is True and result.output == []


async def test_actual_result_page_still_returns_citation_candidates(monkeypatch):
    original = httpx.AsyncClient
    html = ('<a class="result__a" href="https://web.mit.edu/calculus">Calculus</a>'
            '<div class="result__snippet">Definition in a textbook</div>')
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    result = await WebSearchTool().execute(query='derivative definition')
    assert result.success is True and result.output[0]['url'] == 'https://web.mit.edu/calculus'
