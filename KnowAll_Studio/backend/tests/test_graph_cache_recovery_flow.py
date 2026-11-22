"""Private SQLite/Graph/cache/localhost; embeddings explicitly simulated, no AI."""
import copy
import hashlib
import json
import os
import pickle
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import delete

from app.core import graph_rag as graph, rag
from app.models import KnowledgeEdge, KnowledgePointNode
from tests.test_provider_provenance_flow import graph_store  # noqa: F401
from tests.test_search_citation_flow import store  # noqa: F401
from tests.test_search_recovery_flow import localhost

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ['backend/app/core/graph_rag.py','backend/app/core/rag.py','backend/app/api/chat.py',
           'backend/app/core/rag_assistant.py','backend/app/core/rag_pipeline/providers/graphrag_pipeline.py',
           'backend/app/core/tools/knowledge_search.py','backend/app/core/cloud_workspaces.py',
           'backend/app/config.py','backend/app/database.py','backend/app/models/__init__.py',
           'backend/tests/test_graph_cache_recovery_flow.py','backend/tests/test_provider_provenance_flow.py',
           'backend/tests/test_search_citation_flow.py','backend/tests/test_search_recovery_flow.py']
def hashes():
    return {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}

@pytest.fixture(autouse=True)
def receipt(request):
    before=hashes(); start=datetime.now().astimezone().isoformat(); facts={}
    yield facts
    evidence=os.environ.get('KNOWALL_GRAPH_CACHE_EVIDENCE')
    if evidence:
        name=hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]
        Path(evidence).mkdir(parents=True,exist_ok=True)
        Path(evidence,name+'.json').write_text(json.dumps({'Node':request.node.nodeid,'Start':start,
            'End':datetime.now().astimezone().isoformat(),'Before':before,'After':hashes(),
            'AI':'No live AI; deterministic simulated Chroma embedding','Facts':facts,
            'TemporaryDirectory':str(request.node.funcargs.get('tmp_path',''))},ensure_ascii=False,indent=2),encoding='utf8')

def read_cache():
    return pickle.loads(graph._cache_path().read_bytes())
def write_cache(data):
    graph._cache_path().write_bytes(pickle.dumps(data))
async def seed_cache():
    assert await graph.KnowledgeGraph().build(force_rebuild=True)==3
    return read_cache()

def damage(data, case):
    if case == 'missing_adj': data.pop('adj_out')
    elif case == 'nodes_list': data['nodes']=[]
    elif case == 'node_short': data['nodes']['node1']=('node1',)
    elif case == 'node_title_type': data['nodes']['node1']=('node1', [], '', 1, 'doc')
    elif case == 'node_id_mismatch': data['nodes']['node1']=('alien', 'wrong', '', 1, 'doc')
    elif case == 'node_level_type': data['nodes']['node1']=('node1', 'title', '', True, 'doc')
    elif case == 'adj_list': data['adj_out']=[]
    elif case == 'edge_short': data['adj_out']['node1']=[('node2',)]
    elif case == 'edge_dangling': data['adj_out']['node1']=[('alien','related_to','')]
    elif case == 'edge_text_type': data['adj_out']['node1']=[('node2','related_to',42)]
    elif case == 'inconsistent_reverse': data['adj_in']={}
    elif case == 'index_list': data['bigram_index']=[]
    elif case == 'index_dangling': data['bigram_index']['xx']=['alien']
    elif case == 'count_wrong': data['node_count']=999
    elif case == 'edge_count_wrong': data['edge_count']=999
    elif case == 'old_version': data['version']=-1
    elif case == 'legacy_scope': data.pop('scope',None)
    else: raise AssertionError(case)
    return data

@pytest.mark.parametrize('case',['missing_adj','nodes_list','node_short','node_title_type',
    'node_id_mismatch','node_level_type','adj_list','edge_short','edge_dangling','edge_text_type',
    'inconsistent_reverse','index_list','index_dangling','count_wrong','edge_count_wrong',
    'old_version','legacy_scope'])
async def test_corrupt_cache_is_a_miss_then_real_sql_rebuild(graph_store,case,receipt,monkeypatch):
    write_cache(damage(await seed_cache(),case))
    calls=[]; original=graph.async_session
    def observed(): calls.append('graph SQL'); return original()
    monkeypatch.setattr(graph,'async_session',observed)
    restored=graph.KnowledgeGraph()
    assert await restored.build()==3
    assert calls==['graph SQL']
    assert restored._nodes['node1'].title=='线粒体'
    assert restored._nodes['node1'].node_id=='node1'
    assert restored._nodes['node1'].level==1 and type(restored._nodes['node1'].level) is int
    assert restored._adj_in['node2']==[('node1','related_to','能量过程')]
    assert set(restored._bigram_index).issuperset({'线粒','粒体'})
    assert 'xx' not in restored._bigram_index
    assert read_cache()['node_count']==3 and read_cache()['edge_count']==1
    receipt.update(case=case,cache=str(graph._cache_path()),database=graph_store.store.factory.kw['bind'].url.database)

async def test_valid_warm_cache_does_not_query_graph_tables(graph_store,monkeypatch):
    await seed_cache()
    def unavailable(): raise AssertionError('warm cache should not query graph tables')
    monkeypatch.setattr(graph,'async_session',unavailable)
    restored=graph.KnowledgeGraph()
    assert await restored.build()==3
    assert restored._nodes['node2'].document_id=='doc2'

async def test_empty_graph_cache_is_valid_warm(graph_store,monkeypatch):
    async with graph_store.store.factory() as db:
        await db.execute(delete(KnowledgeEdge)); await db.execute(delete(KnowledgePointNode)); await db.commit()
    assert await graph.KnowledgeGraph().build(force_rebuild=True)==0
    def unavailable(): raise AssertionError('empty valid graph cache should stay warm')
    monkeypatch.setattr(graph,'async_session',unavailable)
    assert await graph.KnowledgeGraph().build()==0

@pytest.mark.parametrize('data',[b'',b'not pickle',pickle.dumps([]),pickle.dumps({'version':2})])
async def test_unreadable_cache_cold_rebuilds(graph_store,data):
    graph._cache_path().write_bytes(data)
    assert await graph.KnowledgeGraph().build()==3

async def test_atomic_writer_keeps_reader_on_complete_snapshot(graph_store,monkeypatch,receipt):
    await seed_cache(); original=graph.pickle.dump; ready=threading.Event(); release=threading.Event()
    data=read_cache(); data['nodes']['node1']=('node1','replacement title','complete',1,'doc')
    nodes={key:graph.GraphNode(*row) for key,row in data['nodes'].items()}
    def paused(data,handle,**kwargs):
        original(data,handle,**kwargs); handle.flush(); ready.set()
        assert release.wait(10),'reader did not release writer'
    monkeypatch.setattr(graph.pickle,'dump',paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(graph._save_graph_to_disk,data['adj_out'],data['adj_in'],nodes,data['bigram_index'],data['edge_count'])
        try:
            assert ready.wait(10)
            old=graph._load_graph_from_disk()
            assert old['nodes']['node1'][1]=='线粒体'
            assert len(list(graph._cache_path().parent.glob(graph._cache_path().name+'.*.tmp')))==1
        finally: release.set()
        assert future.result(timeout=10) is True
    assert graph._load_graph_from_disk()['nodes']['node1'][1]=='replacement title'
    assert list(graph._cache_path().parent.glob(graph._cache_path().name+'.*.tmp'))==[]
    receipt.update(ReaderBefore='线粒体',ReaderAfter='replacement title',RealFilesystem=True)

async def test_replace_failure_preserves_old_cache_and_removes_temp(graph_store,monkeypatch):
    await seed_cache(); before=graph._cache_path().read_bytes()
    def denied(*args): raise PermissionError('simulated replacement denied')
    with monkeypatch.context() as patch:
        patch.setattr(graph.os,'replace',denied)
        assert await graph.KnowledgeGraph().build(force_rebuild=True)==3
    assert graph._cache_path().read_bytes()==before
    assert list(graph._cache_path().parent.glob(graph._cache_path().name+'.*.tmp'))==[]

async def test_failed_temp_cleanup_does_not_turn_cache_write_into_service_failure(graph_store,monkeypatch):
    await seed_cache(); before=graph._cache_path().read_bytes(); original=Path.unlink
    def denied(*args): raise PermissionError('simulated replacement denied')
    def deny_cleanup(self,*args,**kwargs):
        if self.name.endswith('.tmp'): raise PermissionError('simulated cleanup denied')
        return original(self,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(graph.os,'replace',denied); patch.setattr(Path,'unlink',deny_cleanup)
        assert await graph.KnowledgeGraph().build(force_rebuild=True)==3
    assert graph._cache_path().read_bytes()==before
    leftovers=list(graph._cache_path().parent.glob(graph._cache_path().name+'.*.tmp'))
    assert len(leftovers)==1
    for path in leftovers: path.unlink()

async def test_cache_scope_is_digest_and_not_database_connection_string(graph_store):
    data=await seed_cache(); raw=graph._cache_path().read_bytes()
    assert len(data['scope'])==64 and all(c in '0123456789abcdef' for c in data['scope'])
    assert rag.settings.database_url.encode() not in raw

@pytest.mark.parametrize('setting',['database_url','chroma_persist_dir'])
async def test_foreign_storage_cache_never_reuses_titles(graph_store,monkeypatch,setting):
    data=await seed_cache()
    # A valid but different configured store. Public ownership remains real SQL.
    changed=rag.settings.database_url+'?scope=other' if setting=='database_url' else rag.settings.chroma_persist_dir+'-other'
    monkeypatch.setattr(rag.settings,setting,changed)
    # Alter cached title, while authoritative graph SQL retains its original title.
    data['nodes']['node1']=('node1','WRONG STORE TITLE','foreign',1,'doc')
    write_cache(data)
    assert await graph.KnowledgeGraph().build()==3
    assert read_cache()['nodes']['node1'][1]=='线粒体'

async def test_failed_serialization_preserves_previous_complete_cache(graph_store,monkeypatch):
    await seed_cache(); before=graph._cache_path().read_bytes()
    def failing(data,handle,**kwargs):
        handle.write(b'partial'); raise OSError('simulated disk-write interruption')
    with monkeypatch.context() as patch:
        patch.setattr(graph.pickle,'dump',failing)
        assert await graph.KnowledgeGraph().build(force_rebuild=True)==3
    assert graph._cache_path().read_bytes()==before
    assert list(graph._cache_path().parent.glob(graph._cache_path().name+'.*.tmp'))==[]
    assert await graph.KnowledgeGraph().build()==3

async def test_native_http_recovers_malformed_graph_cache(graph_store,receipt):
    write_cache(damage(await seed_cache(),'node_short'))
    records=[]
    async with localhost(graph_store.app) as client:
        for route in ['search/graphrag','graphrag/stats','graphrag/rebuild','search/graphrag']:
            if route.endswith('stats'): response=await client.get('/api/v1/chat/'+route)
            else: response=await client.post('/api/v1/chat/'+route,params={'query':'线粒体'})
            records.append({'Route':route,'Status':response.status_code,'Body':response.json()})
            assert response.status_code==200,response.text
        first=records[0]['Body']; by_id={row['id']:row for row in first['results']}
        assert by_id['node1']['title']=='线粒体' and by_id['node1']['metadata']['doc_id']=='doc'
        assert records[1]['Body']['node_count']==3
        receipt.update(HTTP=records,Port=client.base_url.port,Database=graph_store.store.factory.kw['bind'].url.database)

@pytest.mark.parametrize('mode',['restart','foreign','corrupt'])
def test_independent_process_cache_storage_and_restart(tmp_path,mode,receipt):
    cache=tmp_path/'graph.pkl'; first=tmp_path/'first.sqlite'; other=tmp_path/'other.sqlite'
    def child(db,title,stage):
        output=tmp_path/(stage+'.json')
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('graph_cache_worker.py')),
            str(db),str(cache),title,stage,str(output)],cwd=ROOT/'backend',capture_output=True,text=True,encoding='utf8',timeout=35,
            env=dict(os.environ,PYTHONPATH=str(ROOT/'backend'),KNOWALL_IGNORE_ENV_FILE='true'))
        receipt.setdefault('Processes',[]).append({'Stage':stage,'ExitCode':result.returncode,'Stdout':result.stdout,'Stderr':result.stderr})
        assert result.returncode==0,result.stderr
        assert 'PROCESS_GRAPH_OK' in result.stdout
        receipt['Processes'][-1]['Receipt']=json.loads(output.read_text(encoding='utf8'))
    child(first,'FIRST STORE TITLE','seed')
    if mode=='restart': child(first,'FIRST STORE TITLE','warm')
    elif mode=='foreign': child(other,'SECOND STORE TITLE','cold')
    else:
        data=pickle.loads(cache.read_bytes()); data['nodes']['node']=('node',); cache.write_bytes(pickle.dumps(data))
        child(first,'FIRST STORE TITLE','cold')
    for path in [first,other]:
        if path.exists(): path.unlink()
    receipt['DatabasesRemoved']=all(not p.exists() for p in [first,other])
