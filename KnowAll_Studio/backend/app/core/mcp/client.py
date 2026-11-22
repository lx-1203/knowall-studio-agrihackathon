"""MCP Client — manages connections to external MCP servers."""
from __future__ import annotations

import asyncio
import json
import ipaddress
import logging
import os
import socket
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("knowall.mcp.client")


def _is_sensitive_env_key(key: str) -> bool:
    """True if an env var key looks like a credential and should be masked."""
    k = key.lower()
    return (
        k.endswith("key")
        or k.endswith("secret")
        or k.endswith("token")
        or k.endswith("password")
        or "api_key" in k
        or "access_key" in k
        or "auth" in k
        or "credential" in k
    )


def _validate_sse_url(url: str) -> None:
    """Reject SSRF-prone MCP server URLs.

    Only http/https is allowed, and the target host must not resolve to a
    private, loopback, link-local, or unspecified IP (cloud metadata etc.).
    Raises ValueError on refusal.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported MCP URL scheme: {parsed.scheme or '(none)'}. Only http/https allowed.")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("MCP URL must include a host.")

    # Resolve all addresses; if any is private/loopback, refuse (this catches
    # both literal metadata IPs and DNS names that resolve internally).
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"Cannot resolve MCP host '{host}': {e}") from e

    for info in infos:
        ip = info[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_unspecified:
            raise ValueError(f"MCP host '{host}' ({ip}) is not reachable (internal address).")


class MCPServerConfig:
    """Configuration for an MCP server connection."""

    def __init__(
        self,
        name: str,
        transport: str = "stdio",  # "stdio" or "sse"
        command: str = "",
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str = "",
        enabled: bool = True,
    ):
        self.name = name
        self.transport = transport
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.url = url
        self.enabled = enabled

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": self.args,
            "env": {k: ("***" if _is_sensitive_env_key(k) else v)
                    for k, v in self.env.items()},
            "url": self.url,
            "enabled": self.enabled,
        }


class MCPClient:
    """Manages connections to MCP servers and dispatches tool calls."""

    def __init__(self):
        self._servers: dict[str, MCPServerConfig] = {}
        self._connections: dict[str, Any] = {}

    def add_server(self, config: MCPServerConfig):
        """Add an MCP server configuration."""
        self._servers[config.name] = config
        logger.info("MCP server added: %s (%s)", config.name, config.transport)

    def remove_server(self, name: str):
        """Remove an MCP server."""
        self._servers.pop(name, None)
        self._connections.pop(name, None)
        logger.info("MCP server removed: %s", name)

    def list_servers(self) -> list[dict]:
        """List all configured MCP servers."""
        return [s.to_dict() for s in self._servers.values()]

    def get_server(self, name: str) -> MCPServerConfig | None:
        return self._servers.get(name)

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        """Call a tool on a specific MCP server."""
        server = self._servers.get(server_name)
        if not server:
            return {"error": f"MCP server not found: {server_name}"}
        if not server.enabled:
            return {"error": f"MCP server disabled: {server_name}"}

        try:
            if server.transport == "stdio":
                return await self._call_stdio(server, tool_name, arguments)
            elif server.transport == "sse":
                return await self._call_sse(server, tool_name, arguments)
            else:
                return {"error": f"Unknown transport: {server.transport}"}
        except Exception as e:
            logger.error("MCP call failed: %s/%s — %s", server_name, tool_name, e)
            return {"error": str(e)}

    async def list_tools(self, server_name: str) -> list[dict]:
        """List available tools from an MCP server."""
        server = self._servers.get(server_name)
        if not server or not server.enabled:
            return []
        try:
            if server.transport == "stdio":
                return await self._list_tools_stdio(server)
            return []
        except Exception as e:
            logger.error("MCP list_tools failed: %s — %s", server_name, e)
            return []

    async def _call_stdio(self, server: MCPServerConfig, tool_name: str, arguments: dict) -> dict:
        """Call tool via stdio transport (JSON-RPC over stdin/stdout)."""
        if os.environ.get("KNOWALL_WORKSPACE_OWNER"):
            raise PermissionError("Cloud workspaces support remote MCP servers only.")
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

        proc = await asyncio.create_subprocess_exec(
            server.command, *server.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**server.env} if server.env else None,
        )

        stdin_data = json.dumps(request) + "\n"
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode()),
                timeout=30,
            )
        except asyncio.TimeoutError:
            # Terminate the hung child process so it doesn't accumulate as an orphan.
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return {"error": "MCP stdio call timed out"}

        if stderr:
            logger.debug("MCP stderr: %s", stderr.decode()[:200])

        try:
            response = json.loads(stdout.decode().strip().split("\n")[-1])
            return response.get("result", {"raw": stdout.decode()})
        except json.JSONDecodeError:
            return {"output": stdout.decode().strip()}

    async def _list_tools_stdio(self, server: MCPServerConfig) -> list[dict]:
        """List tools via stdio transport."""
        if os.environ.get("KNOWALL_WORKSPACE_OWNER"):
            raise PermissionError("Cloud workspaces support remote MCP servers only.")
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }

        proc = await asyncio.create_subprocess_exec(
            server.command, *server.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdin_data = json.dumps(request) + "\n"
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode()),
                timeout=10,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return []

        try:
            response = json.loads(stdout.decode().strip().split("\n")[-1])
            return response.get("result", {}).get("tools", [])
        except (json.JSONDecodeError, AttributeError):
            return []

    async def _call_sse(self, server: MCPServerConfig, tool_name: str, arguments: dict) -> dict:
        """Call tool via SSE transport (HTTP POST)."""
        import httpx

        # SSRF guard: refuse private/loopback/internal targets before POSTing.
        _validate_sse_url(server.url)

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                server.url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
            )
            resp.raise_for_status()
            return resp.json().get("result", {})


# Module singleton
mcp_client = MCPClient()
