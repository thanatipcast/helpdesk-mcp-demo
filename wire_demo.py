"""
Peek at what the AI actually sees (no AI involved).

This script plays the role of the MCP *client* (the part inside Claude Code):
it starts server.py, asks for the tool list, then calls one tool, and prints
the JSON going back and forth.

    python wire_demo.py

Needs fake_api.py running in another terminal.
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).parent


def show(title: str, obj) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
    print(json.dumps(obj, indent=2, ensure_ascii=False))


async def main() -> None:
    server = StdioServerParameters(command=sys.executable, args=[str(HERE / "server.py")])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            wire = init.model_dump(mode="json", by_alias=True, exclude_none=True)
            show("1. Handshake: the server introduces itself", {
                "serverInfo": wire.get("serverInfo"),
                "protocolVersion": wire.get("protocolVersion"),
            })

            tools = (await session.list_tools()).tools
            wire_tools = [t.model_dump(mode="json", exclude_none=True, by_alias=True) for t in tools]
            if "--full" not in sys.argv:  # one tool in full, the rest as one-liners (pass --full for everything)
                wire_tools = [wire_tools[0]] + [
                    f"{t['name']}: {t['description'].splitlines()[0]}" for t in wire_tools[1:]
                ]
            show("2. tools/list: the 'manual' the AI reads before deciding what to call", wire_tools)

            request = {"name": "search_tickets", "arguments": {"sla_due_within_hours": 4}}
            show("3. tools/call: what the AI sends when you ask 'which tickets breach SLA soon?'", request)

            result = await session.call_tool(request["name"], request["arguments"])
            show("4. Result: what the server sends back (the AI turns this into an answer)",
                 result.model_dump(mode="json", by_alias=True, exclude_none=True).get("structuredContent"))


if __name__ == "__main__":
    asyncio.run(main())
