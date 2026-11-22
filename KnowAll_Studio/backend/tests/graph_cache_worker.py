"""Isolated process receipt for real SQLite/cache, no Chroma or AI requests."""
import asyncio, hashlib, json, sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
db_path, cache_path, title, mode, output = map(str,sys.argv[1:])
start=datetime.now().astimezone().isoformat()
source=ROOT/'backend/app/core/graph_rag.py'
before=hashlib.sha256(source.read_bytes()).hexdigest()

async def run():
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.config import settings
    from app.database import Base
    from app.models import Document,KnowledgePointNode,KnowledgeEdge
    from app.core import graph_rag as graph
    settings.database_url='sqlite+aiosqlite:///'+Path(db_path).as_posix()
    settings.chroma_persist_dir=str(Path(db_path).with_suffix('.vectors'))
    engine=create_async_engine(settings.database_url)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,tables=[Document.__table__,KnowledgePointNode.__table__,KnowledgeEdge.__table__])
    async with factory() as db:
        if await db.get(Document,'doc') is None:
            db.add(Document(id='doc',filename='private-process.txt',file_type='txt',sha256=title))
            db.add(KnowledgePointNode(id='node',document_id='doc',title=title,explanation='same id, authoritative SQL',level=1,sequence=1))
            await db.commit()
    graph.async_session=factory
    graph._cache_path=lambda:Path(cache_path)
    if mode=='warm':
        def unavailable(): raise AssertionError('warm must not query graph SQL tables')
        graph.async_session=unavailable
    try:
        instance=graph.KnowledgeGraph()
        count=await instance.build(force_rebuild=mode=='seed')
        assert count==1
        assert instance._nodes['node'].title==title
        return {'Count':count,'Title':instance._nodes['node'].title,'Database':db_path,'Cache':cache_path,'Mode':mode}
    finally: await engine.dispose()

facts=asyncio.run(run())
Path(output).write_text(json.dumps({'Start':start,'End':datetime.now().astimezone().isoformat(),
    'PID':__import__('os').getpid(),'Before':before,'After':hashlib.sha256(source.read_bytes()).hexdigest(),
    'AI':'No AI or embedding requests','Facts':facts},ensure_ascii=False,indent=2),encoding='utf8')
print('PROCESS_GRAPH_OK',json.dumps(facts,ensure_ascii=False))
