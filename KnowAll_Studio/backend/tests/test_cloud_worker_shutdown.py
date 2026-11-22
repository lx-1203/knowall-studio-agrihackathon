"""A managed worker must finish its database cleanup before it is recycled."""
import sqlite3
import subprocess
import sys
from contextlib import closing
import asyncio

import pytest

from app.core.cloud_workspaces import Worker, WorkspacePool


def test_recycle_allows_real_process_to_commit_and_close_database(tmp_path):
    db = tmp_path / 'exit.sqlite'
    process = subprocess.Popen([sys.executable, '-u', '-c', '''
import sqlite3,sys
connection=sqlite3.connect(sys.argv[1])
connection.execute('create table shutdown_done(value integer)')
connection.commit()
print('READY',flush=True)
sys.stdin.buffer.read()
connection.execute('insert into shutdown_done values (1)')
connection.commit()
connection.close()
''', str(db)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdout.readline().strip() == b'READY'
        WorkspacePool(tmp_path, 'test')._stop(Worker('http://127.0.0.1:1', process))
        assert process.poll() == 0, 'Recycle forcibly terminated the database worker'
        with closing(sqlite3.connect(db)) as connection:
            assert connection.execute('select value from shutdown_done').fetchall() == [(1,)]
        db.rename(tmp_path / 'closed.sqlite')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()


@pytest.mark.asyncio
async def test_recycle_keeps_unrelated_gateway_coroutine_responsive(tmp_path):
    process = subprocess.Popen([sys.executable, '-u', '-c', '''
import sys,time
print('READY',flush=True)
sys.stdin.buffer.read()
time.sleep(0.5)
'''], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdout.readline().strip() == b'READY'
        pool = WorkspacePool(tmp_path, 'test')
        pool.workers['owner'] = Worker('http://127.0.0.1:1', process)
        events = []

        async def independent_request():
            await asyncio.sleep(0.05)
            events.append('request')

        async def recycle():
            await pool.close()
            events.append('closed')

        await asyncio.gather(recycle(), independent_request())
        assert events == ['request', 'closed'], 'Worker wait blocked unrelated gateway requests'
        assert process.returncode == 0
        assert pool.workers == {}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()


@pytest.mark.asyncio
async def test_cancelled_recycle_finishes_process_exit_before_releasing_pool(tmp_path):
    process = subprocess.Popen([sys.executable, '-u', '-c', '''
import sys,time
print('READY',flush=True)
sys.stdin.buffer.read()
time.sleep(0.5)
'''], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdout.readline().strip() == b'READY'
        pool = WorkspacePool(tmp_path, 'test')
        pool.workers['owner'] = Worker('http://127.0.0.1:1', process)
        task = asyncio.create_task(pool.close())
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.poll() == 0, 'Cancellation released the pool while its database worker was still exiting'
        assert not pool.lock.locked()
    finally:
        if process.poll() is None:
            await asyncio.to_thread(process.wait, timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()
