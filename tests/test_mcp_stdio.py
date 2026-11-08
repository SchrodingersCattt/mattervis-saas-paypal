"""The same tool server is usable by an external MCP client over stdio."""

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from contest.mcp_server import decode_tool_result

ROOT = Path(__file__).resolve().parents[1]


def test_external_mcp_client_can_discover_and_inspect(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "contest.mcp_server", "--source", str(ROOT / "contest" / "static" / "disorder-example.cif"),
              "--workspace", str(tmp_path / "mcp")],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, cwd=str(ROOT),
    )

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = {tool.name for tool in (await client.list_tools()).tools}
                assert "resolve_disorder_paid" in tools and "inspect_structure" in tools
                result = decode_tool_result(await client.call_tool("inspect_structure", {}))
                assert result["summary"]["disorder"]["has_disorder"]

    asyncio.run(run())
