"""
Helpdesk AI helper — the "agent service" (demo).

In the real system this piece lives inside the Helpdesk backend:

    admin types in chat box (browser)
        -> agent service  (LLM loop + MCP client)      <- this file
            -> MCP server (server.py)  -> Helpdesk API

ONLY THE LLM IS FAKE. FakeLLM uses keyword rules instead of calling Claude/GPT, so no
API key is needed. Everything else is the real thing:
  * it connects to server.py over MCP (stdio) and reads tools/list,
  * every action is a real tools/call,
  * any tool NOT marked readOnlyHint=True must be confirmed by the admin first (human approval).

Swapping FakeLLM for a real model changes only FakeLLM.next_step(); the loop stays the same.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).parent
SERVER = HERE / "server.py"
MAX_STEPS = 6

# Who the agent acts as. A real system would pass the logged-in admin's token instead.
SERVER_ENV = {
    "HELPDESK_USER": "admin",
    "HELPDESK_CLIENT": "AI helper (MCP)",
    "HELPDESK_API": os.environ.get("HELPDESK_API", "http://127.0.0.1:8765/api"),
}


# =============================================================================================
# The fake LLM
# =============================================================================================

STATUS_WORDS = [  # (regex, status) — checked in this order
    (r"รอลูกค้า|waiting", "waiting_customer"),
    (r"กำลังทำ|in.?progress|เริ่มทำ|รับงาน", "in_progress"),
    (r"ปิดแล้ว|resolved|เสร็จแล้ว", "resolved"),
    (r"เปิดใหม่|reopen|ยังเปิด|\bopen\b|ค้างอยู่", "open"),
]
CHANNELS = [("line", "LINE"), ("email", "Email"), ("อีเมล", "Email"), ("web chat", "Web Chat"),
            ("facebook", "Facebook Messenger"), ("messenger", "Facebook Messenger"), ("app", "App"), ("แอป", "App")]


def _find_priority(t: str) -> str | None:
    if re.search(r"ไม่ด่วน|\blow\b", t):
        return "low"
    if re.search(r"urgent|ด่วนมาก|ด่วนที่สุด", t):
        return "urgent"
    if re.search(r"\bhigh\b|ด่วน|สำคัญ", t):
        return "high"
    if re.search(r"\bmedium\b|ปานกลาง", t):
        return "medium"
    return None


def _find_status(t: str) -> str | None:
    for pattern, status in STATUS_WORDS:
        if re.search(pattern, t):
            return status
    return None


def _sla_text(t: dict) -> str:
    if t.get("status") == "resolved":
        return "ปิดแล้ว"
    h = (datetime.fromisoformat(t["sla_due"]) - datetime.now(timezone.utc)).total_seconds() / 3600
    return f"SLA เกินมา {-h:.1f} ชม." if h < 0 else f"SLA เหลือ {h:.1f} ชม."


class FakeLLM:
    """Stands in for Claude/GPT with the same contract as a real tool-calling model:

        input : the user's message, the tool list from MCP (tools/list), and the tool results so far
        output: either {"type": "tool_call", name, arguments} or {"type": "answer", text}

    A real model reads the tool *descriptions* and reasons; this one matches keywords.
    It still only calls tools that the MCP server actually offers.
    """

    # ---- the one method a real model would replace ------------------------------------------
    def next_step(self, user_text: str, tools: dict[str, dict], done: list[dict]) -> dict:
        plan = self._plan(user_text, tools)
        if isinstance(plan, str):  # nothing to call
            return {"type": "answer", "text": plan}
        if done and not done[-1]["ok"]:  # last call failed or was declined -> stop and explain
            return {"type": "answer", "text": self._summarize(user_text, done)}
        if len(done) < len(plan):
            name, args = plan[len(done)]
            return {"type": "tool_call", "name": name, "arguments": args}
        return {"type": "answer", "text": self._summarize(user_text, done)}

    # ---- "reasoning" (keyword rules) --------------------------------------------------------
    def _plan(self, text: str, tools: dict[str, dict]):
        t = text.lower()
        ids = [int(x) for x in re.findall(r"(?<!\d)(\d{4})(?!\d)", t)]
        plan: list[tuple[str, dict]] = []

        if re.search(r"สร้าง\s*(?:ticket|ทิกเก็ต|เคส)|create (?:a )?ticket|เปิด\s*(?:ticket|เคส)ใหม่", t):
            plan.append(("create_ticket", self._create_args(text, t)))

        elif ids and not re.search(r"หรือยัง|ไหม|มั้ย|\?", t):
            tid = ids[0]
            assignee = re.search(r"(?:ให้|to)\s*@?([a-z][\w.-]*)", t)
            comment = re.search(r"(?:comment|คอมเมนต์|คอมเม้นต์|โน้ต|note)\s*(?:ว่า|:)?\s*(.+)$", text, re.I)
            comment_text = comment.group(1).strip(" \"'“”") if comment else None
            if re.search(r"assign|มอบหมาย|โยน", t) and assignee:
                plan.append(("update_ticket", {"ticket_id": tid, "assignee": assignee.group(1)}))
            if re.search(r"(?<!เ)ปิด|resolve|close", t) and not re.search(r"(?<!เ)ปิดแล้ว", t):  # "เปิด" contains "ปิด"
                # update_ticket's description says: comment before resolving. A good model follows it.
                plan.append(("add_comment", {"ticket_id": tid, "text": comment_text or "ดำเนินการเรียบร้อย ปิดงานผ่าน AI helper"}))
                plan.append(("update_ticket", {"ticket_id": tid, "status": "resolved"}))
                comment_text = None
            elif re.search(r"เปลี่ยน|ปรับ|ย้าย|set|เป็น|change", t):
                change = {}
                if re.search(r"priority|ความสำคัญ", t) and _find_priority(t):
                    change["priority"] = _find_priority(t)
                elif _find_status(t):
                    change["status"] = _find_status(t)
                if change:
                    plan.append(("update_ticket", {"ticket_id": tid, **change}))
            if comment_text:
                plan.append(("add_comment", {"ticket_id": tid, "text": comment_text}))
            if not plan:
                plan = [("get_ticket", {"ticket_id": i}) for i in ids[:3]]

        elif ids:  # a question about specific tickets
            plan = [("get_ticket", {"ticket_id": i}) for i in ids[:3]]

        elif re.search(r"stock|สต็อก|สต๊อก|คลังสินค้า|คำสั่งซื้อ|\border\b|ราคาสินค้า", t):
            names = ", ".join(f"`{n}`" for n in tools)
            return ("ทำไม่ได้ครับ: MCP server ที่เชื่อมอยู่ (helpdesk) ไม่มี tool เรื่องสต็อกหรือคำสั่งซื้อ "
                    f"มีแค่ {names}\n\nถ้าอยากให้ AI helper เช็กสต็อกได้ ต้องเชื่อม MCP server ของระบบ stock/ERP เพิ่ม "
                    "โดย agent service ตัวเดิมต่อหลาย MCP server พร้อมกันได้ ไม่ต้องแก้ server.py")

        else:
            args = {}
            if re.search(r"sla|หลุด|เกินกำหนด|ใกล้ครบ|ใกล้หมด", t):
                hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:ชั่วโมง|ชม|hours?|hrs?|h\b)", t)
                args["sla_due_within_hours"] = float(hours.group(1)) if hours else 4.0
            if (p := _find_priority(t)) and not re.search(r"สรุป|จำนวน|กี่", t):
                args["priority"] = p
            if (s := _find_status(t)) and "sla_due_within_hours" not in args:
                args["status"] = s
            if re.search(r"ยังไม่มีคน|ไม่มีคนรับ|unassigned|ยังไม่ assign", t):
                args["assignee"] = "unassigned"
            elif who := re.search(r"(?:ของ|assigned to|assignee)\s*@?([a-z][\w.-]*)", t):
                args["assignee"] = who.group(1)
            if about := re.search(r"(?:เกี่ยวกับ|เรื่อง|about)\s*([^\s]+)", text, re.I):
                args["text"] = about.group(1)
            if args or re.search(r"ticket|ทิกเก็ต|เคส|case|งาน|list|ค้นหา|หา|มีอะไร|สรุป|จำนวน|กี่", t):
                plan.append(("search_tickets", args))
            else:
                lines = "\n".join(f"• `{n}`: {d.get('description', '').splitlines()[0]}" for n, d in tools.items())
                return ("ยังไม่เข้าใจคำสั่งครับ (fake LLM ใช้ keyword ง่าย ๆ) ลองถามเรื่อง ticket เช่น "
                        "\"มี ticket ไหน SLA จะหลุดใน 4 ชั่วโมง\" หรือ \"ดู ticket 1003\"\n\n"
                        f"Tools ที่ MCP server ให้มา:\n{lines}")

        missing = [name for name, _ in plan if name not in tools]
        if missing:
            return (f"ทำไม่ได้ครับ: ต้องใช้ tool `{missing[0]}` แต่ MCP server ไม่มี tool นี้ "
                    f"(ตอนนี้มี: {', '.join(tools)})")
        return plan

    @staticmethod
    def _create_args(text: str, t: str) -> dict:
        title = None
        if m := re.search(r"ว่า\s*(.+?)(?:\s*(?:สร้าง|เปิด)\s*(?:ticket|ทิกเก็ต|เคส)|$)", text, re.I):
            title = m.group(1)
        elif m := re.search(r"(?:ticket|ทิกเก็ต|เคส)\s*(?:ใหม่)?\s*[:：]\s*(.+)$", text, re.I):
            title = m.group(1)
        title = re.sub(r"\s*(?:ให้หน่อย|หน่อย|priority\s*\w+|ด่วน\w*)\s*", " ", title or text, flags=re.I).strip(" ,.")
        args = {"title": title[:120] or "Ticket จาก AI helper", "priority": _find_priority(t) or "medium"}
        for word, channel in CHANNELS:
            if word in t:
                args["channel"] = channel
                break
        return args

    # ---- writing the final answer ------------------------------------------------------------
    def _summarize(self, user_text: str, done: list[dict]) -> str:
        out = []
        for step in done:
            name, args, data = step["name"], step["arguments"], step.get("data")
            if step.get("declined"):
                out.append(f"ยกเลิกแล้ว ไม่ได้เรียก `{name}` (admin ไม่อนุมัติ) จึงไม่ได้เปลี่ยนแปลงอะไรต่อ")
            elif not step["ok"]:
                out.append(f"ทำไม่สำเร็จ: {step['error']}")
            elif name == "search_tickets":
                out.append(self._search_text(user_text, args, data))
            elif name == "get_ticket":
                c = data.get("comments") or []
                last = f"\nComment ล่าสุด ({c[-1]['author']}): {c[-1]['text']}" if c else "\nยังไม่มี comment"
                out.append(f"#{data['id']} {data['title']}\nสถานะ {data['status']} · {data['priority']} · "
                           f"@{data['assignee'] or 'unassigned'} · {data['channel']} · {_sla_text(data)}{last}")
            elif name == "create_ticket":
                out.append(f"สร้าง ticket #{data['id']} แล้ว: \"{data['title']}\" ({data['priority']}, {data['channel']}) "
                           f"· {_sla_text(data)}")
            elif name == "update_ticket":
                changed = ", ".join(f"{k} → {v}" for k, v in args.items() if k != "ticket_id")
                out.append(f"อัปเดต #{args['ticket_id']} แล้ว: {changed}")
            elif name == "add_comment":
                out.append(f"เพิ่ม comment ใน #{args['ticket_id']} แล้ว: \"{args['text']}\"")
        return "\n\n".join(out)

    @staticmethod
    def _search_text(user_text: str, args: dict, tickets: list[dict]) -> str:
        if re.search(r"สรุป|จำนวน|กี่", user_text):
            counts: dict[str, int] = {}
            for x in tickets:
                counts[x["status"]] = counts.get(x["status"], 0) + 1
            return f"ทั้งหมด {len(tickets)} tickets: " + ", ".join(f"{k} {v}" for k, v in counts.items())
        if not tickets:
            return "ไม่เจอ ticket ที่ตรงเงื่อนไขครับ"
        lines = [f"• #{x['id']} [{x['priority']}] {x['title']} — @{x['assignee'] or 'unassigned'} · {_sla_text(x)}"
                 for x in tickets[:8]]
        more = f"\n…และอีก {len(tickets) - 8} รายการ" if len(tickets) > 8 else ""
        return f"เจอ {len(tickets)} tickets:\n" + "\n".join(lines) + more


# =============================================================================================
# The agent loop + MCP client
# =============================================================================================

@dataclass
class Conversation:
    id: str
    user_text: str
    done: list[dict] = field(default_factory=list)
    pending: dict | None = None


class Agent:
    def __init__(self) -> None:
        self.llm = FakeLLM()
        self.session: ClientSession | None = None
        self.tools: dict[str, dict] = {}
        self.conversations: dict[str, Conversation] = {}
        self._mtime: float | None = None
        self._stop: asyncio.Event | None = None
        self._holder: asyncio.Task | None = None

    # ---- MCP connection ----------------------------------------------------------------------
    async def ensure_connected(self) -> dict | None:
        """Connect to server.py; reconnect if the file changed so edits show up without a restart."""
        mtime = SERVER.stat().st_mtime
        if self.session and mtime == self._mtime:
            return None
        reconnect = self.session is not None
        if reconnect:
            self._stop.set()
            await self._holder
        ready = asyncio.get_running_loop().create_future()
        self._stop = asyncio.Event()
        self._holder = asyncio.create_task(self._hold_connection(ready))
        await ready
        self._mtime = mtime
        if reconnect:
            return {"kind": "info", "text": f"server.py ถูกแก้ → reconnect MCP แล้วโหลด tools ใหม่ ({len(self.tools)} tools)"}
        return None

    async def _hold_connection(self, ready: asyncio.Future) -> None:
        # The connection lives inside this one task (the SDK's async contexts must open and close
        # in the same task). Other tasks just use self.session.
        params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=SERVER_ENV, cwd=str(HERE))
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = (await session.list_tools()).tools
                    self.tools = {x.name: x.model_dump(mode="json", by_alias=True, exclude_none=True) for x in listed}
                    self.session = session
                    ready.set_result(True)
                    await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 — report connection failures to the caller
            if not ready.done():
                ready.set_exception(RuntimeError(f"เชื่อม MCP server ไม่ได้: {exc!r}"))
        finally:
            self.session = None

    def info(self) -> dict:
        return {
            "connected": self.session is not None,
            "server": "helpdesk (server.py, stdio)",
            "llm": "FakeLLM (keyword rules, no API key)",
            "tools": [{"name": n, "read_only": bool(d.get("annotations", {}).get("readOnlyHint"))}
                      for n, d in self.tools.items()],
        }

    # ---- the loop ----------------------------------------------------------------------------
    async def chat(self, message: str) -> dict:
        events = []
        if note := await self.ensure_connected():
            events.append(note)
        conv = Conversation(id=uuid.uuid4().hex[:8], user_text=message)
        self.conversations[conv.id] = conv
        events.append({"kind": "info", "text": f"ส่งข้อความ + tools/list ({len(self.tools)} tools) ให้ LLM"})
        events += await self._advance(conv)
        return self._response(conv, events)

    async def confirm(self, conversation_id: str, approve: bool) -> dict:
        conv = self.conversations.get(conversation_id)
        if not conv or not conv.pending:
            return {"conversation_id": conversation_id, "events": [{"kind": "answer", "text": "ไม่มีรายการรออนุมัติแล้ว"}],
                    "state": "done"}
        await self.ensure_connected()
        name, args = conv.pending["name"], conv.pending["arguments"]
        conv.pending = None
        if approve:
            events = [{"kind": "info", "text": f"admin อนุมัติ → เรียก {name}"}] + await self._execute(conv, name, args)
        else:
            conv.done.append({"name": name, "arguments": args, "ok": False, "declined": True})
            events = [{"kind": "info", "text": f"admin ไม่อนุมัติ → ไม่เรียก {name}"}]
        events += await self._advance(conv)
        return self._response(conv, events)

    async def _advance(self, conv: Conversation) -> list[dict]:
        events = []
        for _ in range(MAX_STEPS):
            step = self.llm.next_step(conv.user_text, self.tools, conv.done)
            if step["type"] == "answer":
                events.append({"kind": "answer", "text": step["text"]})
                return events
            name, args = step["name"], step["arguments"]
            events.append({"kind": "llm", "name": name, "arguments": args})
            read_only = self.tools[name].get("annotations", {}).get("readOnlyHint", False)
            if not read_only:  # human approval: decided from the tool's MCP annotations
                conv.pending = {"name": name, "arguments": args}
                events.append({"kind": "confirm", "name": name, "arguments": args})
                return events
            events += await self._execute(conv, name, args)
        events.append({"kind": "answer", "text": f"หยุดที่ {MAX_STEPS} steps (กันวนไม่รู้จบ)"})
        return events

    async def _execute(self, conv: Conversation, name: str, args: dict) -> list[dict]:
        try:
            result = await self.session.call_tool(name, args)
        except Exception as exc:  # noqa: BLE001 — connection problems become a failed step
            conv.done.append({"name": name, "arguments": args, "ok": False, "error": f"MCP call ล้มเหลว: {exc!r}"})
            return [{"kind": "result", "ok": False, "text": f"MCP call ล้มเหลว: {exc!r}"}]
        wire = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        texts = [c.get("text", "") for c in wire.get("content", []) if c.get("type") == "text"]
        if wire.get("isError"):
            err = " ".join(texts)
            conv.done.append({"name": name, "arguments": args, "ok": False, "error": err})
            return [{"kind": "result", "ok": False, "text": err}]
        data = wire.get("structuredContent")
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        if data is None and texts:
            data = json.loads(texts[0])
        conv.done.append({"name": name, "arguments": args, "ok": True, "data": data})
        summary = f"{len(data)} tickets" if isinstance(data, list) else f"ticket #{data.get('id')}"
        return [{"kind": "result", "ok": True, "text": f"ได้ผลกลับมา: {summary}"}]

    @staticmethod
    def _response(conv: Conversation, events: list[dict]) -> dict:
        return {"conversation_id": conv.id, "events": events, "state": "waiting_confirm" if conv.pending else "done"}


class AgentRuntime:
    """Runs the async agent on its own event-loop thread so the plain (threaded) HTTP server can call it."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="agent-loop").start()
        self.agent = Agent()

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=90)

    def info(self) -> dict:
        try:
            if self.agent.session is None:  # reconnects after edits happen on the next chat, so the chat can say so
                self._run(self.agent.ensure_connected())
        except Exception as exc:  # noqa: BLE001
            return {"connected": False, "error": str(exc)}
        return self.agent.info()

    def chat(self, message: str) -> dict:
        return self._run(self.agent.chat(message))

    def confirm(self, conversation_id: str, approve: bool) -> dict:
        return self._run(self.agent.confirm(conversation_id, approve))
