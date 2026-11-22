"""A native migration must not silence a running service's error reporting."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('logger_name', ['uvicorn.error', 'knowall.cloud'])
def test_migrations_preserve_service_error_output(tmp_path, logger_name):
    script = r'''
import io,json,logging,sys
from sqlalchemy import create_engine,text
from app.core.cloud_workspaces import initialize_schema
stream=io.StringIO()
logger=logging.getLogger(sys.argv[2])
logger.disabled=False
logger.setLevel(logging.ERROR)
logger.propagate=False
logger.addHandler(logging.StreamHandler(stream))
engine=create_engine('sqlite:///'+sys.argv[1])
try:
 with engine.begin() as connection:
  initialize_schema(connection)
  connection.execute(text("INSERT INTO documents (id,filename,file_type,sha256) VALUES ('kept','test.md','md','fake')"))
 logger.error('service-error-after-stamp')
 with engine.begin() as connection:
  initialize_schema(connection)
  assert connection.scalar(text('SELECT id FROM documents'))=='kept'
  revision=connection.scalar(text('SELECT version_num FROM alembic_version'))
 logger.error('service-error-after-upgrade')
 print(json.dumps({'enabled':not logger.disabled,'stamp_logged':'service-error-after-stamp' in stream.getvalue(),'upgrade_logged':'service-error-after-upgrade' in stream.getvalue(),'revision':revision,'data_preserved':True}))
finally:engine.dispose()
'''
    result = subprocess.run([sys.executable, '-c', script, (tmp_path/'learning.sqlite').as_posix(), logger_name],
                            cwd=Path(__file__).parents[1], env={**os.environ, 'PYTHONUTF8':'1'},
                            capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    outcome = json.loads(result.stdout.splitlines()[-1])
    assert outcome['data_preserved'] and outcome['revision']
    assert outcome['enabled'], 'Native schema migration disabled the service error logger'
    assert outcome['stamp_logged'] and outcome['upgrade_logged'], 'Native migrations swallowed service error output'
