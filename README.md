# Helpdesk MCP — demo prototype

Demo สั้น ๆ ให้เห็นว่า "MCP server" หน้าตาจริง ๆ เป็นยังไง: ให้ AI agent (Claude Code) ค้นหา / อัปเดต / สร้าง ticket
บน Helpdesk issue board ผ่าน MCP แล้วดูผลบน board แบบ live

> ข้อมูลทั้งหมดเป็นข้อมูลปลอม อยู่ใน memory ปิดโปรแกรมแล้วหาย ไม่ได้ต่อกับ Helpdesk จริง

## มีอะไรบ้าง

```
 MCP client (มี AI)                     MCP server                Helpdesk backend (ปลอม)
 ┌──────────────────────────────┐
 │ A) AI helper chat box บน board│──┐
 │    → agent.py (fake LLM)      │  ├─MCP──►  server.py  ──REST──►  fake_api.py  ──►  board (http://127.0.0.1:8765)
 │ B) Claude Code                │──┘        ▲ server ตัวเดียวกัน ใช้ได้กับทุก client
 └──────────────────────────────┘
```

| ไฟล์ | คืออะไร |
|---|---|
| `server.py` | **ตัว MCP server** (~130 บรรทัด) — ประกาศ tools 5 ตัว: `search_tickets`, `get_ticket`, `create_ticket`, `update_ticket`, `add_comment` แต่ละตัวแค่เรียก REST API ข้างในไม่มี AI |
| `agent.py` | **Agent service** ของ AI helper (ส่วนที่ Helpdesk backend จะมีจริง): MCP client + agent loop + human approval — **LLM เป็นของปลอม** (keyword rules ไม่ต้องใช้ API key) ส่วนอื่นเป็น MCP จริงทั้งหมด |
| `fake_api.py` + `board.html` | Helpdesk ปลอม: REST API + หน้า board ที่อัปเดต live + กล่อง AI helper + log ว่าแต่ละ client เรียก API อะไรบ้าง |
| `wire_demo.py` | สคริปต์ที่ทำตัวเป็น MCP client เอง (ไม่มี AI) แล้ว print JSON ที่วิ่งไปมา — ให้เห็นว่า AI "เห็นอะไร" |
| `.mcp.json` | config ที่บอก Claude Code ว่าโฟลเดอร์นี้มี MCP server ชื่อ `helpdesk` |

## Setup (ครั้งแรกครั้งเดียว)

ต้องมี Python 3.11+ และ Claude Code

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

**macOS / Linux** — คำสั่งอื่น ๆ ใน README ให้เปลี่ยน `.venv\Scripts\python` เป็น `.venv/bin/python`

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`.mcp.json` ใช้ `python3 server.py` ได้ทั้งสอง OS (server.py จะสลับไปใช้ `.venv` ให้เอง)

## รัน demo

**Terminal 1 — เปิด Helpdesk ปลอม**

```powershell
.venv\Scripts\python fake_api.py
```

แล้วเปิด http://127.0.0.1:8765 ในเบราว์เซอร์ (แนะนำวางข้าง ๆ Claude Code ตอน present)

> ทุกคำสั่งในไฟล์นี้ต้องรันจากโฟลเดอร์ของ repo นี้ (ถ้ารันจากโฟลเดอร์อื่นจะเจอ error `The module '.venv' could not be loaded` บน Windows)

## AI helper chat box (แบบ "admin พิมพ์สั่งงานในระบบ")

กล่อง **AI helper** ด้านขวาของ board = AI ที่ฝังอยู่ในระบบ (frontend agent): admin พิมพ์สั่ง → agent service (`agent.py`)
คุยกับ MCP server → board เปลี่ยน ไม่ต้องใช้ Claude Code หรือ API key

- กด chip คำสั่งตัวอย่าง หรือพิมพ์เอง
- ในแชทจะเห็นทุกขั้น: `LLM decides to call …` (LLM เลือก tool) → `MCP …` (ผลจาก `tools/call`) → คำตอบ
- tool ที่ **ไม่ได้** ประกาศ `readOnlyHint=True` (create / update / comment) จะขึ้นการ์ด **Confirm / Cancel** ก่อนเสมอ
  agent ตัดสินใจจาก annotation ที่ MCP server ส่งมาใน `tools/list` — นี่คือ human approval
- `เช็กสต็อกสินค้า A` → ทำไม่ได้ เพราะ MCP server ไม่มี tool นั้น (ถ้าจะทำได้ต้องต่อ MCP server ของระบบ stock เพิ่ม)
- log ด้านล่างจะแยกให้เห็นว่า call ไหนมาจาก `AI helper (MCP)` และ call ไหนมาจาก `Claude Code (MCP)` ทั้งที่ใช้ `server.py` ตัวเดียวกัน
- แก้ `server.py` แล้วส่งข้อความใหม่ได้เลย agent จะ reconnect และโหลด tools ใหม่ให้เอง (ไม่ต้อง restart `fake_api.py`)

**ข้อจำกัดของ fake LLM:** เข้าใจแค่ keyword ง่าย ๆ (ticket id 4 หลัก, assign/ปิด/เปลี่ยน/comment/สร้าง/SLA/สรุป …)
และ **ไม่ได้อ่าน description ของ tool จริง ๆ** — การทดลองเรื่อง "description กำหนดพฤติกรรม AI" ต้องลองกับ Claude Code
ถ้ามี API key ภายหลัง เปลี่ยนแค่ `FakeLLM.next_step()` ใน `agent.py` เป็นการเรียก model จริง ส่วนอื่นเหมือนเดิม

**Terminal 2 — เปิด Claude Code ในโฟลเดอร์นี้**

```powershell
claude
```

ครั้งแรก (หรือหลัง `.mcp.json` ถูกแก้) Claude Code จะถามว่าจะอนุญาต MCP server `helpdesk` จาก `.mcp.json` ไหม → เลือกอนุญาต
เช็กได้ด้วย `/mcp` ว่า `helpdesk` ขึ้น connected และมี 5 tools

## Script สำหรับ present (พิมพ์ทีละข้อ)

| # | พิมพ์ใน Claude Code | ชี้ให้ดูอะไร |
|---|---|---|
| 1 | `มี ticket ไหนที่ SLA จะหลุดภายใน 4 ชั่วโมงบ้าง` | Claude **เลือก tool + parameter เอง** จาก description (น่าจะเป็น `search_tickets` + `sla_due_within_hours`) — เราไม่ได้บอกชื่อ tool เลย |
| 2 | `ดู ticket 1003 ให้หน่อย สรุปว่าตอนนี้ถึงไหนแล้ว` | `get_ticket` ดึง comment history มาสรุป |
| 3 | `assign 1002 ให้ pimchanok แล้วเขียน comment ว่ารับเรื่องแล้ว จะติดต่อลูกค้าภายในวันนี้` | **Claude Code ขออนุญาตก่อนเรียก tool** (คนยังคุมได้) → กด allow แล้วดู card บน board กระพริบ + log ด้านขวาโผล่ `PATCH` / `POST` |
| 4 | `ลูกค้าทัก LINE มาว่าสแกน QR จ่ายเงินแล้วแอปค้าง สร้าง ticket ให้หน่อย priority high` | `create_ticket` → ticket ใหม่โผล่ใน Open พร้อม SLA ที่คำนวณจาก priority |
| 5 | `ปิด ticket 1006 ให้หน่อย` | ระบบไม่มี status "closed" — schema บังคับ enum ไว้ และ description ของ `update_ticket` บอกให้เขียน comment ก่อน resolve → ดูว่า Claude ทำตามไหม ให้เห็นว่า **คุณภาพ description = พฤติกรรมของ AI** |

## ดูสิ่งที่วิ่งอยู่ข้างใต้ (ไม่มี AI)

```powershell
.venv\Scripts\python wire_demo.py
```

จะ print 4 ขั้น: handshake → `tools/list` (คู่มือที่ AI อ่าน) → `tools/call` (สิ่งที่ AI ส่งมา) → ผลลัพธ์ (JSON ที่ AI เอาไปตอบ)
ใส่ `--full` เพื่อดู schema ของทุก tool

**ลองแบบ HTTP (แบบที่ทีมจะใช้ร่วมกันจริง)** — รัน `.venv\Scripts\python server.py --http` แล้วยิงด้วย curl ได้เลย:

```powershell
curl.exe -s -X POST http://127.0.0.1:8000/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{},\"clientInfo\":{\"name\":\"curl\",\"version\":\"0\"}}}'
```

→ ตอบกลับเป็น JSON-RPC ธรรมดา ให้เห็นว่า MCP ก็คือ HTTP + JSON ไม่มีอะไรลึกลับ

## Demo นี้ "ยังไม่ได้" ทำอะไร (= สิ่งที่ระบบจริงต้องมี)

| เรื่อง | ใน demo | ของจริงต้องมี |
|---|---|---|
| Auth / สิทธิ์ | user มาจาก env var (`HELPDESK_USER`) ไม่มี login | OAuth ตาม MCP spec — agent ทำได้เท่าสิทธิ์ของ user ที่ login |
| Transport | stdio (รันบนเครื่องตัวเอง) | Streamable HTTP deploy เป็น service กลาง (`--http` มีให้ลองแล้ว แต่ยังไม่มี auth) |
| ยืนยันก่อนแก้ข้อมูล | ฝั่ง client: การ์ด Confirm ของ AI helper (อ่านจาก `readOnlyHint`) / permission prompt ของ Claude Code — **server ไม่ได้บังคับ** client ที่ไม่ถามก็แก้ได้เลย | ตัดสินใจว่า action ไหนต้อง human confirm และบังคับที่ server/backend ด้วย (ไม่พึ่ง client อย่างเดียว) |
| LLM | AI helper ใช้ fake LLM (keyword rules) | เรียก model จริง (Claude ฯลฯ) ใน agent service — key อยู่ฝั่ง backend เท่านั้น |
| Audit log | log ง่าย ๆ ใน board ("MCP agent") | บันทึกว่า agent ทำแทนใคร ทำอะไร เมื่อไหร่ |
| Prompt injection | ไม่ได้กัน | เนื้อหา ticket มาจากลูกค้า → อาจมีข้อความหลอก agent ให้ทำสิ่งที่ไม่ควร |
| Rate limit / ขนาดข้อมูล | ไม่มี | จำกัดจำนวน call, ตัด field ที่ไม่จำเป็นก่อนส่งให้ AI (ดู `_short()` ใน `server.py`) |

## หมายเหตุทางเทคนิค

- ใช้ official MCP Python SDK **v2** (`mcp>=2.3`) — ใน v2 `FastMCP` เปลี่ยนชื่อเป็น `MCPServer`
  (`from mcp.server.mcpserver import MCPServer`) ตัวอย่างบนเน็ตที่ยังใช้ `FastMCP` คือ v1
- Error ที่อยากให้ AI เห็นข้อความต้อง raise `ToolError` — exception อื่น SDK v2 จะซ่อนข้อความไว้ฝั่ง server
- ทดสอบแล้วบน Windows 11, Python 3.13, mcp 2.3.0, Claude Code 2.1.330 (2026-11-24)
