"""Cloud personal workspaces use separate processes, databases and file stores."""
import asyncio
import hashlib
import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from alembic import command
from alembic.config import Config
from app.database import Base
from app.models import User

BACKEND = Path(__file__).resolve().parents[2]


def workspace_directory(root, user_id):
    return Path(root).resolve() / hashlib.sha256(user_id.encode()).hexdigest()


def worker_environment(directory, user_id, jwt_secret, inherited=None):
    source = os.environ if inherited is None else inherited
    # Allow process infrastructure only; provider/admin secrets remain outside workers.
    permitted = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'LANG', 'LC_ALL', 'VIRTUAL_ENV', 'LD_LIBRARY_PATH', 'PYTHONPATH'}
    env = {key: value for key, value in source.items() if key.upper() in permitted}
    directory = Path(directory).resolve()
    env.update(KNOWALL_DATA_ROOT=str(directory), DATABASE_URL='sqlite+aiosqlite:///' + (directory / 'learning.sqlite').as_posix(),
               DOCUMENT_DIR=str(directory / 'documents'), EXPORT_DIR=str(directory / 'exports'),
               CHROMA_PERSIST_DIR=str(directory / 'vectors'), KNOWALL_LOG_DIR=str(directory / 'logs'),
               KNOWALL_SECRET_KEY_FILE=str(directory / '.secret_key'), JWT_SECRET=jwt_secret, DEBUG='false', LOCAL_AUTO_LOGIN='false',
               KNOWALL_DESKTOP_REQUIRE_LOGIN='true', KNOWALL_IGNORE_ENV_FILE='true',
               KNOWALL_WORKSPACE_OWNER=user_id, PYTHONUNBUFFERED='1', GRAPHRAG_ENABLED='false')
    return env


def initialize_schema(connection, legacy_owner_id=None):
    """Create a fresh versioned schema, or migrate a known schema without data loss."""
    config = Config(str(BACKEND / 'alembic.ini'))
    config.set_main_option('script_location', str(BACKEND / 'alembic'))
    config.attributes['connection'] = connection
    if legacy_owner_id is not None:
        config.attributes['legacy_conversation_owner'] = legacy_owner_id
    tables = set(inspect(connection).get_table_names())
    if not tables:
        Base.metadata.create_all(connection)
        command.stamp(config, 'head')
    elif 'alembic_version' not in tables:
        raise RuntimeError('Unversioned cloud database requires an explicit migration; refusing to replace data')
    else:
        command.upgrade(config, 'head')


@dataclass
class Worker:
    origin: str
    process: object
    log: object = None
    active: int = 0
    last_used: float = 0


class WorkspacePool:
    def __init__(self, root, jwt_secret, launcher=None, max_workers=8, idle_seconds=600):
        self.root = Path(root).resolve()
        self.jwt_secret = jwt_secret
        self.launcher = launcher or self._launch
        self.max_workers = max_workers
        self.idle_seconds = idle_seconds
        self.workers = {}
        self.lock = asyncio.Lock()

    def _stop(self, worker):
        if worker.process.poll() is None:
            control = getattr(worker.process, 'stdin', None)
            if control is not None and not control.closed:
                control.close()  # Own pipe EOF asks Uvicorn to finish its lifespan.
                try:
                    worker.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            if worker.process.poll() is None:
                worker.process.terminate()
            if hasattr(worker.process, 'wait'):
                try:
                    worker.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    worker.process.kill()
                    worker.process.wait(timeout=3)
        if getattr(worker, 'log', None):
            worker.log.close()

    async def _stop_async(self, worker):
        # Cancelling the coroutine cannot cancel its process-wait thread. Keep
        # the pool lock until that thread finishes, then propagate cancellation.
        pending = asyncio.create_task(asyncio.to_thread(self._stop, worker))
        cancelled = False
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled = True
        pending.result()
        if cancelled:
            raise asyncio.CancelledError

    async def get(self, user):
        # One launch at a time also bounds the short-lived port selection race.
        async with self.lock:
            now = time.monotonic()
            for uid, worker in list(self.workers.items()):
                if worker.process.poll() is not None or (not getattr(worker, 'active', 0) and now - getattr(worker, 'last_used', now) > self.idle_seconds):
                    await self._stop_async(worker)
                    del self.workers[uid]
            if user.id not in self.workers:
                if len(self.workers) >= self.max_workers:
                    raise HTTPException(503, '学习服务繁忙，请稍后重试')
                self.workers[user.id] = await self.launcher(user)
            worker = self.workers[user.id]
            worker.last_used = now
            return worker

    @asynccontextmanager
    async def lease(self, user):
        worker = await self.get(user)
        worker.active = getattr(worker, 'active', 0) + 1
        try:
            yield worker
        finally:
            worker.active -= 1
            worker.last_used = time.monotonic()

    async def close(self):
        async with self.lock:
            for worker in self.workers.values():
                await self._stop_async(worker)
            self.workers.clear()

    async def _launch(self, user):
        directory = workspace_directory(self.root, user.id)
        directory.mkdir(parents=True, exist_ok=True)
        # Seed only the owner's identity for existing route dependencies. Credentials
        # are managed by the central account service, never in this replica.
        def prepare():
            engine = create_engine('sqlite:///' + (directory / 'learning.sqlite').as_posix())
            from sqlalchemy.orm import Session
            try:
                with engine.begin() as connection:
                    initialize_schema(connection, legacy_owner_id=user.id)
                with Session(engine) as session:
                    stored = session.get(User, user.id)
                    if stored is None:
                        stored = User(id=user.id, username=user.username, email=user.email, password_hash='!central-account')
                        session.add(stored)
                    stored.username, stored.email = user.username, user.email
                    stored.is_active, stored.is_admin = True, bool(getattr(user, "is_admin", False))
                    session.commit()
            finally:
                engine.dispose()
        await asyncio.to_thread(prepare)
        with socket.socket() as socket_handle:
            socket_handle.bind(('127.0.0.1', 0))
            port = socket_handle.getsockname()[1]
        origin = 'http://127.0.0.1:' + str(port)
        log = (directory / 'worker.log').open('ab')
        process = subprocess.Popen([sys.executable, str(BACKEND / 'cloud_worker.py'), '--port', str(port)],
                                   cwd=BACKEND, env=worker_environment(directory, user.id, self.jwt_secret),
                                   # A service must not inherit the gateway's live
                                   # console/control pipe, including on restart.
                                   stdin=subprocess.PIPE, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        worker = Worker(origin, process, log, last_used=time.monotonic())
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(60):
                if process.poll() is not None:
                    break
                try:
                    response = await client.get(origin + '/health', timeout=1)
                    if response.status_code == 200:
                        return worker
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.5)
        await self._stop_async(worker)
        raise HTTPException(503, '个人学习空间启动失败，请稍后重试')
