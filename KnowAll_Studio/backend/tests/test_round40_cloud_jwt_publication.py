"""Exercise the actual cloud configure function without starting user services."""
import ast
import hashlib
import multiprocessing as mp
import os
from pathlib import Path
import secrets
import tempfile
from contextlib import contextmanager

import pytest
from jose import jwt


@pytest.fixture(autouse=True)
def private_environment(monkeypatch):
    # configure_environment mutates many fields; isolate the entire mapping.
    monkeypatch.setattr(os, 'environ', dict(os.environ))


def configure_function():
    # Exact production function body, with its real stdlib/helper globals.
    from app.core.secret_file import load_or_create_secret
    source=Path(__file__).parents[1]/'cloud_entry.py'
    node=next(n for n in ast.parse(source.read_text(encoding='utf-8-sig')).body
              if isinstance(n,ast.FunctionDef) and n.name=='configure_environment')
    namespace=dict(os=os,secrets=secrets,Path=Path,load_or_create_secret=load_or_create_secret)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
    return namespace['configure_environment']


def entry_worker(directory,role,created,observed,release,results):
    os.environ['KNOWALL_CLOUD_DATA_ROOT']=directory
    os.environ.pop('JWT_SECRET',None)
    target=Path(directory)/'.jwt_secret'
    if role=='writer':
        original_open=Path.open
        class PausedCreate:
            def __init__(self,handle): self.handle=handle
            def __enter__(self):
                created.set(); assert release.wait(timeout=20)
                return self.handle.__enter__()
            def __exit__(self,*args): return self.handle.__exit__(*args)
        def opening(path,*args,**kwargs):
            handle=original_open(path,*args,**kwargs)
            return PausedCreate(handle) if path==target and args and args[0]=='x' else handle
        Path.open=opening
        original_temp=tempfile.NamedTemporaryFile
        @contextmanager
        def temporary(*args,**kwargs):
            with original_temp(*args,**kwargs) as handle:
                created.set(); assert release.wait(timeout=20)
                yield handle
        tempfile.NamedTemporaryFile=temporary
    else:
        for method in ['read_bytes','read_text']:
            original=getattr(Path,method)
            def watching(path,*args,_original=original,**kwargs):
                try: return _original(path,*args,**kwargs)
                finally:
                    if path==target: observed.set()
            setattr(Path,method,watching)
    try:
        _,secret=configure_function()()
        results.put(dict(pid=os.getpid(),hash=hashlib.sha256(secret.encode()).hexdigest()))
    except Exception as exc:
        results.put(dict(pid=os.getpid(),error=type(exc).__name__))


def test_other_entry_never_observes_unwritten_jwt_secret(tmp_path):
    ctx=mp.get_context('spawn'); created=ctx.Event(); observed=ctx.Event(); release=ctx.Event(); results=ctx.Queue()
    writer=ctx.Process(target=entry_worker,args=(str(tmp_path),'writer',created,observed,release,results))
    observer=ctx.Process(target=entry_worker,args=(str(tmp_path),'observer',created,observed,release,results))
    try:
        writer.start(); assert created.wait(timeout=15)
        observer.start(); assert observed.wait(timeout=15)
        release.set()
        rows=[results.get(timeout=20) for _ in range(2)]
        assert all('error' not in row for row in rows), rows
        assert len({row['pid'] for row in rows})==2 and len({row['hash'] for row in rows})==1
        assert len((tmp_path/'.jwt_secret').read_text())>=32
    finally:
        release.set()
        for child in [writer,observer]:
            if child.pid is not None:
                child.join(timeout=5)
                if child.is_alive(): child.kill(); child.join(timeout=5)
        results.close(); results.join_thread()


def test_existing_jwt_and_token_survive_next_entry(tmp_path,monkeypatch):
    old=secrets.token_urlsafe(64); (tmp_path/'.jwt_secret').write_text(old)
    monkeypatch.setenv('KNOWALL_CLOUD_DATA_ROOT',str(tmp_path)); monkeypatch.delenv('JWT_SECRET',raising=False)
    token=jwt.encode({'sub':'synthetic-owner'},old,algorithm='HS256')
    _,recovered=configure_function()()
    assert recovered==old and jwt.decode(token,recovered,algorithms=['HS256'])['sub']=='synthetic-owner'
    assert (tmp_path/'.jwt_secret').read_text()==old


def test_explicit_environment_secret_takes_precedence(tmp_path,monkeypatch):
    (tmp_path/'.jwt_secret').write_text('old-invalid')
    explicit=secrets.token_urlsafe(64)
    monkeypatch.setenv('KNOWALL_CLOUD_DATA_ROOT',str(tmp_path)); monkeypatch.setenv('JWT_SECRET',explicit)
    assert configure_function()()[1]==explicit
    assert (tmp_path/'.jwt_secret').read_text()=='old-invalid'


def test_bad_existing_jwt_refuses_start_without_replacement(tmp_path,monkeypatch):
    (tmp_path/'.jwt_secret').write_text('short')
    monkeypatch.setenv('KNOWALL_CLOUD_DATA_ROOT',str(tmp_path)); monkeypatch.delenv('JWT_SECRET',raising=False)
    with pytest.raises(RuntimeError,match='at least 32'): configure_function()()
    assert (tmp_path/'.jwt_secret').read_text()=='short'
