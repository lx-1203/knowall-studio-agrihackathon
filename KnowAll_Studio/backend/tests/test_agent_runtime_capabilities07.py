"""Readiness is observed SQL shape, never paid model inference or crash replay."""
import importlib
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text, event

RUN = {'id','user_id','conversation_id','assistant_message_id','status','scope','model','sources','steps','error_code','created_at','completed_at'}
BACKGROUND = {'run_id','user_id','cancel_requested','created_at'}
CHECKPOINT = {'run_id','user_id','input_ciphertext','version','created_at'}

@pytest.mark.parametrize('tables,expected', [
 ({}, (False,False,False)),
 ({'agent_runs':RUN}, (True,False,False)),
 ({'agent_runs':RUN,'agent_background_tasks':BACKGROUND}, (True,True,False)),
 ({'agent_runs':RUN,'agent_background_tasks':BACKGROUND,'agent_input_checkpoints':CHECKPOINT}, (True,True,True)),
 ({'agent_runs':RUN-{'steps'},'agent_background_tasks':BACKGROUND,'agent_input_checkpoints':CHECKPOINT}, (False,False,False)),
 ({'agent_runs':RUN,'agent_background_tasks':BACKGROUND-{'cancel_requested'},'agent_input_checkpoints':CHECKPOINT}, (True,False,False)),
 ({'agent_runs':RUN,'agent_background_tasks':BACKGROUND,'agent_input_checkpoints':CHECKPOINT-{'input_ciphertext'}}, (True,True,False)),
])
async def test_actual_table_shape_without_writes(tables,expected):
    module=importlib.import_module('app.core.agent_runtime.capabilities')
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for table,columns in tables.items():
            await connection.execute(text(f'CREATE TABLE {table} ('+','.join(c+' TEXT' for c in sorted(columns))+')'))
    statements=[]
    event.listen(engine.sync_engine,'before_cursor_execute',lambda c,cu,s,p,ctx,m:statements.append(s))
    try:
        async with async_sessionmaker(engine)() as db:
            actual=await module.runtime_capabilities(db)
            assert tuple(actual[k] for k in ('durable_runs','background_runs','input_checkpoints'))==expected
            assert actual['streaming_events']==expected[0]
            assert actual['streaming_tools'] is False
            assert actual['execution_resume'] is False
            closed=await module.runtime_capabilities(db,accepting_background=False)
            assert closed['background_runs'] is False
            assert closed['input_checkpoints']==expected[2]
        assert not any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','DROP')) for s in statements)
    finally:await engine.dispose()

async def test_database_error_is_not_fabricated_readiness():
    module=importlib.import_module('app.core.agent_runtime.capabilities')
    class Failed:
        async def connection(self):raise RuntimeError('unavailable-secret-database')
    with pytest.raises(RuntimeError):await module.runtime_capabilities(Failed())
