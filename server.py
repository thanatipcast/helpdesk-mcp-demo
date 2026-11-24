"""
Helpdesk MCP server (demo).

This whole file IS "the MCP". It is a small program that:
  1. tells the AI which tools exist (name + description + input schema), and
  2. when the AI calls a tool, runs the matching Python function,
     which just calls the normal Helpdesk REST API.

There is no AI in here. The AI lives in the client (Claude Code, Claude Desktop, ...).

Run modes:
    python server.py                 # stdio: the client starts this as a child process (what this demo uses)
    python server.py --http          # Streamable HTTP on http://127.0.0.1:8000/mcp (what a shared team server would use)
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal

# If started with a system Python (e.g. `python3 server.py` from .mcp.json), re-run inside this
# folder's .venv so the same config works on Windows and macOS. stdin/stdout are inherited.
_VENV = Path(__file__).resolve().parent / ".venv"
_VENV_PY = _VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if _VENV_PY.exists() and Path(sys.prefix).resolve() != _VENV.resolve():
    sys.exit(subprocess.call([str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]]))

import httpx  # noqa: E402 — imported after the venv hand-off above
from mcp.server.mcpserver import MCPServer  # noqa: E402 — official Python SDK v2 (was called FastMCP in v1)
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402 — message is shown to the AI (others are hidden)
from mcp.types import ToolAnnotations  # noqa: E402
from pydantic import Field  # noqa: E402

API = os.environ.get("HELPDESK_API", "http://127.0.0.1:8765/api")
# In a real server this would come from the user's OAuth login, not an env var.
ACTING_USER = os.environ.get("HELPDESK_USER", "demo-dev")
# Label shown on the board's activity panel, so you can tell which client used this same server.
CLIENT_LABEL = os.environ.get("HELPDESK_CLIENT", "MCP agent")

logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = MCPServer("helpdesk")
http = httpx.Client(base_url=API, timeout=10, headers={"X-Client": CLIENT_LABEL})

Status = Literal["open", "in_progress", "waiting_customer", "resolved"]
Priority = Literal["low", "medium", "high", "urgent"]


def _call(method: str, path: str, **kwargs):
    """Call the Helpdesk API and turn API errors into readable tool errors."""
    try:
        r = http.request(method, path, **kwargs)
    except httpx.ConnectError:
        raise ToolError(f"Cannot reach the Helpdesk API at {API}. Is fake_api.py running?")
    if r.status_code >= 400:
        raise ToolError(f"Helpdesk API error {r.status_code}: {r.json().get('error', r.text)}")
    return r.json()


def _short(t: dict) -> dict:
    """Trim a ticket to the fields an agent usually needs (keeps the AI's context small)."""
    return {k: t[k] for k in ("id", "title", "status", "priority", "assignee", "channel", "sla_due")} | {
        "comment_count": len(t["comments"])
    }


# ---------------------------------------------------------------- read-only tools

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def search_tickets(
    status: Annotated[Status | None, Field(description="Only tickets in this status")] = None,
    assignee: Annotated[str | None, Field(description="Staff username, or 'unassigned'")] = None,
    priority: Annotated[Priority | None, Field(description="Only tickets with this priority")] = None,
    text: Annotated[str | None, Field(description="Words to look for in the ticket title")] = None,
    sla_due_within_hours: Annotated[
        float | None, Field(description="Only unresolved tickets whose SLA deadline is within this many hours (overdue ones included)")
    ] = None,
) -> list[dict]:
    """Search helpdesk tickets. Use this to find or list tickets, e.g. 'my open tickets',
    'urgent tickets', or 'tickets about to breach SLA'. Returns a short summary of each ticket;
    call get_ticket for the full details and comments."""
    params = {k: v for k, v in locals().items() if v is not None}
    if "text" in params:
        params["q"] = params.pop("text")
    return [_short(t) for t in _call("GET", "/tickets", params=params)]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_ticket(ticket_id: Annotated[int, Field(description="Ticket number, e.g. 1003")]) -> dict:
    """Get one ticket with all its details and its full comment history."""
    return _call("GET", f"/tickets/{ticket_id}")


# ---------------------------------------------------------------- tools that change data

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def create_ticket(
    title: Annotated[str, Field(description="Short summary of the problem")],
    priority: Priority = "medium",
    channel: Annotated[str, Field(description="Where the request came from, e.g. LINE, Email, App")] = "Web",
    assignee: Annotated[str | None, Field(description="Staff username to assign, if known")] = None,
) -> dict:
    """Create a new helpdesk ticket. The SLA deadline is set automatically from the priority."""
    body = {"title": title, "priority": priority, "channel": channel, "assignee": assignee}
    return _short(_call("POST", "/tickets", json=body))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def update_ticket(
    ticket_id: int,
    status: Status | None = None,
    assignee: Annotated[str | None, Field(description="Staff username to assign the ticket to")] = None,
    priority: Priority | None = None,
) -> dict:
    """Change a ticket's status, assignee or priority. Only the fields you pass are changed.
    Before setting status to 'resolved', add a comment explaining what was done."""
    body = {k: v for k, v in {"status": status, "assignee": assignee, "priority": priority}.items() if v is not None}
    if not body:
        raise ToolError("Pass at least one of status, assignee or priority.")
    return _short(_call("PATCH", f"/tickets/{ticket_id}", json=body))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def add_comment(
    ticket_id: int,
    text: Annotated[str, Field(description="The comment. Write it for the support team to read.")],
) -> dict:
    """Add an internal comment to a ticket. It is recorded as written by the AI agent on behalf of the current user."""
    body = {"text": text, "author": f"{ACTING_USER} (via AI agent)"}
    return _short(_call("POST", f"/tickets/{ticket_id}/comments", json=body))


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio
