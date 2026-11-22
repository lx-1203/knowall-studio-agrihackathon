"""Call receipt for the one cancellation regression; deletion after process exit."""
import os,json,hashlib
from pathlib import Path
from datetime import datetime
import pytest
def directory():return Path(os.environ['KNOWALL_BOOK_RUNTIME_ROOT']).resolve()
def hashes():
    phase=json.loads((directory().parent/'phase-before.json').read_text());root=directory().parents[2]
    return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in phase['Sources']}
@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    item.book_retry={'Test':item.nodeid,'Start':datetime.now().astimezone().isoformat(),'SourceBefore':hashes(),'PluginSourceBefore':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:return (yield)
    finally:item.book_retry.update(End=datetime.now().astimezone().isoformat(),SourceAfter=hashes(),PluginSourceAfter=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item,nextitem):
    import app.database
    path=app.database.async_session.kw['bind'].url.database
    try:return (yield)
    finally:
        d=item.book_retry;d['Database']=path;d['Cleanup']='literal deletion deferred until this pytest process exits; cancelled SQLite worker may retain a Windows handle'
        out=directory();out.mkdir(parents=True,exist_ok=True)
        (out/'retry.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')
