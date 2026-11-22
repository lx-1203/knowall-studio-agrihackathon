"""Targeted migration against isolated SQLite; no existing production database touched."""
import importlib.util
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

def test_material_state_upgrade_downgrade_preserves_unrelated_data():
    path=Path(__file__).parents[1]/'alembic/versions/202610122200_add_material_states.py'
    spec=importlib.util.spec_from_file_location('material_state_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE keep_data (value TEXT NOT NULL)'))
        connection.execute(text("INSERT INTO keep_data VALUES ('old local data')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            assert set(inspect(connection).get_pk_constraint('material_states')['constrained_columns'])=={'user_id','document_id','kind'}
            assert set(inspect(connection).get_pk_constraint('material_state_operations')['constrained_columns'])=={'user_id','document_id','kind','operation_id'}
            module.downgrade()
            assert inspect(connection).get_table_names()==['keep_data']
            assert connection.scalar(text('SELECT value FROM keep_data'))=='old local data'
    engine.dispose()
