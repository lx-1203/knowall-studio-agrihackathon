"""Real temporary document SQL and owner checks; no embeddings/model calls."""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.database import Base
from app.models import Document,DocumentChunk
from app.core.agent_runtime.tool_bridge import DocumentToolBridge


@pytest.fixture
async def documents(tmp_path):
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'sources.sqlite').as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,tables=[Document.__table__,DocumentChunk.__table__])
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        for name,owner,text in [('a','alice','细胞呼吸原文'),('b','bob','细胞外人秘密'),('legacy',None,'细胞公开材料')]:
            db.add(Document(id=name,filename=name+'.txt',file_type='txt',sha256=name,status='ready',metadata_={'knowledge_owner_id':owner} if owner else {}))
            await db.flush()
            db.add(DocumentChunk(id=name+'-chunk',doc_id=name,chunk_index=0,text_content=text,page_range='p3-p4'))
        await db.commit()
    try: yield factory
    finally: await engine.dispose()


async def test_search_is_owned_and_returns_actual_source_metadata(documents):
    bridge=DocumentToolBridge(documents,'alice')
    result=await bridge.execute('c1','search_documents',{'query':'细胞'})
    assert result.status=='success'
    sources=result.output['sources']
    assert {s['document_id'] for s in sources}=={'a','legacy'}
    assert all(s['page_range']=='p3-p4' and s['citation_id'].startswith('S') for s in sources)
    assert '秘密' not in str(result.output)


@pytest.mark.parametrize('chunk',['b-chunk','missing'])
async def test_foreign_source_and_absent_source_are_indistinguishable(documents,chunk):
    bridge=DocumentToolBridge(documents,'alice')
    result=await bridge.execute('c1','read_document_chunk',{'chunk_id':chunk})
    assert result.status=='error' and result.output=={'error':'SOURCE_NOT_FOUND'}
    assert not bridge.sources


@pytest.mark.parametrize('args',[{'query':' '},{'query':'细胞','user_id':'bob'},{'query':'细胞','limit':True},{'query':'细胞','limit':100},{'query':9}])
async def test_schema_validation_cannot_override_identity_or_budget(documents,args):
    result=await DocumentToolBridge(documents,'alice').execute('c1','search_documents',args)
    assert result.status=='error' and result.output['error']=='INVALID_ARGUMENTS'


async def test_read_reuses_citation_identity_and_never_reads_other_user(documents):
    bridge=DocumentToolBridge(documents,'alice')
    first=await bridge.execute('c1','search_documents',{'query':'细胞呼吸'})
    second=await bridge.execute('c2','read_document_chunk',{'chunk_id':'a-chunk'})
    assert second.output['sources'][0]['citation_id']==first.output['sources'][0]['citation_id']=='S1'
    assert second.output['sources'][0]['text']=='细胞呼吸原文' and len(bridge.sources)==1


async def test_literal_wildcards_do_not_expand_document_search(documents):
    result=await DocumentToolBridge(documents,'alice').execute('c1','search_documents',{'query':'%'})
    assert result.status=='success' and result.output['sources']==[]


async def test_unknown_tool_does_not_run_another_registered_tool(documents):
    result=await DocumentToolBridge(documents,'alice').execute('c1','shell',{'command':'whoami'})
    assert result.status=='error' and result.output=={'error':'UNKNOWN_TOOL'}


@pytest.mark.parametrize('change',['filename','page_range','text','owner'])
async def test_source_verification_rejects_changed_citation_metadata(documents,change):
    bridge=DocumentToolBridge(documents,'alice')
    await bridge.execute('c1','read_document_chunk',{'chunk_id':'a-chunk'})
    async with documents() as db:
        doc=await db.get(Document,'a');chunk=await db.get(DocumentChunk,'a-chunk')
        if change=='filename':doc.filename='new.txt'
        if change=='page_range':chunk.page_range='p8'
        if change=='text':chunk.text_content='changed source'
        if change=='owner':doc.metadata_={'knowledge_owner_id':'bob'}
        await db.commit()
    assert await bridge.verified_sources(['S1']) is None


async def test_disabled_document_tools_are_not_advertised_or_executed(documents):
    bridge=DocumentToolBridge(documents,'alice',enabled=False)
    assert bridge.definitions()==[]
    result=await bridge.execute('c1','search_documents',{'query':'细胞'})
    assert result.output=={'error':'UNKNOWN_TOOL'} and bridge.sources=={}
