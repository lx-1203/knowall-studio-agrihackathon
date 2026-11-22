"""Confirm the rejected locking-read design's InnoDB gap-lock failure."""
import asyncio
import json
from pathlib import Path
import os
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from app.models.kaf import KafFile
from tests.test_round46_mysql import mysql_publisher, private_kaf
from tests.test_round46_source_migration import row


async def test_rejected_locking_reads_really_deadlock(mysql_publisher):
    sessions, _, _ = mysql_publisher
    barrier = asyncio.Barrier(2)
    async def insert(identity):
        async with sessions() as db:
            await db.execute(select(KafFile).where(KafFile.source_doc_id==identity).limit(1).with_for_update())
            await barrier.wait()
            db.add(KafFile(**row(identity,identity)))
            try:
                await db.flush(); await db.commit()
                return {'identity':identity, 'result':'committed'}
            except OperationalError as error:
                code=error.orig.args[0]
                await db.rollback()
                return {'identity':identity, 'mysql_error':code}
    results=await asyncio.gather(insert('gap-a46'),insert('gap-z46'))
    assert sum(r.get('mysql_error')==1213 for r in results)==1
    assert sum(r.get('result')=='committed' for r in results)==1
    destination=Path(os.environ['KNOWALL_ROUND46_DIAGNOSTIC_OUTPUT'])
    destination.write_text(json.dumps({'real_mysql_gap_lock_diagnostic':results,
        'scope':'Rejected FOR UPDATE design only; current product uses a fresh non-locking read.'},indent=2),encoding='utf8')
