"""Apply and undo the new metadata migration against an old disposable schema."""
import importlib.util
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_metadata_migration_preserves_legacy_rows_and_downgrades():
    path = Path(__file__).parents[1] / 'alembic/versions/202610090423_add_fsrs_library_metadata.py'
    assert path.is_file(), 'FSRS metadata migration is required for old databases'
    spec = importlib.util.spec_from_file_location('fsrs_metadata_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE review_schedule (id TEXT PRIMARY KEY, fsrs_difficulty FLOAT, review_count INTEGER)'))
        conn.execute(text('CREATE TABLE recitation_records (id TEXT PRIMARY KEY, created_at DATETIME)'))
        conn.execute(text("INSERT INTO review_schedule VALUES ('old', 0.5, 7)"))
        conn.execute(text("INSERT INTO recitation_records VALUES ('old', '2020-01-01 00:00:00')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert conn.execute(text('SELECT fsrs_difficulty, review_count, fsrs_version FROM review_schedule')).one() == (0.5, 7, None)
            assert conn.execute(text('SELECT created_at, last_review_at, fsrs_version FROM recitation_records')).one() == ('2020-01-01 00:00:00', None, None)
            migration.downgrade()
            assert 'fsrs_version' not in {c['name'] for c in inspect(conn).get_columns('review_schedule')}
            assert conn.scalar(text('SELECT review_count FROM review_schedule')) == 7
    engine.dispose()


def test_cloud_workspace_upgrades_previous_head_without_replacing_cards():
    from app.core.cloud_workspaces import initialize_schema
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    root = Path(__file__).parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    head = ScriptDirectory.from_config(config).get_current_head()
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        initialize_schema(conn)
        # Reconstruct the previous deployed schema, using only temporary data.
        conn.execute(text('ALTER TABLE review_schedule DROP COLUMN fsrs_version'))
        conn.execute(text('ALTER TABLE recitation_records DROP COLUMN fsrs_version'))
        conn.execute(text('ALTER TABLE recitation_records DROP COLUMN last_review_at'))
        conn.execute(text("UPDATE alembic_version SET version_num='f1a5c9d3e7b2'"))
        conn.execute(text("INSERT INTO flashcards (id, card_type, front, back) VALUES ('keep', 'basic', 'Q', 'A')"))
        conn.execute(text("INSERT INTO review_schedule (id, card_id, fsrs_stability, fsrs_difficulty, review_count, state) VALUES ('keep', 'keep', 5, 0.5, 7, 'review')"))
        initialize_schema(conn)
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == head
        assert conn.scalar(text('SELECT front FROM flashcards')) == 'Q'
        assert conn.execute(text('SELECT fsrs_difficulty, review_count, fsrs_version FROM review_schedule')).one() == (0.5, 7, None)
    engine.dispose()
