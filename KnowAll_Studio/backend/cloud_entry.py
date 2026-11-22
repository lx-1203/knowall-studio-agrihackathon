"""Cloud deployment entry: configure persistent paths before importing the app."""
import os
import secrets
from pathlib import Path
from app.core.secret_file import load_or_create_secret


def configure_environment():
    root = Path(os.environ.get('KNOWALL_CLOUD_DATA_ROOT', '/srv/knowall')).resolve()
    root.mkdir(parents=True, exist_ok=True)
    secret = os.environ.get('JWT_SECRET', '').strip()
    if not secret:
        path = root / '.jwt_secret'
        secret = load_or_create_secret(path, lambda: secrets.token_urlsafe(64).encode('utf-8')).decode('utf-8').strip()
    if len(secret) < 32:
        raise RuntimeError('JWT_SECRET must contain at least 32 characters')
    os.environ.update(
        KNOWALL_CLOUD_DATA_ROOT=str(root), KNOWALL_DATA_ROOT=str(root),
        DATABASE_URL='sqlite+aiosqlite:///' + (root / 'accounts.sqlite').as_posix(),
        JWT_SECRET=secret, DEBUG='false', LOCAL_AUTO_LOGIN='false',
        KNOWALL_DESKTOP_REQUIRE_LOGIN='true', KNOWALL_IGNORE_ENV_FILE='true',
        DOCUMENT_DIR=str(root / 'central' / 'documents'), EXPORT_DIR=str(root / 'central' / 'exports'),
        CHROMA_PERSIST_DIR=str(root / 'central' / 'vectors'), KNOWALL_LOG_DIR=str(root / 'logs'),
        KNOWALL_SECRET_KEY_FILE=str(root / 'central' / '.secret_key'), GRAPHRAG_ENABLED='false')
    return root, secret


DATA_ROOT, JWT_SECRET = configure_environment()

from app import database
from app.core.cloud_workspaces import WorkspacePool, initialize_schema
from app.core.cloud_gateway import create_cloud_app


async def initialize_accounts():
    async with database.engine.begin() as connection:
        await connection.run_sync(initialize_schema)


pool = WorkspacePool(DATA_ROOT / 'workspaces', JWT_SECRET,
                     max_workers=int(os.environ.get('KNOWALL_CLOUD_MAX_WORKERS', '8')),
                     idle_seconds=int(os.environ.get('KNOWALL_CLOUD_IDLE_SECONDS', '600')))
app = create_cloud_app(pool, initialize=initialize_accounts)
