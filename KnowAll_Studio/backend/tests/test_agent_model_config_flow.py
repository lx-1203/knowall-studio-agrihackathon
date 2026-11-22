"""Real Agent DB/parser/BOIS; direct contract tests explicitly simulate AI generation."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from app.models import LanguageVocabulary,KnowledgeTree,KnowledgeEdge
from app.database import Base
from app.core.agents.mindmap_agent import MindMapAgent
from app.core.agents.language_agent import LanguageAgent
from tests.test_generation_cache_isolation_flow import cache_flow
from tests.test_study_notification_flow import flow


@pytest.fixture
async def model_agents(cache_flow,monkeypatch):
    async with cache_flow.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,tables=[KnowledgeTree.__table__,KnowledgeEdge.__table__])
    async def simulated(**kwargs):
        if kwargs['prompt_template_id']=='mindmap.bois_restructure':
            content=json.dumps({'restructured_tree':{'nodes':[{'id':'sim-root','label':'模拟模型根节点',
                'level':1,'children':[{'id':'sim-child','label':'模拟子节点','level':2,'children':[]}]}]}})
        else:
            content=json.dumps([{'word':'simulated-vocabulary','definition':'明确模拟词汇'}])
        return SimpleNamespace(content=content)
    generated=AsyncMock(side_effect=simulated)
    monkeypatch.setattr(cache_flow.api,'generate',generated)
    yield SimpleNamespace(**vars(cache_flow),generated=generated)


@pytest.mark.parametrize('form',['nested','expanded'])
@pytest.mark.parametrize('kind',['mindmap','language'])
async def test_agent_selected_model_reaches_generation_config(model_agents,kind,form):
    config={'model':'openai/selected','force_restructure':True}
    kwargs={'config':config} if form=='nested' else config
    agent=MindMapAgent() if kind=='mindmap' else LanguageAgent()
    result=await agent.run(summary_id='summary',document_id='source',**kwargs)
    assert result.status=='success',result.error
    assert model_agents.generated.await_count==1
    assert model_agents.generated.await_args.kwargs['config'].model=='openai/selected'
    if kind=='mindmap':
        assert result.result['llm_restructured'] and result.result['total_nodes']==2
    else:
        async with model_agents.factory() as db:
            vocab=(await db.scalars(select(LanguageVocabulary))).all()
        assert len(vocab)==1 and vocab[0].word=='simulated-vocabulary'


@pytest.mark.parametrize('form',['nested','expanded'])
@pytest.mark.parametrize('enabled,forced,expected',[(False,True,False),(True,True,True),(True,False,False)])
async def test_mindmap_configuration_switches_use_same_contract(model_agents,monkeypatch,form,enabled,forced,expected):
    monkeypatch.setattr(MindMapAgent,'BOIS_SCORE_THRESHOLD',0)
    config={'model':'openai/selected','enable_bois_llm':enabled,'force_restructure':forced}
    result=await MindMapAgent().run('summary',document_id='source',**({'config':config} if form=='nested' else config))
    assert result.status=='success',result.error
    assert model_agents.generated.await_count==int(expected)
    assert result.result['llm_restructured']==expected


async def test_mindmap_provider_failure_preserves_basic_graph(model_agents):
    model_agents.generated.side_effect=RuntimeError('explicit simulated provider failure')
    result=await MindMapAgent().run('summary',force_restructure=True,model='openai/selected')
    assert result.status=='success' and not result.result['llm_restructured']
    assert result.result['total_nodes']==2 and result.result['nodes']


@pytest.mark.parametrize('kind',['mindmap','language'])
async def test_default_model_is_preserved(model_agents,kind):
    agent=MindMapAgent() if kind=='mindmap' else LanguageAgent()
    result=await agent.run('summary',document_id='source',force_restructure=True)
    assert result.status=='success'
    assert model_agents.generated.await_args.kwargs['config'].model=='deepseek-chat'


@pytest.mark.parametrize('form',['nested','expanded'])
async def test_disabled_restructure_prevents_low_score_automatic_model_call(model_agents,monkeypatch,form):
    monkeypatch.setattr(MindMapAgent,'BOIS_SCORE_THRESHOLD',101)
    config={'enable_bois_llm':False,'force_restructure':True,'model':'openai/selected'}
    result=await MindMapAgent().run('summary',**({'config':config} if form=='nested' else config))
    assert result.status=='success' and not result.result['llm_restructured']
    assert model_agents.generated.await_count==0
