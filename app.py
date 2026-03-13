"""Sauna Session Tracker — FastAPI backend.

Tracks sauna presence via Aqara FP2 webhook/polling and manages
multi-interval sessions with automatic break detection.
"""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("SAUNA_DB", "sauna.db")
BREAK_TIMEOUT_SEC = int(os.getenv("SAUNA_BREAK_TIMEOUT", "600"))  # 10 min

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    intervals   TEXT NOT NULL DEFAULT '[]',
    total_in    REAL NOT NULL DEFAULT 0
);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


# ---------------------------------------------------------------------------
# Session state (in-memory, persisted to SQLite on every transition)
# ---------------------------------------------------------------------------
class SessionState:
    def __init__(self):
        self.active_session_id: int | None = None
        self.intervals: list[dict] = []  # [{type, start, end, duration}, ...]
        self.phase: str = "idle"  # idle | in_sauna | on_break
        self.phase_start: float = 0.0
        self.lock = asyncio.Lock()
        self._break_task: asyncio.Task | None = None

    # -- public helpers ----------------------------------------------------
    def snapshot(self) -> dict:
        """Return JSON-safe snapshot for the frontend."""
        now = time.time()
        elapsed = now - self.phase_start if self.phase != "idle" else 0
        total_in = sum(
            i["duration"] for i in self.intervals if i["type"] == "in_sauna"
        )
        if self.phase == "in_sauna":
            total_in += elapsed
        return {
            "phase": self.phase,
            "phase_elapsed": round(elapsed, 1),
            "session_id": self.active_session_id,
            "intervals": self.intervals,
            "total_in": round(total_in, 1),
            "round": sum(1 for i in self.intervals if i["type"] == "in_sauna")
            + (1 if self.phase == "in_sauna" else 0),
            "started_at": (
                self.intervals[0]["start"] if self.intervals else None
            ),
        }

    # -- transitions -------------------------------------------------------
    async def presence_detected(self):
        async with self.lock:
            if self._break_task and not self._break_task.done():
                self._break_task.cancel()
                self._break_task = None

            now = time.time()

            if self.phase == "idle":
                # Start a brand-new session
                self.intervals = []
                self.phase = "in_sauna"
                self.phase_start = now
                async with await get_db() as db:
                    cur = await db.execute(
                        "INSERT INTO sessions (started_at, intervals) VALUES (?, ?)",
                        (_iso(now), "[]"),
                    )
                    self.active_session_id = cur.lastrowid
                    await db.commit()

            elif self.phase == "on_break":
                # Finish break interval, start new in-sauna round
                self._close_interval(now, "break")
                self.phase = "in_sauna"
                self.phase_start = now
                await self._persist()

    async def presence_lost(self):
        async with self.lock:
            if self.phase != "in_sauna":
                return
            now = time.time()
            self._close_interval(now, "in_sauna")
            self.phase = "on_break"
            self.phase_start = now
            await self._persist()
            # Schedule auto-close
            self._break_task = asyncio.create_task(self._auto_close())

    async def _auto_close(self):
        """Close the session if break exceeds BREAK_TIMEOUT_SEC."""
        try:
            await asyncio.sleep(BREAK_TIMEOUT_SEC)
        except asyncio.CancelledError:
            return
        async with self.lock:
            if self.phase != "on_break":
                return
            # Do NOT count the final break — just close
            self.phase = "idle"
            self.phase_start = 0.0
            await self._persist(close=True)

    # -- internal helpers --------------------------------------------------
    def _close_interval(self, now: float, interval_type: str):
        duration = round(now - self.phase_start, 1)
        self.intervals.append(
            {
                "type": interval_type,
                "start": _iso(self.phase_start),
                "end": _iso(now),
                "duration": duration,
            }
        )

    async def _persist(self, close: bool = False):
        total_in = sum(
            i["duration"] for i in self.intervals if i["type"] == "in_sauna"
        )
        async with await get_db() as db:
            if close:
                await db.execute(
                    """UPDATE sessions
                       SET intervals = ?, total_in = ?, ended_at = ?
                       WHERE id = ?""",
                    (
                        json.dumps(self.intervals),
                        total_in,
                        _iso(time.time()),
                        self.active_session_id,
                    ),
                )
                self.active_session_id = None
                self.intervals = []
            else:
                await db.execute(
                    "UPDATE sessions SET intervals = ?, total_in = ? WHERE id = ?",
                    (
                        json.dumps(self.intervals),
                        total_in,
                        self.active_session_id,
                    ),
                )
            await db.commit()


state = SessionState()

# ---------------------------------------------------------------------------
# WebSocket hub (push live state to all connected browsers)
# ---------------------------------------------------------------------------
connected_clients: set[WebSocket] = set()


async def broadcast():
    snapshot = state.snapshot()
    msg = json.dumps(snapshot)
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)


async def ticker():
    """Push state to all clients every second while a session is active."""
    while True:
        if state.phase != "idle" and connected_clients:
            await broadcast()
        await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with await get_db() as db:
        await db.executescript(CREATE_TABLES)
        await db.commit()
    # Recover any session that was left open (e.g. after a crash)
    await _recover_open_session()
    task = asyncio.create_task(ticker())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


async def _recover_open_session():
    """If the server restarts while a session is open, close it."""
    async with await get_db() as db:
        cur = await db.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        if row:
            await db.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (_iso(time.time()), row["id"]),
            )
            await db.commit()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse("static/index.html")


@app.post("/api/presence")
async def presence_webhook(body: dict | None = None):
    """Called by the Aqara integration when presence state changes.

    Accepts: {"presence": true/false}
    """
    if body and body.get("presence"):
        await state.presence_detected()
    else:
        await state.presence_lost()
    await broadcast()
    return {"ok": True, "state": state.snapshot()}


@app.get("/api/state")
async def get_state():
    return state.snapshot()


@app.get("/api/sessions")
async def list_sessions(limit: int = 50, offset: int = 0):
    async with await get_db() as db:
        cur = await db.execute(
            "SELECT * FROM sessions WHERE ended_at IS NOT NULL ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "intervals": json.loads(r["intervals"]),
                "total_in": r["total_in"],
            }
            for r in rows
        ]


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: int):
    async with await get_db() as db:
        cur = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        r = await cur.fetchone()
        if not r:
            return {"error": "not found"}
        return {
            "id": r["id"],
            "started_at": r["started_at"],
            "ended_at": r["ended_at"],
            "intervals": json.loads(r["intervals"]),
            "total_in": r["total_in"],
        }


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: int):
    async with await get_db() as db:
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()
    return {"ok": True}


# -- Manual controls (handy for testing without the sensor) ----------------
@app.post("/api/simulate/enter")
async def simulate_enter():
    await state.presence_detected()
    await broadcast()
    return {"ok": True, "state": state.snapshot()}


@app.post("/api/simulate/leave")
async def simulate_leave():
    await state.presence_lost()
    await broadcast()
    return {"ok": True, "state": state.snapshot()}


# -- WebSocket for live updates -------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    try:
        await ws.send_text(json.dumps(state.snapshot()))
        while True:
            await ws.receive_text()  # keep connection alive
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(ws)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
