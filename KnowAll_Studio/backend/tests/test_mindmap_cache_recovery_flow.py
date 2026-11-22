"""Actual scheduler/cache/SQLite; only external AI is explicitly simulated."""
import json,os,hashlib
from pathlib import Path
from datetime import datetime
import pytest
from app.database import Base
from app.models import APICache,APICallLog,APIQuota,KnowledgeTree,KnowledgeEdge
from app.core.agents.mindmap_agent import MindMapAgent
from app.core.knowledge.bois_analyzer import bois_analyzer
from tests.test_generation_cache_isolation_flow import cache_flow,flow,rows

VALID={'restructured_tree':{'nodes':[{'id':'r','label':'细胞','level':1,'children':[{'id':'c','label':'能量','level':2,'children':[]}]}]}}
def node(**kwargs):return {'id':'r','label':'细胞',**kwargs}
def tree(*nodes):return json.dumps({'restructured_tree':{'nodes':list(nodes)}},ensure_ascii=False)
def deep_tree(depth):
    root=node();current=root
    for n in range(2,depth+1):
        child=node(id=str(n),level=min(n,4));current['children']=[child];current=child
    return tree(root)
INVALID=['not JSON','null','[]','{}','{"restructured_tree":[]}',tree(),
    '{"restructured_tree":{"nodes":{}}}',tree(1),tree(node(id=1)),tree(node(id=' ')),
    tree(node(label=3)),tree(node(label=' ')),tree(node(level='2')),tree(node(level=True)),
    tree(node(level=0)),tree(node(level=5)),tree(node(tag=None)),tree(node(summary=3)),
    tree(node(children=None)),tree(node(children={})),tree(node(),node()),
    tree(node(children=[node()])),deep_tree(5),tree(*[node(id=str(n)) for n in range(201)])]

@pytest.fixture
async def mindmap_flow(cache_flow,monkeypatch,request,tmp_path):
    f=cache_flow
    monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost');monkeypatch.setenv('no_proxy','127.0.0.1,localhost')
    async with f.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,tables=[KnowledgeTree.__table__,KnowledgeEdge.__table__])
    original=f.api.generate
    async def generate(**kwargs):
        kwargs['config'].max_retries=0;kwargs['config'].fallback_models=[]
        return await original(**kwargs)
    monkeypatch.setattr(f.api,'generate',generate)
    f.api._adapters['deepseek-chat']=f.adapters['primary']
    root=Path(__file__).resolve().parents[1]
    paths=['app/core/agents/mindmap_agent.py','app/core/api_scheduler/client.py','app/core/api_scheduler/cache.py',
           'app/core/api_scheduler/rate_limiter.py','app/core/knowledge/bois_analyzer.py','app/models/__init__.py','app/api/agents.py']
    hashes=lambda:{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
    d={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),'SourceBefore':hashes(),
       'AI':'explicit in-process simulated adapter; actual SQLite/cache/quota/BOIS','DB':str(f.database)}
    f.evidence=d
    try:yield f
    finally:
        d['SourceAfter']=hashes();d['End']=datetime.now().astimezone().isoformat();d['AdapterCalls']=len(f.adapters['primary'].calls)
        await f.engine.dispose()
        for suffix in ('','-wal','-shm'):
            p=Path(str(f.database)+suffix);assert p.resolve().parent==tmp_path.resolve()
            if p.exists():p.unlink()
        d['DBRemoved']=not f.database.exists()
        out=Path(os.environ.get('KNOWALL_MINDMAP_EVIDENCE_DIR',str(tmp_path/'mindmap-evidence')))
        out.mkdir(parents=True,exist_ok=True)
        (out/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')

async def restructure(f,body):
    f.adapters['primary'].content=body
    nodes=[node(level=1,summary='',tag='')]
    return await MindMapAgent()._llm_bois_restructure(nodes,[],bois_analyzer.analyze(nodes,[]))

@pytest.mark.parametrize('body',INVALID)
async def test_invalid_restructure_is_not_cached_and_same_request_recovers(mindmap_flow,body):
    f=mindmap_flow
    first=await restructure(f,body);f.evidence['First']=first
    assert first is None
    assert await rows(f,APICache)==[]
    logs=await rows(f,APICallLog);assert len(logs)==1 and not logs[0].success
    recovered=await restructure(f,json.dumps(VALID));assert recovered and len(recovered[0])==2
    cached=await restructure(f,'unused');assert cached==recovered
    assert len(f.adapters['primary'].calls)==2
    assert len(await rows(f,APICache))==1
    assert [x.success for x in await rows(f,APICallLog)]==[False,True]
    assert (await rows(f,APIQuota))[0].used_today==36
    f.evidence['Recovered']=recovered

@pytest.mark.parametrize('body',['not JSON',tree(),tree(node(level='2')),tree(node(children=[node()]))])
async def test_old_invalid_mindmap_cache_is_evicted_and_replaced(mindmap_flow,body):
    f=mindmap_flow
    assert await restructure(f,json.dumps(VALID))
    async with f.factory() as db:
        c=await db.get(APICache,(await rows(f,APICache))[0].cache_key);c.response_content=body;await db.commit()
    result=await restructure(f,json.dumps(VALID));assert result and len(result[0])==2
    assert len(f.adapters['primary'].calls)==2
    assert len(await rows(f,APICache))==1
    assert json.loads((await rows(f,APICache))[0].response_content)==VALID
    f.evidence['Recovered']=result

@pytest.mark.parametrize('style',['normal','optional','unicode','four-level','200-nodes','long-summary'])
async def test_valid_mindmap_schema_preserves_flattening_and_cache(mindmap_flow,style):
    f=mindmap_flow;body=json.dumps(VALID)
    if style=='optional':body=tree(node())
    if style=='unicode':body=tree(node(id='é中文🙂',label='Unicode [] 方括号',children=[]))
    if style=='four-level':body=deep_tree(4)
    if style=='200-nodes':body=tree(*[node(id=str(n)) for n in range(200)])
    if style=='long-summary':body=tree(node(summary='细'*150))
    result=await restructure(f,body);assert result
    assert await restructure(f,'unused')==result
    assert len(f.adapters['primary'].calls)==1 and len(await rows(f,APICache))==1
    if style=='normal':assert result[1]==[{'source':'r','target':'c','relation':'parent_child'}]
    if style=='long-summary':assert result[0][0]['summary']=='细'*120
    if style=='200-nodes':assert len(result[0])==200
    if style=='optional':assert result[0][0]['level']==1 and result[0][0]['tag']==''
    f.evidence['Result']=result

@pytest.mark.parametrize('body',['not JSON',tree(),tree(node(level='2')),tree(node(label=3))])
async def test_bad_restructure_preserves_basic_graph_then_run_recovers(mindmap_flow,body):
    f=mindmap_flow;f.adapters['primary'].content=body
    agent=MindMapAgent();first=await agent.run('summary',force_restructure=True)
    assert first.status=='success',first.error
    assert not first.result['llm_restructured'] and first.result['total_nodes']==2
    assert await rows(f,APICache)==[]
    f.adapters['primary'].content=json.dumps(VALID)
    recovered=await agent.run('summary',force_restructure=True)
    assert recovered.status=='success' and recovered.result['llm_restructured']
    assert len(f.adapters['primary'].calls)==2
    f.evidence['Fallback']=first.result;f.evidence['Recovered']=recovered.result

@pytest.mark.parametrize('body',[
    tree({'label':'missing-id'}),tree({'id':'missing-label'}),tree(node(id={})),
    tree(node(level=2.0)),tree(node(tag=False)),tree(node(summary=[])),
    tree(node(children=[{'id':'child','label':'c','children':[1]}])),
])
async def test_nested_and_missing_field_boundaries_keep_basic_graph(mindmap_flow,body):
    f=mindmap_flow;f.adapters['primary'].content=body
    result=await MindMapAgent().run('summary',force_restructure=True)
    assert result.status=='success' and not result.result['llm_restructured']
    assert await rows(f,APICache)==[] and not (await rows(f,APICallLog))[0].success
    f.evidence['Fallback']=result.result

@pytest.mark.parametrize('style',['fenced','prefix','200-nested'])
async def test_normalized_and_bounded_nested_output_is_usable(mindmap_flow,style):
    f=mindmap_flow
    body=json.dumps(VALID)
    if style=='fenced':body='```json\n'+body+'\n```'
    if style=='prefix':body='模型说明：\n'+body
    if style=='200-nested':body=tree(node(level=1,children=[node(id=str(n),level=2) for n in range(199)]))
    f.adapters['primary'].content=body
    result=await MindMapAgent().run('summary',force_restructure=True)
    assert result.status=='success' and result.result['llm_restructured'],result.error
    expected=200 if style=='200-nested' else 2
    assert result.result['total_nodes']==expected and result.result['total_edges']==expected-1
    cached=await MindMapAgent().run('summary',force_restructure=True)
    assert cached.result==result.result and len(f.adapters['primary'].calls)==1
    f.evidence['Result']=result.result

async def test_disable_restructure_bypasses_scheduler_without_cache(mindmap_flow):
    f=mindmap_flow;f.adapters['primary'].content='should never be used'
    result=await MindMapAgent().run('summary',force_restructure=True,enable_bois_llm=False)
    assert result.status=='success' and not result.result['llm_restructured']
    assert await rows(f,APICache)==await rows(f,APICallLog)==await rows(f,APIQuota)==[]
    assert f.adapters['primary'].calls==[]
    f.evidence['Result']=result.result
