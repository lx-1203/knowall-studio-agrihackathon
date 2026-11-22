"""Account-scoped, once-per-Beijing-day check-ins with a monthly calendar."""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.utc_time import utc_iso
from app.database import get_db
from app.models.gamification import DailyCheckIn

router = APIRouter(prefix='/api/v1/check-ins', tags=['check-ins'])
BEIJING = timezone(timedelta(hours=8))
MONTH_PATTERN = r'^(19|20|21)\d{2}-(0[1-9]|1[0-2])$'


def current_instant():
    return datetime.now(timezone.utc)


class CheckInRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    note: str = Field(default='', max_length=300)


async def snapshot(db: AsyncSession, user_id: str, today: date, month: str | None = None):
    target = date.fromisoformat((month or today.strftime('%Y-%m')) + '-01')
    month_days = monthrange(target.year, target.month)[1]
    dates = set((await db.execute(select(DailyCheckIn.check_in_date).where(
        DailyCheckIn.user_id == user_id, DailyCheckIn.check_in_date <= today,
    ))).scalars().all())
    cursor = today if today in dates else today - timedelta(days=1)
    current_streak = 0
    while cursor in dates:
        current_streak += 1
        cursor -= timedelta(days=1)
    longest = run = 0
    previous = None
    for day in sorted(dates):
        run = run + 1 if previous is not None and day == previous + timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    rows = (await db.execute(select(DailyCheckIn).where(
        DailyCheckIn.user_id == user_id,
        DailyCheckIn.check_in_date >= target,
        DailyCheckIn.check_in_date <= target.replace(day=month_days),
        DailyCheckIn.check_in_date <= today,
    ).order_by(DailyCheckIn.check_in_date))).scalars().all()
    return {'today': today.isoformat(), 'timezone': 'Asia/Shanghai',
            'checked_in_today': today in dates, 'current_streak': current_streak,
            'longest_streak': longest, 'total_days': len(dates), 'month': target.strftime('%Y-%m'),
            'month_days': month_days,
            'records': [{'date': row.check_in_date.isoformat(), 'note': row.note,
                         'checked_at': utc_iso(row.created_at)} for row in rows]}


@router.get('')
async def get_check_ins(month: str | None = Query(None, pattern=MONTH_PATTERN),
                         db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    return await snapshot(db, user.id, current_instant().astimezone(BEIJING).date(), month)


@router.post('')
async def check_in(req: CheckInRequest, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    # Rollback expires ORM objects, including the authenticated user. Keep the
    # scalar ID before writing so duplicate requests never trigger implicit IO.
    user_id = user.id
    instant = current_instant()
    today = instant.astimezone(BEIJING).date()
    # The database uniqueness constraint arbitrates simultaneous taps/devices.
    # Do not update on conflict: an uncertain replay must preserve the first receipt.
    row = DailyCheckIn(user_id=user_id, check_in_date=today, note=req.note,
                       created_at=instant.astimezone(timezone.utc).replace(tzinfo=None))
    db.add(row)
    already = False
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(DailyCheckIn.id).where(
            DailyCheckIn.user_id == user_id, DailyCheckIn.check_in_date == today,
        ))).scalar_one_or_none()
        if existing is None:
            raise
        already = True
    return {**await snapshot(db, user_id, today), 'already_checked_in': already}
