"""Round-only provenance for old regression tests, no product monkeypatches."""
import hashlib, json, os
from datetime import datetime
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item,nextitem):
    evidence=os.environ.get('KNOWALL_GRAPH_REVIEW_RUNTIME')
    if not evidence:
        yield; return
    paths=json.loads((ROOT/'docs/test-round-06a3a/phase.json').read_text(encoding='utf8'))['Sources']
    def hashes(): return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
    start=datetime.now().astimezone().isoformat(); before=hashes()
    yield
    tmp=getattr(item,'_graph_round_tmp',None)
    files=[str(p) for p in tmp.rglob('*.sqlite*')] if tmp else []
    out=Path(evidence); out.mkdir(parents=True,exist_ok=True)
    out.joinpath(hashlib.sha256(item.nodeid.encode()).hexdigest()[:16]+'.json').write_text(json.dumps({
        'Node':item.nodeid,'Start':start,'End':datetime.now().astimezone().isoformat(),
        'Before':before,'After':hashes(),'TemporaryDirectory':str(tmp) if tmp else None,
        'DatabaseFiles':files},ensure_ascii=False,indent=2),encoding='utf8')

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    yield
    if 'tmp_path' in item.funcargs: item._graph_round_tmp=item.funcargs['tmp_path']
