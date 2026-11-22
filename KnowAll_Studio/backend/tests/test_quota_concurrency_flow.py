"""Deterministic actual SQLite concurrent quota reads and atomic accumulation."""
import asyncio
from datetime import date,timedelta

import pytest
from sqlalchemy import select,update
from sqlalchemy.sql.selectable import Select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import APIQuota
from app.core.api_scheduler import client
from tests.test_generation_cache_isolation_flow import cache_flow,rows
from tests.test_study_notification_flow import flow


def synchronize_two_quota_reads(monkeypatch):
    original=AsyncSession.execute;ready=asyncio.Event();count=0
    async def barrier(self,statement,*args,**kwargs):
        nonlocal count
        result=await original(self,statement,*args,**kwargs)
        if isinstance(statement,Select) and statement.column_descriptions[0].get('entity') is APIQuota:
            count+=1
            if count==2:ready.set()
            await asyncio.wait_for(ready.wait(),3)
        return result
    monkeypatch.setattr(AsyncSession,'execute',barrier)


async def test_first_concurrent_quota_creation_recovers_real_unique_conflict(cache_flow,monkeypatch):
    with monkeypatch.context() as scope:
        synchronize_two_quota_reads(scope)
        outcomes=await asyncio.wait_for(asyncio.gather(client.rate_limiter.check_quota('alice',10),
            client.rate_limiter.check_quota('alice',10),return_exceptions=True),5)
    assert outcomes==[(True,'ok'),(True,'ok')],outcomes
    assert len(await rows(cache_flow,APIQuota))==1


@pytest.mark.parametrize('previous_day',[False,True])
async def test_concurrent_usage_accumulates_without_lost_update(cache_flow,monkeypatch,previous_day):
    await client.rate_limiter.check_quota('alice',10)
    if previous_day:
        async with cache_flow.factory() as db:
            await db.execute(update(APIQuota).values(used_today=100,total_used=100,reset_at=date.today()-timedelta(days=1)))
            await db.commit()
    with monkeypatch.context() as scope:
        synchronize_two_quota_reads(scope)
        await asyncio.wait_for(asyncio.gather(client.rate_limiter.record_usage('alice',7,11),
                                             client.rate_limiter.record_usage('alice',7,11)),5)
    quota=(await rows(cache_flow,APIQuota))[0]
    assert quota.used_today==36
    assert quota.total_used==(136 if previous_day else 36)
    assert quota.reset_at==date.today()


async def test_usage_remains_scoped_to_actual_user(cache_flow):
    await client.rate_limiter.check_quota('alice',10)
    await client.rate_limiter.check_quota('bob',10)
    await asyncio.gather(client.rate_limiter.record_usage('alice',7,11),client.rate_limiter.record_usage('bob',7,11))
    assert {quota.user_id:quota.used_today for quota in await rows(cache_flow,APIQuota)}=={'alice':18,'bob':18}
