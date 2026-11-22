"""Two actual Python processes collide on the same private SQLite quota row."""
import asyncio,json,sys,subprocess,hashlib
from pathlib import Path
from datetime import datetime
import pytest
from app.models import APIQuota
from tests.test_quota_first_use_flow import quota_flow,cache_flow,flow,rows

async def worker(database,gate,name,limit,estimate):
    import importlib,tempfile
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
    from sqlalchemy.sql.selectable import Select
    from app.config import settings
    rate=importlib.import_module('app.core.api_scheduler.rate_limiter')
    db=Path(database).resolve();gate=Path(gate).resolve()
    assert db.is_relative_to(Path(tempfile.gettempdir()).resolve()) and db.name=='study.sqlite'
    assert gate.parent==db.parent
    settings.daily_token_limit=limit
    engine=create_async_engine('sqlite+aiosqlite:///'+db.as_posix(),connect_args={'timeout':10})
    first=True
    class GatedSession(AsyncSession):
        async def execute(self,statement,*args,**kwargs):
            nonlocal first
            result=await super().execute(statement,*args,**kwargs)
            if first and isinstance(statement,Select) and statement.column_descriptions[0].get('entity') is APIQuota:
                first=False;(gate/(name+'.ready')).write_text('actual first SELECT completed',encoding='utf8')
                async with asyncio.timeout(10):
                    while not (gate/'release').exists():await asyncio.sleep(.01)
            return result
    rate.async_session=async_sessionmaker(engine,class_=GatedSession,expire_on_commit=False)
    started=datetime.now().astimezone().isoformat()
    try:
        result=await rate.RateLimiter().check_quota('alice',estimate)
        print(json.dumps({'Start':started,'End':datetime.now().astimezone().isoformat(),'Name':name,'Decision':result,
            'SourceSHA256':hashlib.sha256(Path(rate.__file__).read_bytes()).hexdigest()}))
    finally:await engine.dispose()

@pytest.mark.parametrize('limit,estimate,allowed',[(1,2,False),(0,1,False),(2,2,True)])
async def test_independent_process_first_creation_obeys_budget_after_unique_conflict(quota_flow,limit,estimate,allowed):
    f=quota_flow;gate=f.database.parent/'quota-process-gate';gate.mkdir()
    entry='import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")'
    commands=[[sys.executable,'-c',entry,str(Path(__file__).resolve()),str(f.database),str(gate),name,str(limit),str(estimate)] for name in ('one','two')]
    children=[subprocess.Popen(command,cwd=Path(__file__).resolve().parents[1],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf8') for command in commands]
    try:
        async with asyncio.timeout(20):
            while not all((gate/(name+'.ready')).exists() for name in ('one','two')):
                if any(c.poll() is not None for c in children):raise AssertionError([c.communicate() for c in children])
                await asyncio.sleep(.01)
        (gate/'release').write_text('both processes selected before INSERT',encoding='utf8')
        outputs=await asyncio.gather(*(asyncio.to_thread(c.communicate,timeout=15) for c in children))
        records=[]
        for command,c,(stdout,stderr) in zip(commands,children,outputs):
            record={'Command':command,'ExitCode':c.returncode,'Stdout':stdout,'Stderr':stderr};records.append(record)
            assert c.returncode==0,record
            data=json.loads(stdout);assert data['Decision'][0] is allowed,data
            assert data['SourceSHA256']==f.evidence['SourceBefore']['app/core/api_scheduler/rate_limiter.py']
        f.evidence['IndependentProcesses']=records
        assert len(await rows(f,APIQuota))==1 and (await rows(f,APIQuota))[0].used_today==0
    finally:
        for c in children:
            if c.poll() is None:c.kill()
            await asyncio.to_thread(c.wait)
        f.evidence['ChildExitCodes']=[c.returncode for c in children]
        for name in ('one.ready','two.ready','release'):
            p=gate/name
            if p.exists():p.unlink()
        gate.rmdir();f.evidence['IPCGateRemoved']=not gate.exists()

if __name__=='__main__':
    asyncio.run(worker(sys.argv[1],sys.argv[2],sys.argv[3],int(sys.argv[4]),int(sys.argv[5])))
