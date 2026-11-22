"""Round-specific runtime receipts and literal temporary SQLite cleanup."""
import os,json,hashlib,tempfile
from pathlib import Path
from datetime import datetime
import pytest

def receipt_root():return Path(os.environ['KNOWALL_QUOTA_RUNTIME_ROOT']).resolve()
def hashes():
    out=receipt_root();phase=json.loads((out.parent/'phase-before.json').read_text(encoding='utf8'))
    root=out.parents[2]
    return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in phase['Sources']}
@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    item.quota_receipt={'Test':item.nodeid,'Start':datetime.now().astimezone().isoformat(),'SourceBefore':hashes()}
    try:return (yield)
    finally:
        item.quota_receipt['End']=datetime.now().astimezone().isoformat();item.quota_receipt['SourceAfter']=hashes()
@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item,nextitem):
    databases=set()
    for name in ('cache_flow','store'):
        f=item.funcargs.get(name)
        if f is not None:
            path=getattr(f,'database',None) or getattr(f,'path',None)
            if path:databases.add(Path(path).resolve())
    try:return (yield)
    finally:
        d=getattr(item,'quota_receipt',{'Test':item.nodeid,'CallNotReached':True});d['Databases']=[]
        for path in databases:
            assert path.is_relative_to(Path(tempfile.gettempdir()).resolve())
            assert path.name in ('study.sqlite','cache-race.sqlite') and path.parent.name.startswith('test_')
            deleted=[]
            for suffix in ('','-wal','-shm'):
                p=Path(str(path)+suffix);assert p.resolve().parent==path.parent
                if p.exists():p.unlink();deleted.append(str(p))
            d['Databases'].append({'Path':str(path),'Deleted':deleted,'Removed':not path.exists()})
        out=receipt_root();out.mkdir(parents=True,exist_ok=True)
        (out/(hashlib.sha256(item.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')
