# Helpdesk MCP demo — context for Claude

Personal learning / POC project: shows how an **MCP server** lets AI agents work with a helpdesk
issue board (tickets). Everything runs locally with fake data; there is no real Helpdesk backend here.
The README has the full walkthrough and a presentation script. The fake LLM replies in English or Thai (it follows the language of the question); sample tickets are in Thai.

## The pieces
- `fake_api.py` + `board.html` — pretend Helpdesk backend: in-memory tickets behind a REST API, a live
  board page at http://127.0.0.1:8765, an activity panel listing every API call per client, and the
  `/api/agent/*` routes for the AI helper chat box. `HELPDESK_PORT` env changes the port.
- `server.py` — **the MCP server** (official Python SDK v2). 5 tools: `search_tickets`, `get_ticket`
  (read-only), `create_ticket`, `update_ticket`, `add_comment`. Each just calls the REST API.
  stdio by default, `--http` = Streamable HTTP on :8000/mcp (no auth yet). Re-runs itself inside
  `.venv` when started with a system Python, so `.mcp.json` (`python3 server.py`) works on Windows and macOS.
- `agent.py` — the "agent service" behind the AI helper chat box: MCP client + agent loop + human
  approval (any tool without `readOnlyHint=True` needs Confirm). **The LLM is fake** (`FakeLLM`, keyword
  rules, no API key). Swapping in a real model = replace `FakeLLM.next_step()` only. Reconnects
  automatically when `server.py` changes.
- `wire_demo.py` — acts as an MCP client with no AI and prints the raw JSON (handshake, tools/list,
  tools/call, result). `--full` prints every tool schema.
- `.mcp.json` — registers the server for Claude Code in this folder (approve it on first run).

## Run
```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt        # Windows: .venv\Scripts\python
.venv/bin/python fake_api.py                               # then open http://127.0.0.1:8765
claude                                                     # in this folder, second terminal
```

## Gotchas learned the hard way
- MCP Python SDK **2.x**: `FastMCP` was renamed `MCPServer` (`from mcp.server.mcpserver import MCPServer`);
  most examples online are v1. `httpx` is no longer pulled in by `mcp` — it's in requirements.txt.
- Raise `mcp.server.mcpserver.exceptions.ToolError` for errors the model should read; any other
  exception reaches the client only as a generic "Error executing tool …".
- Keep the SDK's async contexts (`stdio_client`, `ClientSession`) opened and closed in the same task
  (see `Agent._hold_connection`).
- Human approval in this demo is client-side only (AI helper card / Claude Code prompt). The server does
  not enforce it — a real system must (pending action confirmed by the user's own session).

## MCP spec facts used (checked 2026-11-24)
- Current spec version is 2026-07-28: stateless (no `initialize`, no `Mcp-Session-Id`), `server/discover`,
  Sampling/Roots/HTTP+SSE deprecated. SDK 2.3 supports it; the demo client still uses the older
  initialize handshake (2025-11-25).
- Remote (HTTP) servers: OAuth 2.1 resource server, RFC 9728 metadata, validate token audience,
  never pass the client's token through to upstream APIs. stdio servers take credentials from env.
- Tool annotations are hints and untrusted unless the server is trusted.

## Ideas for next steps
- Project field + per-project permission on tickets; optimistic locking (`expected_version`).
- Server-enforced approval: write tools create a pending action; only the user's Confirm commits it.
- OAuth on the `--http` server; audit log of agent actions.
- Real LLM in `agent.py` (needs an API key in an env var, never in files).
- A "Backend Agent" that reacts to new tickets (suggest category / assignee).
