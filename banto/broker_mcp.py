"""MCP transport bridge. All operations execute in the common local broker."""
from __future__ import annotations

import asyncio
import functools
import sys

from mcp.server.fastmcp import FastMCP

from .broker import MCP_OPERATIONS
from .broker_client import BrokerClient


def bridge(function):
    @functools.wraps(function)
    async def forwarded(*args, **kwargs):
        import inspect
        arguments = inspect.signature(function).bind(*args, **kwargs)
        arguments.apply_defaults()
        return await asyncio.to_thread(BrokerClient().call, function.__name__, **arguments.arguments)
    return forwarded


def build_mcp() -> FastMCP:
    from . import mcp_server
    server = FastMCP("banto")
    for name in MCP_OPERATIONS:
        # Preserve each tool's existing annotations and schema.
        original = mcp_server.mcp._tool_manager.get_tool(name)
        server.add_tool(bridge(getattr(mcp_server, name)), annotations=original.annotations)

    @server.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def banto_api_request(provider: str, payload: dict) -> dict:
        """Call OpenAI Responses or Anthropic Messages without exposing the key.

        This incurs provider API usage. Supply the model explicitly. No arbitrary
        URLs, auth headers, streaming, remote tools, or secret-return operations.
        """
        return await asyncio.to_thread(BrokerClient().call, "api_request", provider=provider, payload=payload)

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def banto_broker_health() -> dict:
        """Check the common process without reading secrets or contacting providers."""
        return await asyncio.to_thread(BrokerClient().call, "health")

    return server


def main() -> None:
    # Only a local stdio bridge is exposed here. Remote HTTP ingress needs its
    # own authenticated gateway, not an unauthenticated loopback MCP endpoint.
    if sys.argv[1:] not in ([], ["--transport", "stdio"]):
        raise SystemExit("Broker MCP supports local stdio only. Use a separately authenticated gateway for remote clients.")
    build_mcp().run(transport="stdio")
