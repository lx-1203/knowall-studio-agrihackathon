"""The actual main registration must expose authenticated MCP through /api.

Evaluate only MCP registration expressions from main, avoiding startup/user data.
Handlers are real; registries use harmless fixture data. Final native test covers
the complete cloud/worker bootstrap separately.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import sys

import httpx
import pytest
from fastapi import FastAPI
from app.core.auth import get_current_user
from app.database import get_db
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models.system_config import SystemConfig
from tests.test_product_auth import load_account_router

mcp = load_account_router('mcp')


@pytest.fixture
async def mcp_client(monkeypatch, tmp_path):
    app = FastAPI()
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "mcp.db"}')
    sessions = async_sessionmaker(engine)
    async with engine.begin() as connection:
        await connection.run_sync(SystemConfig.__table__.create)
    async def no_database():
        async with sessions() as db:
            yield db  # Private disposable database, never open user DBs.
    app.dependency_overrides[get_db] = no_database
    tree = ast.parse((Path(__file__).parents[1]/'app/main.py').read_text(encoding='utf-8-sig'))
    for statement in tree.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if (isinstance(call.func, ast.Attribute) and call.func.attr == 'include_router'
                    and call.args and isinstance(call.args[0], ast.Name) and call.args[0].id == 'mcp_router'):
                exec(compile(ast.Module(body=[statement],type_ignores=[]),'<actual main MCP registration>','exec'),{'app':app,'mcp_router':mcp.router})
    monkeypatch.setitem(sys.modules,'app.core.tools',SimpleNamespace(tool_registry=SimpleNamespace(list_tools=lambda:[{'name':'fixture-tool'}])))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        yield client,app
    await engine.dispose()


@pytest.mark.parametrize('prefix',['/api/v1/mcp','/mcp'])
@pytest.mark.parametrize('suffix',['/servers','/tools/builtin'])
async def test_standard_and_legacy_reads_share_real_handlers(mcp_client,prefix,suffix):
    client,app=mcp_client
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id='isolated')
    response=await client.get(prefix+suffix)
    assert response.status_code==200,response.text
    expected={'servers':[]} if suffix=='/servers' else {'tools':[{'name':'fixture-tool'}]}
    assert response.json()==expected


@pytest.mark.parametrize('prefix',['/api/v1/mcp','/mcp'])
@pytest.mark.parametrize('suffix',['/servers','/tools/builtin'])
async def test_both_namespaces_reject_anonymous(mcp_client,prefix,suffix):
    client,_=mcp_client
    response=await client.get(prefix+suffix)
    assert response.status_code==401,response.text
