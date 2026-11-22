"""Real Alembic table rebuild and data-preserving source-width migration."""
import importlib.util
from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
from alembic import command
from app.models.kaf import KafFile

REVISION = Path(__file__).parents[1]/'alembic/versions/202610101748_widen_kaf_source_identity.py'


def migration(connection):
    spec = importlib.util.spec_from_file_location('round46_migration', REVISION)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def legacy(connection):
    metadata = sa.MetaData()
    table = sa.Table('kaf_files', metadata, *[
        column._copy() for column in KafFile.__table__.columns if column.name != 'source_claim'
    ])
    table.c.source_doc_id.type = sa.String(36)
    for index in list(table.indexes):
        if index.name == 'ix_kaf_files_source_doc_id': table.indexes.remove(index)
    table.create(connection)
    return table


def row(identity, source):
    return dict(id=identity, file_id='file-'+identity, title='旧中文资料', topic='测试',
                source_doc_id=source, storage_path='original/'+identity+'.kaf', sha256='a'*64)


def test_current_metadata_accepts_namespaced_uuid():
    assert KafFile.__table__.c.source_doc_id.type.length >= 44
    assert any(i.name == 'ix_kaf_files_source_doc_id' and not i.unique for i in KafFile.__table__.indexes)


@pytest.mark.parametrize('sources', [['same46','same46',None], ['summary:'+'a'*36, None, 'b'*36]])
def test_upgrade_preserves_duplicate_null_and_long_history(tmp_path, sources):
    engine = sa.create_engine('sqlite:///'+str(tmp_path/'legacy.db'))
    with engine.begin() as connection:
        table = legacy(connection)
        connection.execute(table.insert(), [row(str(i), source) for i,source in enumerate(sources)])
        before = connection.execute(sa.select(table).order_by(table.c.id)).all()
        migration(connection).upgrade()
        after = sa.Table('kaf_files', sa.MetaData(), autoload_with=connection)
        assert connection.execute(sa.select(after).order_by(after.c.id)).all() == before
        assert after.c.source_doc_id.type.length == 64
        indexes = {i['name']:i for i in sa.inspect(connection).get_indexes('kaf_files')}
        assert not indexes['ix_kaf_files_source_doc_id']['unique']
        assert indexes['ix_kaf_files_file_id']['unique']
        connection.execute(after.insert(), row('new', 'summary:'+'f'*36))
    engine.dispose()


def test_real_alembic_incremental_upgrade_from_existing_head(tmp_path):
    engine = sa.create_engine('sqlite:///'+str(tmp_path/'head.db'))
    config = Config()
    config.set_main_option('script_location', str(REVISION.parents[1]))
    with engine.begin() as connection:
        table = legacy(connection)
        connection.execute(table.insert(), [row('1','same46'), row('2','same46'), row('3',None)])
        config.attributes['connection'] = connection
        command.stamp(config, '202610101615')
        command.upgrade(config, '202610101748')
        assert connection.scalar(sa.text('SELECT version_num FROM alembic_version')) == '202610101748'
        assert sa.inspect(connection).get_columns('kaf_files')[6]['type'].length == 64
        assert connection.scalar(sa.text('SELECT COUNT(*) FROM kaf_files')) == 3
    engine.dispose()


@pytest.mark.parametrize('source', ['summary:'+'a'*36, 'x'*64])
def test_downgrade_refuses_loss_without_changing_table(tmp_path, source):
    engine = sa.create_engine('sqlite:///'+str(tmp_path/'guard.db'))
    with engine.begin() as connection:
        legacy(connection); module = migration(connection); module.upgrade()
        table = sa.Table('kaf_files', sa.MetaData(), autoload_with=connection)
        connection.execute(table.insert(), row('long', source))
        before = connection.execute(sa.select(table)).all()
        with pytest.raises(RuntimeError, match='36'): module.downgrade()
        assert connection.execute(sa.select(table)).all() == before
        assert sa.inspect(connection).get_columns('kaf_files')[6]['type'].length == 64
        assert any(i['name']=='ix_kaf_files_source_doc_id' for i in sa.inspect(connection).get_indexes('kaf_files'))
    engine.dispose()


def test_safe_short_downgrade_retains_rows_and_unique_file_index(tmp_path):
    engine = sa.create_engine('sqlite:///'+str(tmp_path/'short.db'))
    with engine.begin() as connection:
        table = legacy(connection)
        connection.execute(table.insert(), [row('1', 'a'*36), row('2', None), row('3','a'*36)])
        before = connection.execute(sa.select(table).order_by(table.c.id)).all()
        module = migration(connection); module.upgrade(); module.downgrade()
        after = sa.Table('kaf_files', sa.MetaData(), autoload_with=connection)
        assert after.c.source_doc_id.type.length == 36
        assert connection.execute(sa.select(after).order_by(after.c.id)).all() == before
        indexes = sa.inspect(connection).get_indexes('kaf_files')
        assert all(i['name']!='ix_kaf_files_source_doc_id' for i in indexes)
        assert any(i['name']=='ix_kaf_files_file_id' and i['unique'] for i in indexes)
    engine.dispose()
