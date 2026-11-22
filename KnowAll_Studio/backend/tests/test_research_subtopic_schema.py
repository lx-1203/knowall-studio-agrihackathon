"""Research model response contract; all provider/tool calls explicitly simulated."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.core.agents.research_agent import ResearchAgent, ResearchStatus, ResearchTask
from app.core.api_scheduler import api_client
from app.core.api_scheduler.client import _current_user_id


BAD = [None, 'null', '{}', '[]', '[null]', '[1]', '[true]', '["bad"]', '[{}]',
       '[{"title":null}]', '[{"title":1}]', '[{"title":[]}]', '[{"title":{}}]',
       '[{"title":" "}]', '[{"title":"ok","description":null}]',
       '[{"title":"ok","description":3}]', '[{"title":"ok","description":[]}]',
       '[{"title":"ok","description":{}}]', '[{"title":"ok"},null]']


@pytest.mark.parametrize('raw', BAD)
async def test_bad_selected_subtopics_atomically_fall_back_to_original_topic(monkeypatch, raw):
    monkeypatch.setattr(api_client, 'chat', AsyncMock(return_value=raw))
    items = await ResearchAgent()._decompose('original topic', 2)
    assert [(i.title, i.description) for i in items] == [('original topic', 'original topic')]
    assert items[0].id and items[0].status == 'pending'


@pytest.mark.parametrize('raw,expected', [
    ('[{"title":" T ","description":" D "}]', [('T','D')]),
    ('[{"title":"T"}]', [('T','')]),
    ('```json\n[{"title":"T","description":""}]\n```', [('T','')]),
    ('[{"title":"A"},{"title":"B"},null]', [('A',''),('B','')]),
])
async def test_legal_subtopics_keep_order_fields_and_selected_limit(monkeypatch, raw, expected):
    monkeypatch.setattr(api_client, 'chat', AsyncMock(return_value=raw))
    items = await ResearchAgent()._decompose('original topic', 2)
    assert [(i.title,i.description) for i in items] == expected
    assert len({i.id for i in items}) == len(items)


@pytest.mark.parametrize('raw,expected', [('', 'original'),('  ', 'original'),('""','original'),
    ("''",'original'),('"  "','original'),("'  '",'original'),(None,'original'),(3,'original'),('" refined "','refined')])
async def test_refined_topic_is_nonempty_text_or_original(monkeypatch, raw, expected):
    monkeypatch.setattr(api_client, 'chat', AsyncMock(return_value=raw))
    assert await ResearchAgent()._rephrase('original') == expected


@pytest.mark.parametrize('raw', ['" '+"''"+' "', "' "+'""'+" '", '"\t'+"'"+'\n"'])
async def test_nested_quote_only_topic_is_still_empty(monkeypatch,raw):
    monkeypatch.setattr(api_client,'chat',AsyncMock(return_value=raw))
    assert await ResearchAgent()._rephrase('original')=='original'


@pytest.mark.parametrize('stage', ['rephrase', 'decompose'])
async def test_provider_exception_is_not_schema_fallback(monkeypatch, stage):
    monkeypatch.setattr(api_client, 'chat', AsyncMock(side_effect=RuntimeError('explicit provider failure')))
    agent=ResearchAgent()
    with pytest.raises(RuntimeError, match='explicit provider failure'):
        await (agent._rephrase('original') if stage=='rephrase' else agent._decompose('original',2))


@pytest.mark.parametrize('raw', ['[null]', '[{"title":null}]','[{"title":"ok","description":[]}]'])
async def test_pipeline_schema_fallback_finishes_and_resets_identity(monkeypatch, raw):
    import app.core.agents.research_agent as module
    from types import SimpleNamespace
    observed=[]
    queries=[]
    async def model(prompt,**kwargs):
        observed.append(_current_user_id.get())
        if '细化为' in prompt:return '"  "'
        if '分解为' in prompt:return raw
        return '# explicitly simulated report'
    async def tool(name,**kwargs):
        queries.append(kwargs['query'])
        return SimpleNamespace(success=True,output={'results':[]} if name=='knowledge_search' else [])
    monkeypatch.setattr(api_client,'chat',model)
    monkeypatch.setattr(module,'execute_tool',tool)
    agent=ResearchAgent()
    task=ResearchTask('fixture','original topic',user_id='alice')
    token=_current_user_id.set('ambient')
    try:
        await agent._run_pipeline(task,2)
        assert task.status==ResearchStatus.COMPLETED and task.progress==1
        assert task.refined_topic=='original topic'
        assert task.sub_topics[0].title=='original topic'
        assert task.sub_topics[0].description=='original topic'
        assert queries==['original topic original topic','original topic','original topic']
        assert observed==['alice']*3 and _current_user_id.get()=='ambient'
    finally:
        _current_user_id.reset(token)
        await agent.close()
