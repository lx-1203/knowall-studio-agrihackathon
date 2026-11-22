"""Native durable identity, migration and real JWT request contracts."""
import asyncio, importlib.util, os
from pathlib import Path
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.exc import IntegrityError
from alembic.config import Config
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core import kaf_service, kaf_publisher
from app.models.kaf import KafFile
from tests.test_round47_mysql import mysql47, private_kaf
from tests.test_round44_auto_publish import publisher, account_app
from tests.test_round43_kaf_recovery import real_kaf_http
from tests.test_round46_source_migration import row

REVISION=Path(__file__).parents[1]/'alembic/versions/202610101854_claim_kaf_source.py'

def module(connection):
    spec=importlib.util.spec_from_file_location('round48_migration',REVISION)
    loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded)
    loaded.op=Operations(MigrationContext.configure(connection));return loaded

def old_table(connection, collation=None):
    # Actual pre48 schema, independent of changing current ORM metadata.
    metadata=sa.MetaData()
    table=sa.Table('kaf_files',metadata,*[c._copy() for c in KafFile.__table__.columns if c.name!='source_claim'])
    table.c.source_doc_id.type=sa.String(64,collation=collation)
    table.create(connection);return table

def check_migration(connection, collation=None, real_command=False):
    table=old_table(connection,collation)
    sources=['same48','same48',None,None,'summary:'+'a'*36,'x'*64,'']
    if collation:sources+=['Résumé48','resume48']
    connection.execute(table.insert(),[row(str(i),source) for i,source in enumerate(sources)])
    columns=[c.name for c in table.c]
    before=connection.execute(sa.select(table).order_by(table.c.id)).all()
    if real_command:
        config=Config();config.set_main_option('script_location',str(REVISION.parents[1]))
        config.attributes['connection']=connection
        command.stamp(config,'202610101748');command.upgrade(config,'202610101854')
        assert connection.scalar(sa.text('SELECT version_num FROM alembic_version'))=='202610101854'
    else:module(connection).upgrade()
    new=sa.Table('kaf_files',sa.MetaData(),autoload_with=connection)
    assert connection.execute(sa.select(*[new.c[c] for c in columns]).order_by(new.c.id)).all()==before
    claims=connection.execute(sa.select(new.c.source_claim).where(new.c.source_claim.is_not(None))).scalars().all()
    assert len(claims)==(5 if collation=='utf8mb4_bin' else 4 if collation else 3)
    assert new.c.source_claim.type.length==64
    if collation:assert new.c.source_claim.type.collation==collation
    assert any(c['name']=='uq_kaf_files_source_claim' for c in sa.inspect(connection).get_unique_constraints('kaf_files'))
    # Reject duplicates in the database, with no publisher or named lock involved.
    with pytest.raises(IntegrityError):
        with connection.begin_nested():connection.execute(new.insert(),dict(row('dupe','same48'),source_claim='same48'))
    if collation:
        if collation=='utf8mb4_0900_ai_ci':
            with pytest.raises(IntegrityError):
                with connection.begin_nested():connection.execute(new.insert(),dict(row('accent-dupe','RESUME48'),source_claim='RESUME48'))
        else:
            connection.execute(new.insert(),dict(row('accent-new','RESUME48'),source_claim='RESUME48'))
            connection.execute(new.delete().where(new.c.id=='accent-new'))
    connection.execute(new.insert(),[dict(row('null-new',None),source_claim=None),dict(row('distinct','new48'),source_claim='new48')])
    connection.execute(new.delete().where(new.c.id.in_(['null-new','distinct'])))
    if real_command:command.downgrade(config,'202610101748')
    else:module(connection).downgrade()
    restored=sa.Table('kaf_files',sa.MetaData(),autoload_with=connection)
    assert 'source_claim' not in restored.c
    assert connection.execute(sa.select(restored).order_by(restored.c.id)).all()==before
    restored.drop(connection)
    if real_command:sa.Table('alembic_version',sa.MetaData(),autoload_with=connection).drop(connection)

@pytest.mark.parametrize('real_command',[False,True])
def test_sqlite_migration_preserves_history_and_downgrade(tmp_path,real_command):
    engine=sa.create_engine('sqlite:///'+str(tmp_path/'legacy48.db'))
    try:
        with engine.begin() as conn:check_migration(conn,real_command=real_command)
    finally:engine.dispose()

@pytest.mark.parametrize('collation',['utf8mb4_0900_ai_ci','utf8mb4_bin'])
async def test_mysql_migration_native_collation_and_history(collation):
    url=os.environ['KNOWALL_ROUND48_MYSQL_URL'];assert 'round48_private' in url and '127.0.0.1' in url
    engine=create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text('DROP TABLE IF EXISTS kaf_files'))
            await conn.execute(sa.text('DROP TABLE IF EXISTS alembic_version'))
            await conn.run_sync(lambda sync:check_migration(sync,collation,True))
    finally:await engine.dispose()

@pytest.mark.parametrize('kind',['document','summary'])
async def test_two_uncoordinated_mysql_registers_have_one_winner(mysql47,kind):
    engine,sessions,service,directory,_=mysql47
    source='summary:'+'b'*36 if kind=='summary' else 'b'*36
    def build(text):
        if kind=='document':return service.publish_from_markdown(text,'test48',source_doc_id=source)
        return service.publish_from_package({'chapters':[{'body':text}]},{'topic':'test48','title':text,'source_doc_id':source})
    files=await asyncio.gather(asyncio.to_thread(build,'# A'),asyncio.to_thread(build,'# B'))
    async def register(kf):
        async with sessions() as db:
            try:await kaf_service.register_publication(db,kf);return 'winner'
            except RuntimeError as exc:
                assert exc.__class__.__name__=='KAFSourceConflictError'
                assert db.is_active
                assert await db.scalar(sa.select(sa.func.count()).select_from(KafFile))==1
                return 'conflict'
    results=await asyncio.gather(*[register(kf) for kf in files])
    assert sorted(results)==['conflict','winner']
    assert len(list(directory.glob('*.kaf')))==1
    async with sessions() as db:
        saved=(await db.scalars(sa.select(KafFile))).one()
        assert saved.source_claim==source

async def test_mysql_old_snapshot_conflict_uses_current_read(mysql47):
    _,sessions,service,directory,_=mysql47
    async with sessions() as old:
        assert await old.scalar(sa.select(sa.func.count()).select_from(KafFile))==0
        async with sessions() as fresh:
            await kaf_service.register_publication(fresh,service.publish_from_markdown('# first','test48',source_doc_id='old48'))
        assert await old.scalar(sa.select(sa.func.count()).select_from(KafFile))==0
        with pytest.raises(RuntimeError,match='来源'):
            await kaf_service.register_publication(old,service.publish_from_markdown('# second','test48',source_doc_id='old48'))
        assert old.is_active
        assert len(list(directory.glob('*.kaf')))==1

@pytest.mark.parametrize('source',[None,''])
async def test_anonymous_sources_allow_multiple_native_registers(mysql47,source):
    _,sessions,service,directory,_=mysql47
    async with sessions() as db:
        for text in ['# A','# B']:
            await kaf_service.register_publication(db,service.publish_from_markdown(text,'test48',source_doc_id=source))
        assert await db.scalar(sa.select(sa.func.count()).select_from(KafFile))==2
    assert len(list(directory.glob('*.kaf')))==2

async def test_other_native_integrity_errors_are_not_source_conflict(publisher):
    app,service,directory=publisher
    async with app.sessions() as db:
        await kaf_service.register_publication(db,service.publish_from_markdown('# existing','test48',source_doc_id='existing48'))
        bad=service.publish_from_markdown('# bad','test48',source_doc_id='existing48');bad.title=None
        with pytest.raises(IntegrityError):await kaf_service.register_publication(db,bad)
        assert db.is_active
        assert len(list(directory.glob('*.kaf')))==1

@pytest.mark.parametrize('kind',['document','summary'])
async def test_optional_helper_survives_competing_direct_registration(mysql47,kind,monkeypatch):
    _,sessions,service,directory,_=mysql47
    original=kaf_service.register_publication
    source='race48' if kind=='document' else 'summary:race48'
    async def competing(db,kf):
        async with sessions() as other:
            await original(other,service.publish_from_markdown('# competitor','test48',source_doc_id=source))
        await original(db,kf)
    with monkeypatch.context() as patch:
        patch.setattr(kaf_service,'register_publication',competing)
        async with sessions() as db:
            if kind=='document':result=await kaf_publisher.publish_document(db,'race48','a.md',markdown='# candidate')
            else:result=await kaf_publisher.publish_summary_package(db,'race48','title','topic',[{'title':'chapter','body':'text'}])
            assert result is None and db.is_active
    assert len(list(directory.glob('*.kaf')))==1

async def test_other_file_id_unique_violation_stays_integrity_error(mysql47):
    _,sessions,service,directory,_=mysql47
    async with sessions() as db:
        db.add(KafFile(file_id='occupied48',title='historical',topic='test48',storage_path='historical/occupied48.kaf',sha256='a'*64))
        await db.commit()
        rejected=service.publish_from_markdown('# new','test48',file_id='occupied48',source_doc_id='new48')
        with pytest.raises(IntegrityError):await kaf_service.register_publication(db,rejected)
        assert db.is_active and not list(directory.glob('*.kaf'))

async def test_mysql_interrupted_ddl_can_resume_without_history_loss():
    url=os.environ['KNOWALL_ROUND48_MYSQL_URL'];assert 'round48_private' in url and '127.0.0.1' in url
    engine=create_async_engine(url)
    try:
        async with engine.begin() as conn:
            def seed(sync):
                table=old_table(sync)
                sync.execute(table.insert(),[row('a','interrupted48'),row('b','interrupted48')])
            await conn.run_sync(seed)
        def interrupted(sync):
            migration=module(sync)
            def fault(*a,**kw):raise RuntimeError('test48-after-native-column-DDL')
            migration.op.batch_alter_table=fault
            migration.upgrade()
        with pytest.raises(RuntimeError,match='after-native-column'):
            async with engine.begin() as conn:await conn.run_sync(interrupted)
        async with engine.begin() as conn:
            def resume(sync):
                assert 'source_claim' in {c['name'] for c in sa.inspect(sync).get_columns('kaf_files')}
                module(sync).upgrade()
                # Also recover an applied DDL whose Alembic stamp was interrupted.
                module(sync).upgrade()
                table=sa.Table('kaf_files',sa.MetaData(),autoload_with=sync)
                assert sync.scalar(sa.select(sa.func.count()).select_from(table))==2
                assert sync.scalar(sa.select(sa.func.count(table.c.source_claim)))==1
                table.drop(sync)
            await conn.run_sync(resume)
    finally:await engine.dispose()

async def test_admin_valid_max_lengths_and_unknown_fields_remain_compatible(real_kaf_http):
    _,client,headers,*_=real_kaf_http
    response=await client.post('/api/v1/kaf/admin/publish',headers=headers,json={
        'markdown':'# limits48','title':'t'*255,'topic':'t'*64,'source_doc_id':'s'*64,
        'source_doc_filename':'f'*500,'enabled_tiers':'t'*200,'legacy_extra':'ignored'})
    assert response.status_code==201,response.text

async def test_nonadmin_invalid_publish_remains_forbidden(real_kaf_http):
    from app.models.user import User
    app,client,headers,*_=real_kaf_http
    async with app.sessions() as db:
        user=await db.scalar(sa.select(User));user.is_admin=False;await db.commit()
    response=await client.post('/api/v1/kaf/admin/publish',headers=headers,json={'markdown':[]})
    assert response.status_code==403,response.text

async def test_admin_source_conflict_preserves_original_and_returns409(real_kaf_http):
    app,client,headers,doc,_,service=real_kaf_http
    payload={'markdown':'# A','source_doc_id':'http48','title':'first48'}
    first=await client.post('/api/v1/kaf/admin/publish',headers=headers,json=payload)
    assert first.status_code==201,first.text
    directory=Path(doc.storage_path).parent
    before={p:p.read_bytes() for p in directory.glob('*.kaf')}
    second=await client.post('/api/v1/kaf/admin/publish',headers=headers,json={**payload,'markdown':'# B','title':'second48'})
    assert second.status_code==409,second.text
    assert {p:p.read_bytes() for p in directory.glob('*.kaf')}==before
    async with app.sessions() as db:
        saved=await db.scalar(sa.select(KafFile).where(KafFile.source_doc_id=='http48'))
        assert saved.title=='first48' and saved.source_claim=='http48'

@pytest.mark.parametrize('field,value',[('markdown',[]),('markdown',123),('title','t'*256),('title',None),('topic','t'*65),('source_doc_id','s'*65),('source_doc_filename','f'*501),('enabled_tiers','t'*201)])
async def test_admin_bad_payload_native422_before_file_build(real_kaf_http,field,value,monkeypatch):
    _,client,headers,doc,_,service=real_kaf_http
    def forbidden(*a,**kw):raise AssertionError('Invalid request reached file builder')
    monkeypatch.setattr(service,'publish_from_markdown',forbidden)
    response=await client.post('/api/v1/kaf/admin/publish',headers=headers,json={'markdown':'# valid',field:value})
    assert response.status_code==422,response.text
    assert response.json()['detail'][0]['loc']==['body',field]

@pytest.mark.parametrize('markdown',['','   ',None])
async def test_admin_blank_markdown_compatibility(real_kaf_http,markdown):
    _,client,headers,*_=real_kaf_http
    response=await client.post('/api/v1/kaf/admin/publish',headers=headers,json={'markdown':markdown})
    assert response.status_code==400,response.text
