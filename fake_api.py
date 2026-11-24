"""
Fake Helpdesk API (demo only).

Stands in for the real Helpdesk backend: an in-memory ticket store behind a
plain REST API, plus a live "issue board" web page so people can watch what
the AI agent does while it works.

    python fake_api.py            -> http://127.0.0.1:8765

The ticket API knows nothing about AI or MCP. The MCP server (server.py) is just another
API client, exactly like the Helpdesk web UI would be.

The /api/agent/* routes are the "AI helper" chat box's backend: they hand the message to
the agent service (agent.py), which talks to server.py over MCP like any other MCP client.
"""

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST, PORT = "127.0.0.1", int(os.environ.get("HELPDESK_PORT", "8765"))
# Tell the MCP server (started by the AI helper) where this API lives.
os.environ.setdefault("HELPDESK_API", f"http://{HOST}:{PORT}/api")
STATUSES = ["open", "in_progress", "waiting_customer", "resolved"]
PRIORITIES = ["low", "medium", "high", "urgent"]
BOARD_HTML = Path(__file__).with_name("board.html")

_lock = threading.Lock()
_tickets: dict[int, dict] = {}
_activity: list[dict] = []
_next_id = 1001
_agent = None  # created on first use (see _get_agent)


def _get_agent():
    global _agent
    with _lock:
        if _agent is None:
            from agent import AgentRuntime  # imported lazily: the ticket API works even without the MCP SDK
            _agent = AgentRuntime()
    return _agent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _seed() -> None:
    global _next_id
    now = _now()
    seed = [
        ("ลูกค้าแจ้งชำระเงินผ่าน QR แล้วแต่สถานะยังไม่อัปเดต", "urgent", "open", "somchai", "LINE", 1),
        ("ขอเปลี่ยนที่อยู่จัดส่งใบกำกับภาษี", "low", "open", None, "Email", 30),
        ("แอปเด้งออกตอนเปิดหน้าประวัติการสั่งซื้อ (Android 15)", "high", "in_progress", "thanakorn", "App", 3),
        ("Web Chat ไม่แสดงรูปที่ลูกค้าแนบมา", "medium", "in_progress", "somchai", "Web Chat", 8),
        ("ลูกค้าขอคืนสินค้า สินค้าชำรุด", "medium", "waiting_customer", "pimchanok", "LINE", 20),
        ("Login ผ่าน LINE ไม่ได้ ขึ้น error 400", "high", "open", "thanakorn", "LINE", 2),
        ("สอบถามโปรโมชันเดือนธันวาคม", "low", "resolved", "pimchanok", "Facebook Messenger", 48),
        ("SLA report ประจำสัปดาห์ตัวเลขไม่ตรงกับ dashboard", "medium", "open", None, "Email", 12),
    ]
    for title, prio, status, assignee, channel, sla_hours in seed:
        tid = _next_id
        _next_id += 1
        created = now - timedelta(hours=5)
        _tickets[tid] = {
            "id": tid,
            "title": title,
            "status": status,
            "priority": prio,
            "assignee": assignee,
            "channel": channel,
            "sla_due": _iso(now + timedelta(hours=sla_hours)),
            "created_at": _iso(created),
            "updated_at": _iso(created),
            "comments": [],
        }
    _tickets[1003]["comments"].append(
        {"author": "thanakorn", "text": "Reproduce ได้บน Pixel 8 เท่านั้น กำลังดู crash log", "at": _iso(now - timedelta(hours=1))}
    )


def _log(method: str, path: str, client: str, summary: str) -> None:
    _activity.append({"at": _iso(_now()), "method": method, "path": path, "client": client, "summary": summary})
    del _activity[:-50]


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeHelpdesk/0.1"

    # ---- helpers -------------------------------------------------------
    def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code: int, msg: str):
        self._send(code, {"error": msg})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _client(self) -> str:
        return self.headers.get("X-Client", "browser")

    def log_message(self, fmt, *args):  # print only calls made by API clients (not the board's polling)
        if "X-Client" in self.headers:
            super().log_message(fmt, *args)

    # ---- routes --------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/board"):
            return self._send(200, BOARD_HTML.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/agent/info":
            return self._send(200, _get_agent().info())
        if url.path == "/api/activity":
            with _lock:
                return self._send(200, list(reversed(_activity)))
        if url.path == "/api/tickets":
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            with _lock:
                items = list(_tickets.values())
                if q.get("status"):
                    items = [t for t in items if t["status"] == q["status"]]
                if q.get("assignee"):
                    want = q["assignee"].lower()
                    items = [t for t in items if (t["assignee"] or "unassigned").lower() == want]
                if q.get("priority"):
                    items = [t for t in items if t["priority"] == q["priority"]]
                if q.get("q"):
                    items = [t for t in items if q["q"].lower() in t["title"].lower()]
                if q.get("sla_due_within_hours"):
                    limit = _now() + timedelta(hours=float(q["sla_due_within_hours"]))
                    items = [t for t in items if t["status"] != "resolved" and datetime.fromisoformat(t["sla_due"]) <= limit]
                if self._client() != "browser":
                    _log("GET", self.path, self._client(), f"search → {len(items)} tickets")
            return self._send(200, items)
        m = re.fullmatch(r"/api/tickets/(\d+)", url.path)
        if m:
            with _lock:
                t = _tickets.get(int(m.group(1)))
                if t and self._client() != "browser":
                    _log("GET", url.path, self._client(), f"read #{t['id']}")
            return self._send(200, t) if t else self._error(404, "ticket not found")
        self._error(404, "not found")

    def do_POST(self):
        global _next_id
        url = urlparse(self.path)
        body = self._body()
        if url.path in ("/api/agent/chat", "/api/agent/confirm"):
            try:
                if url.path == "/api/agent/chat":
                    msg = (body.get("message") or "").strip()
                    if not msg:
                        return self._error(400, "message is required")
                    return self._send(200, _get_agent().chat(msg))
                return self._send(200, _get_agent().confirm(body.get("conversation_id", ""), bool(body.get("approve"))))
            except Exception as exc:  # show agent failures in the chat box instead of a dead request
                return self._send(200, {"state": "done", "events": [{"kind": "answer", "text": f"Agent error: {exc}"}]})
        if url.path == "/api/tickets":
            title = (body.get("title") or "").strip()
            prio = body.get("priority", "medium")
            if not title:
                return self._error(400, "title is required")
            if prio not in PRIORITIES:
                return self._error(400, f"priority must be one of {PRIORITIES}")
            now = _now()
            sla_hours = {"urgent": 2, "high": 8, "medium": 24, "low": 72}[prio]
            with _lock:
                tid = _next_id
                _next_id += 1
                t = {
                    "id": tid,
                    "title": title,
                    "status": "open",
                    "priority": prio,
                    "assignee": body.get("assignee"),
                    "channel": body.get("channel", "Web"),
                    "sla_due": _iso(now + timedelta(hours=sla_hours)),
                    "created_at": _iso(now),
                    "updated_at": _iso(now),
                    "comments": [],
                }
                _tickets[tid] = t
                _log("POST", url.path, self._client(), f"created #{tid} “{title}”")
            return self._send(201, t)
        m = re.fullmatch(r"/api/tickets/(\d+)/comments", url.path)
        if m:
            text = (body.get("text") or "").strip()
            if not text:
                return self._error(400, "text is required")
            with _lock:
                t = _tickets.get(int(m.group(1)))
                if not t:
                    return self._error(404, "ticket not found")
                c = {"author": body.get("author", "unknown"), "text": text, "at": _iso(_now())}
                t["comments"].append(c)
                t["updated_at"] = c["at"]
                _log("POST", url.path, self._client(), f"comment on #{t['id']}")
            return self._send(201, t)
        self._error(404, "not found")

    def do_PATCH(self):
        m = re.fullmatch(r"/api/tickets/(\d+)", urlparse(self.path).path)
        if not m:
            return self._error(404, "not found")
        body = self._body()
        if "status" in body and body["status"] not in STATUSES:
            return self._error(400, f"status must be one of {STATUSES}")
        if "priority" in body and body["priority"] not in PRIORITIES:
            return self._error(400, f"priority must be one of {PRIORITIES}")
        with _lock:
            t = _tickets.get(int(m.group(1)))
            if not t:
                return self._error(404, "ticket not found")
            changes = []
            for field in ("status", "assignee", "priority"):
                if field in body and body[field] != t[field]:
                    changes.append(f"{field}: {t[field]} → {body[field]}")
                    t[field] = body[field]
            t["updated_at"] = _iso(_now())
            _log("PATCH", self.path, self._client(), f"#{t['id']} " + (", ".join(changes) or "no change"))
        self._send(200, t)


if __name__ == "__main__":
    _seed()
    print(f"Fake Helpdesk API + board on http://{HOST}:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
