"""Real SQLite failures must release pools without replacing data or errors."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.core import cloud_workspaces
from app.core.cloud_workspaces import WorkspacePool, initialize_schema, workspace_directory
from app.models import User


@pytest.mark.parametrize('failure', ['unversioned', 'owner_conflict'])
async def test_prepare_failure_releases_database_and_preserves_original_error(tmp_path, monkeypatch, failure):
    owner = SimpleNamespace(id='owner', username='owner', email='owner@example.test')
    directory = workspace_directory(tmp_path, owner.id)
    directory.mkdir()
    database = directory / 'learning.sqlite'
    setup = create_engine('sqlite:///' + database.as_posix())
    try:
        with setup.begin() as connection:
            if failure == 'unversioned':
                connection.execute(text('CREATE TABLE keep (value INTEGER)'))
                connection.execute(text('INSERT INTO keep VALUES (7)'))
            else:
                initialize_schema(connection)
                connection.execute(User.__table__.insert().values(
                    id='existing', username=owner.username, email='other@example.test', password_hash='!test'))
    finally:
        setup.dispose()
    engines = []

    def track_engine(*args, **kwargs):
        engine = create_engine(*args, **kwargs)
        engines.append(engine)
        return engine

    def forbidden_spawn(*args, **kwargs):
        pytest.fail('Preparation failure must not spawn a private worker')

    monkeypatch.setattr(cloud_workspaces, 'create_engine', track_engine)
    monkeypatch.setattr(cloud_workspaces.subprocess, 'Popen', forbidden_spawn)
    pool = WorkspacePool(tmp_path, 'temporary-test-secret')
    try:
        error = RuntimeError if failure == 'unversioned' else IntegrityError
        with pytest.raises(error):
            await pool._launch(owner)
        assert len(engines) == 1
        assert engines[0].pool.checkedin() == 0, 'Preparation retained a live database connection'
        # On Windows this also proves no live SQLite file handle blocks rename.
        moved = database.with_suffix('.verified.sqlite')
        database.rename(moved)
        verification = create_engine('sqlite:///' + moved.as_posix())
        try:
            with verification.connect() as connection:
                if failure == 'unversioned':
                    assert connection.scalar(text('SELECT value FROM keep')) == 7
                else:
                    assert connection.scalar(text('SELECT id FROM users')) == 'existing'
        finally:
            verification.dispose()
    finally:
        for engine in engines:
            engine.dispose()
        await pool.close()
