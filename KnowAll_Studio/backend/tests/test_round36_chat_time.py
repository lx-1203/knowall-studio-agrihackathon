"""Conversation metadata UTC via real read-only routes, no AI request."""
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import get_db
from app.models import Conversation, Message
from tests.test_product_auth import load_account_router


async def test_legacy_conversation_and_message_keep_stored_instant(tmp_path):
    router = load_account_router('chat').router
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "conversations.db"}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def db():
        async with sessions() as session:
            yield session
    app = FastAPI(); app.include_router(router); app.dependency_overrides[get_db] = db
    instant = datetime(2026, 10, 9, 19, 40)
    try:
        async with engine.begin() as connection:
            for model in (Conversation, Message):
                await connection.run_sync(model.__table__.create)
        async with sessions() as session:
            session.add(Conversation(id='conversation', title='旧UTC对话', role_preset='tutor', created_at=instant))
            await session.flush()
            session.add(Message(id='message', conversation_id='conversation', role='user', content='存储原文保持', created_at=instant))
            await session.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            listed = await client.get('/api/v1/chat/conversations'); assert listed.status_code == 200
            parsed = datetime.fromisoformat(listed.json()[0]['created_at']); assert parsed.tzinfo is not None
            assert parsed == instant.replace(tzinfo=timezone.utc)
            detail = await client.get('/api/v1/chat/conversations/conversation'); assert detail.status_code == 200
            message = detail.json()['messages'][0]; assert datetime.fromisoformat(message['created_at']) == parsed
            assert message['content'] == '存储原文保持'
            assert (await client.get('/api/v1/chat/conversations/unknown')).status_code == 404
        async with sessions() as session:
            assert (await session.get(Conversation, 'conversation')).created_at == instant
            assert (await session.get(Message, 'message')).created_at == instant
    finally:
        await engine.dispose()
