"""Two-process evidence against explicitly supplied disposable SQL/key paths."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser()
parser.add_argument('mode',choices=('write','read'))
parser.add_argument('database')
parser.add_argument('key')
args=parser.parse_args()
os.environ['KNOWALL_SECRET_KEY_FILE']=args.key


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.models import AgentRun,AgentBackgroundTask,AgentInputCheckpoint
    from app.database import Base
    from app.core.agent_runtime import store,input_checkpoint
    engine=create_async_engine('sqlite+aiosqlite:///'+Path(args.database).as_posix())
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        if args.mode=='write':
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all,tables=[AgentRun.__table__,AgentBackgroundTask.__table__,AgentInputCheckpoint.__table__])
            run_id=await store.start_background(factory,'process-owner',None,'requested-model',
                checkpoint_input={'message':'cross-process checkpoint','history':[{'role':'user','content':'bounded prior text'}]})
            print(json.dumps({'pid':os.getpid(),'run_id':run_id,'mode':args.mode,'paid_calls':0}))
        else:
            from sqlalchemy import select
            async with factory() as db:
                row=await db.scalar(select(AgentRun))
                cp,payload=await input_checkpoint.load_owned(db,row,'process-owner')
                print(json.dumps({'pid':os.getpid(),'run_id':row.id,'mode':args.mode,'message':payload.message,
                    'history':payload.model_dump()['history'],'status':row.status,'paid_calls':0,'execution_resumed':False}))
    finally:await engine.dispose()


asyncio.run(main())
