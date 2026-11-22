"""Private MCP configuration in the existing encrypted key/value store.

Each server is one row: native database upserts preserve concurrent changes to
other servers. Tool execution always reads current committed configuration.
"""
import hashlib
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.crypto import encrypt_api_key, decrypt_api_key
from app.core.mcp.client import MCPServerConfig
from app.models import now
from app.models.system_config import SystemConfig


class MCPServerRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    transport: str = Field(default='stdio', pattern='^(stdio|sse)$')
    command: str = Field(default='', max_length=500)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = Field(default='', max_length=2000)
    enabled: bool = True


class _StoredServer(BaseModel):
    version: Literal[1]
    owner: str
    config: MCPServerRequest


def _prefix(user_id: str) -> str:
    return f'mcp:{user_id}:'


def _key(user_id: str, name: str) -> str:
    # User.id is String(36); 100-character Unicode names still fit key(128).
    return _prefix(user_id) + hashlib.sha256(name.encode('utf-8')).hexdigest()


def _decode(row: SystemConfig, user_id: str) -> MCPServerConfig:
    try:
        stored = _StoredServer.model_validate_json(decrypt_api_key(row.value))
        if stored.owner != user_id or row.key != _key(user_id, stored.config.name):
            raise ValueError('configuration identity mismatch')
        return MCPServerConfig(**stored.config.model_dump())
    except ValueError as exc:
        # Never expose encrypted values, credentials or raw validation input.
        raise HTTPException(503, 'MCP配置无法读取，请重新保存或删除该配置') from exc


async def list_servers(db: AsyncSession, user_id: str) -> list[dict]:
    rows = (await db.scalars(select(SystemConfig).where(
        SystemConfig.key.startswith(_prefix(user_id), autoescape=True)
    ).order_by(SystemConfig.key))).all()
    return await run_in_threadpool(lambda: [_decode(row, user_id).to_dict() for row in rows])


async def get_server(db: AsyncSession, user_id: str, name: str) -> MCPServerConfig | None:
    row = await db.get(SystemConfig, _key(user_id, name))
    return await run_in_threadpool(_decode, row, user_id) if row is not None else None


async def save_server(db: AsyncSession, user_id: str, config: MCPServerConfig) -> None:
    payload = _StoredServer(version=1, owner=user_id,
                            config=MCPServerRequest(**vars(config))).model_dump_json()
    encrypted = await run_in_threadpool(encrypt_api_key, payload)
    values = dict(key=_key(user_id, config.name), value=encrypted,
                  description='Private MCP server configuration v1', updated_at=now())
    dialect = db.get_bind().dialect.name
    if dialect in ('sqlite', 'postgresql'):
        if dialect == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        statement = insert(SystemConfig).values(**values)
        statement = statement.on_conflict_do_update(index_elements=['key'],
            set_={'value': statement.excluded.value, 'updated_at': statement.excluded.updated_at})
    elif dialect in ('mysql', 'mariadb'):
        from sqlalchemy.dialects.mysql import insert
        statement = insert(SystemConfig).values(**values)
        statement = statement.on_duplicate_key_update(value=statement.inserted.value,
                                                       updated_at=statement.inserted.updated_at)
    else:
        raise HTTPException(503, '当前数据库暂不支持MCP配置持久化')
    await db.execute(statement)
    await db.commit()


async def remove_server(db: AsyncSession, user_id: str, name: str) -> None:
    await db.execute(delete(SystemConfig).where(SystemConfig.key == _key(user_id, name)))
    await db.commit()
