"""Identity policy for private research/book jobs, retaining anonymous local mode."""
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import get_db
from app.core.auth import get_current_user, security


async def generation_user(request: Request, credentials=Depends(security), db: AsyncSession=Depends(get_db)):
    if not request.headers.get('authorization') and credentials is None and not settings.desktop_require_login:
        return None
    return await get_current_user(credentials, db)
