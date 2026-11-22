"""Round-only call-source evidence and literal disposable DB cleanup."""
import os,json,hashlib,tempfile
from pathlib import Path
from datetime import datetime
import pytest
def out():return Path(os.environ['KNOWALL_BOOK_RUNTIME_ROOT']).resolve()
def hashes():
    phase=json.loads((out().parent/'phase-before.json').read_text());root=out().parents[2]
    return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in phase['Sources']}
@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    item.book_receipt={'Test':item.nodeid,'Start':datetime.now().astimezone().isoformat(),'SourceBefore':hashes()}
    try:return (yield)
    finally:item.book_receipt.update(End=datetime.now().astimezone().isoformat(),SourceAfter=hashes())
@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item,nextitem):
    paths=set()
    for name in ('cache_flow','flow'):
        f=item.funcargs.get(name)
        if f is not None:
            path=getattr(f,'database',None)
            factory=getattr(f,'factory',None)
            if factory is None and name=='flow':
                import app.database
                factory=app.database.async_session
            if not path and factory:path=factory.kw['bind'].url.database
            if path:paths.add(Path(path).resolve())
    try:return (yield)
    finally:
        d=getattr(item,'book_receipt',{'Test':item.nodeid,'CallNotReached':True});d['Databases']=[]
        for p in paths:
            assert p.is_relative_to(Path(tempfile.gettempdir()).resolve()) and p.name in ('source.sqlite','study.sqlite') and p.parent.name.startswith('test_')
            deleted=[]
            for suffix in ('','-wal','-shm'):
                path=Path(str(p)+suffix);assert path.resolve().parent==p.parent
                if path.exists():path.unlink();deleted.append(str(path))
            d['Databases'].append({'Path':str(p),'Deleted':deleted,'Removed':not p.exists()})
        directory=out();directory.mkdir(parents=True,exist_ok=True)
        (directory/(hashlib.sha256(item.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')
