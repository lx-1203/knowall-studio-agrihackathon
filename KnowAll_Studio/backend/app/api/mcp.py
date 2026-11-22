"""MCP API — endpoints for managing MCP servers and tools."""
import logging
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.database import get_db
from app.core.mcp import store
from app.core.mcp.store import MCPServerRequest

logger = logging.getLogger("knowall.api.mcp")
router = APIRouter(prefix="/mcp", tags=["mcp"])


class MCPToolCallRequest(BaseModel):
    server_name: str = Field(..., min_length=1, max_length=100)
    tool_name: str = Field(..., min_length=1, max_length=200)
    arguments: dict = Field(default_factory=dict)


@router.get("/servers")
async def list_mcp_servers(_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List all configured MCP servers (authenticated)."""
    return {"servers": await store.list_servers(db, _user.id)}


@router.post("/servers")
async def add_mcp_server(req: MCPServerRequest, _user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Add a new MCP server configuration (authenticated).

    Rejects SSRF-prone SSE URLs and empty commands so an unauthenticated or
    abused caller cannot register an arbitrary subprocess/HTTP target.
    """
    from app.core.mcp.client import MCPServerConfig, _validate_sse_url

    if req.transport == "stdio" and os.environ.get("KNOWALL_WORKSPACE_OWNER"):
        raise HTTPException(403, "Cloud workspaces support remote MCP servers only.")
    if req.transport == "stdio" and not req.command.strip():
        raise HTTPException(400, "stdio transport requires a command")
    if req.transport == "sse" and not req.url.strip():
        raise HTTPException(400, "sse transport requires a url")
    if req.transport == "sse":
        try:
            _validate_sse_url(req.url)
        except ValueError as e:
            raise HTTPException(400, str(e))

    config = MCPServerConfig(
        name=req.name,
        transport=req.transport,
        command=req.command,
        args=req.args,
        env=req.env,
        url=req.url,
        enabled=req.enabled,
    )
    await store.save_server(db, _user.id, config)
    return {"status": "ok", "server": config.to_dict()}


@router.delete("/servers/{name}")
async def remove_mcp_server(name: str, _user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Remove an MCP server (authenticated)."""
    await store.remove_server(db, _user.id, name)
    return {"status": "ok"}


@router.get("/servers/{name}/tools")
async def list_server_tools(name: str, _user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List available tools from an MCP server (authenticated)."""
    from app.core.mcp.client import MCPClient
    client = MCPClient()
    server = await store.get_server(db, _user.id, name)
    if server is not None:
        client.add_server(server)
    tools = await client.list_tools(name)
    return {"server": name, "tools": tools}


@router.post("/tools/call")
async def call_mcp_tool(req: MCPToolCallRequest, _user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Call a tool on an MCP server (authenticated)."""
    from app.core.mcp.client import MCPClient
    client = MCPClient()
    server = await store.get_server(db, _user.id, req.server_name)
    if server is not None:
        client.add_server(server)
    result = await client.call_tool(req.server_name, req.tool_name, req.arguments)
    return {"result": result}


@router.get("/tools/builtin")
async def list_builtin_tools(_user=Depends(get_current_user)):
    """List all built-in tools (including MCP-wrapped tools) (authenticated)."""
    from app.core.tools import tool_registry
    return {"tools": tool_registry.list_tools()}
