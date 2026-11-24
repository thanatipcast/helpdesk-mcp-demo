# Helpdesk MCP — demo prototype

A small, runnable demo of what an **MCP server** really looks like. AI agents search, read, create and
update tickets on a helpdesk issue board through MCP, and you watch the board change live.

> Everything is fake and in memory: stop the program and the data is gone. The sample tickets are in Thai.

## What's inside

```
 MCP clients (have the AI)               MCP server                Helpdesk backend (fake)
 ┌───────────────────────────────┐
 │ A) AI helper chat box on board │──┐
 │    → agent.py (fake LLM)       │  ├─MCP──►  server.py  ──REST──►  fake_api.py  ──►  board (http://127.0.0.1:8765)
 │ B) Claude Code                 │──┘        ▲ one server, works with every client
 └───────────────────────────────┘
```

| File | What it is |
|---|---|
| `server.py` | **The MCP server** (~130 lines). Declares 5 tools: `search_tickets`, `get_ticket`, `create_ticket`, `update_ticket`, `add_comment`. Each one just calls the REST API. There is no AI inside. |
| `agent.py` | The AI helper's **agent service** (the part a real backend would have): MCP client + agent loop + human approval. **The LLM is fake** (keyword rules, no API key needed); everything else is real MCP. |
| `fake_api.py` + `board.html` | The fake helpdesk: a REST API, a live board page, the AI helper chat box, and a log of which client made which API call. |
| `wire_demo.py` | Acts as an MCP client itself (no AI) and prints the JSON going back and forth, so you can see what the AI "sees". |
| `.mcp.json` | Tells Claude Code that this folder has an MCP server named `helpdesk`. |

## Setup (once)

You need Python 3.11+ and (optionally) [Claude Code](https://claude.com/claude-code).

**macOS / Linux**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

**Windows (PowerShell)** — in the commands below, use `.venv\Scripts\python` instead of `.venv/bin/python`.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

`.mcp.json` runs `python3 server.py` on both systems; `server.py` switches into `.venv` by itself.
Run every command from the repo folder.

## Run the demo

**Terminal 1 — start the fake helpdesk**

```bash
.venv/bin/python fake_api.py
```

Open http://127.0.0.1:8765 in a browser (put it next to Claude Code when presenting).

## The AI helper chat box ("an admin types requests into the app")

The **AI helper** box on the right of the board is an AI built into the app: the admin types a request →
the agent service (`agent.py`) talks to the MCP server → the board changes. No Claude Code or API key needed.

- Click an example chip or type your own (English or Thai).
- The chat shows every step: `LLM decides to call …` (the LLM picks a tool) → `MCP …` (the `tools/call`
  result) → the answer.
- Any tool **not** marked `readOnlyHint=True` (create / update / comment) shows a **Confirm / Cancel** card
  first. The agent decides this from the annotations the MCP server sends in `tools/list`: that is the
  human approval.
- `Check stock for product A` → it can't, because the MCP server has no such tool (you'd connect the stock
  system's MCP server too).
- The log underneath shows which calls came from `AI helper (MCP)` and which from `Claude Code (MCP)`,
  even though both use the same `server.py`.
- Edit `server.py` and send a new message: the agent reconnects and reloads the tools by itself
  (no need to restart `fake_api.py`).

Example requests the fake LLM understands:

| Type this | What happens |
|---|---|
| `Which tickets will breach SLA within 4 hours?` | `search_tickets(sla_due_within_hours=4)` |
| `Show me ticket 1003` | `get_ticket` with the comment history |
| `Assign 1002 to pimchanok and comment: picked up, will call the customer today` | `update_ticket` then `add_comment`, each with a Confirm card |
| `Close ticket 1006` | `add_comment` then `update_ticket(status=resolved)`: the tool description says to comment before resolving |
| `Create ticket: customer paid by QR but the app froze, priority high, via LINE` | `create_ticket`; the SLA comes from the priority |
| `How many tickets are in each status?` | `search_tickets` and a count per status |
| `Check stock for product A` | No matching tool, so it explains why it can't |

**Limits of the fake LLM:** it only matches simple keywords (4-digit ticket ids, assign / close / change /
comment / create / SLA / how many …) and **does not really read the tool descriptions**. To see
"descriptions shape the AI's behaviour", use Claude Code. With an API key later, replace only
`FakeLLM.next_step()` in `agent.py` with a call to a real model; nothing else changes.

**Terminal 2 — open Claude Code in this folder**

```bash
claude
```

The first time (and after `.mcp.json` changes) Claude Code asks whether to allow the `helpdesk` MCP
server from `.mcp.json` → allow it. Check with `/mcp` that `helpdesk` is connected with 5 tools.

## Presentation script (type one at a time in Claude Code)

| # | Type in Claude Code | What to point out |
|---|---|---|
| 1 | `Which tickets will breach SLA in the next 4 hours?` | Claude **picks the tool and arguments itself** from the descriptions (likely `search_tickets` + `sla_due_within_hours`); we never told it a tool name |
| 2 | `Show me ticket 1003 and summarize where it stands` | `get_ticket` pulls the comment history to summarize |
| 3 | `Assign 1002 to pimchanok and add a comment that we've picked it up and will contact the customer today` | **Claude Code asks permission before calling the tool** (a human stays in control) → allow it and watch the card flash on the board and `PATCH` / `POST` appear in the log |
| 4 | `A customer messaged on LINE: they paid by QR and the app froze. Create a high-priority ticket` | `create_ticket` → a new ticket appears under Open with an SLA computed from the priority |
| 5 | `Close ticket 1006` | There is no "closed" status — the schema enforces an enum, and `update_ticket`'s description says to comment before resolving → see whether Claude follows it: **description quality = AI behaviour** |

## Look underneath (no AI)

```bash
.venv/bin/python wire_demo.py
```

It prints 4 steps: handshake → `tools/list` (the manual the AI reads) → `tools/call` (what the AI sends)
→ the result (the JSON the AI answers from). Add `--full` to see every tool's schema.

**Try HTTP (how a shared, deployed server would run)** — start `.venv/bin/python server.py --http`, then
call it with curl:

```bash
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

On Windows PowerShell, put it on one line, use `curl.exe`, and escape the inner quotes as `\"`.

→ The reply is plain JSON-RPC: MCP is just HTTP + JSON, nothing magic.

## What this demo does *not* do (what a real system needs)

| Topic | In the demo | A real system needs |
|---|---|---|
| Auth / permissions | The user comes from an env var (`HELPDESK_USER`), no login | OAuth per the MCP spec; the agent can do only what the logged-in user can |
| Transport | stdio (runs on your machine) | Streamable HTTP deployed as a shared service (`--http` is there to try, but has no auth yet) |
| Approval before changes | Client side only: the AI helper's Confirm card (from `readOnlyHint`) / Claude Code's permission prompt. **The server does not enforce it**: a client that doesn't ask can change data | Decide which actions need human confirmation and enforce it in the server/backend too, not only in the client |
| LLM | The AI helper uses a fake LLM (keyword rules) | A real model (Claude etc.) in the agent service, with the key kept on the backend only |
| Audit log | A simple log on the board | Record who the agent acted for, what it did, and when |
| Prompt injection | Not handled | Ticket text comes from customers and may contain instructions meant to trick the agent |
| Rate limits / payload size | None | Limit calls and trim fields before sending them to the AI (see `_short()` in `server.py`) |

## Technical notes

- Uses the official MCP Python SDK **v2** (`mcp>=2.3`). In v2, `FastMCP` was renamed `MCPServer`
  (`from mcp.server.mcpserver import MCPServer`); examples online that still use `FastMCP` are v1.
- To show an error message to the AI, raise `ToolError`. SDK v2 hides the text of any other exception.
- The current MCP spec is 2026-07-28 (stateless, no `initialize` handshake). SDK 2.3 supports it; this
  demo's client still uses the older initialize-based handshake (2025-11-25).
- Tested on Windows 11, Python 3.13, mcp 2.3.0, Claude Code 2.1.330 (2026-11-24).
