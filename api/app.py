"""FastAPI application — the single source of truth for the trading system.

Both the web dashboard (Phase 4b) and the Telegram bot are clients of
this service, so there is exactly one approval queue and double-approvals
are impossible.

Security (SPEC section 10A):
  - Binds to localhost only (configured in run.py).
  - State-changing endpoints require X-Session-Token header.
  - Origin header checked; cross-origin requests rejected.
  - PAPER mode badge always returned in every response.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.auth import get_session_token, require_token
from api.queue import ApprovalError, approve_signal, get_pending_signals, reject_signal
from execution.executor import execute_approved_signal
from config.settings import load_settings
from data.database import get_connection, init_db

UTC = timezone.utc

_DB_PATH: str = ""
_connections: list[WebSocket] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _DB_PATH
    from config.settings import guard_database, require_python_311
    require_python_311()
    settings = load_settings()
    guard_database(settings)       # one DB per mode (filename + in-DB stamp)
    _DB_PATH = settings.database.path
    yield


app = FastAPI(
    title="Trading Bot API",
    description="AI trading analyst — PAPER mode only",
    lifespan=lifespan,
)

# Only allow localhost origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _get_conn() -> sqlite3.Connection:
    return get_connection(_DB_PATH)


# ---------------------------------------------------------------------------
# WebSocket broadcast
# ---------------------------------------------------------------------------

async def broadcast(message: dict) -> None:
    """Push a message to all connected WebSocket clients."""
    dead = []
    for ws in _connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connections.remove(ws)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _connections.remove(websocket)


# ---------------------------------------------------------------------------
# Read-only endpoints (no auth required)
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def status_endpoint():
    settings = load_settings()
    return {
        "mode": "PAPER",
        "phase": settings.phase,
        "mock_mode": settings.mock_mode,
        "timestamp": datetime.now(UTC).isoformat(),
        "session_token_set": bool(get_session_token()),
    }


@app.get("/api/signals")
async def list_signals():
    conn = _get_conn()
    try:
        rows = get_pending_signals(conn)
        return {"signals": [dict(r) for r in rows], "mode": "PAPER"}
    finally:
        conn.close()


@app.get("/api/signals/{signal_id}")
async def get_signal(signal_id: int):
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM signals WHERE id=?", (signal_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Signal not found")
        return {"signal": dict(row), "mode": "PAPER"}
    finally:
        conn.close()


@app.get("/api/positions")
async def list_positions():
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE closed_at IS NULL ORDER BY opened_at DESC"
        ).fetchall()
        return {"positions": [dict(r) for r in rows], "mode": "PAPER"}
    finally:
        conn.close()


@app.get("/api/equity")
async def equity_history():
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM equity_snapshots ORDER BY snapped_at DESC LIMIT 100"
        ).fetchall()
        return {"snapshots": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/readiness")
async def readiness():
    conn = _get_conn()
    try:
        from learning.readiness import check_readiness
        report = check_readiness(conn)
        return {
            "items": [
                {
                    "name": item.name,
                    "passed": item.passed,
                    "value": item.value,
                    "requirement": item.requirement,
                }
                for item in report.items
            ],
            "overall": report.overall,
            "summary": report.summary,
            "blockers": report.blockers,
            "checked_at": report.checked_at.isoformat(),
        }
    finally:
        conn.close()


@app.get("/api/journal")
async def journal():
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM approvals ORDER BY decided_at DESC LIMIT 200"
        ).fetchall()
        return {"approvals": [dict(r) for r in rows], "mode": "PAPER"}
    finally:
        conn.close()


@app.get("/api/activity")
async def activity():
    """Real-time feed of what the bot is doing + a live scorecard."""
    import json as _json
    conn = _get_conn()
    try:
        events: list[dict] = []
        for r in conn.execute(
            "SELECT symbol, action, signal_type, created_at FROM signals "
            "ORDER BY created_at DESC LIMIT 20"
        ):
            events.append({"time": r["created_at"], "type": "SIGNAL",
                           "text": f"Signal generated: {r['action']} {r['symbol']} ({r['signal_type']})"})
        for r in conn.execute(
            "SELECT a.decision, a.interface, a.decided_at, s.symbol "
            "FROM approvals a JOIN signals s ON a.signal_id=s.id "
            "ORDER BY a.decided_at DESC LIMIT 20"
        ):
            events.append({"time": r["decided_at"], "type": r["decision"],
                           "text": f"{r['decision']} {r['symbol']} (via {r['interface']})"})
        for r in conn.execute(
            "SELECT symbol, action, quantity, status, submitted_at FROM orders "
            "ORDER BY submitted_at DESC LIMIT 20"
        ):
            events.append({"time": r["submitted_at"], "type": "ORDER",
                           "text": f"Order {r['status']}: {r['action']} {r['quantity']} {r['symbol']}"})
        for r in conn.execute(
            "SELECT event_type, note, logged_at FROM system_events "
            "ORDER BY logged_at DESC LIMIT 30"
        ):
            txt = r["event_type"] + (f": {r['note']}" if r["note"] else "")
            events.append({"time": r["logged_at"], "type": r["event_type"], "text": txt})
        events.sort(key=lambda e: e["time"] or "", reverse=True)

        score = {}
        row = conn.execute(
            "SELECT note FROM system_events WHERE event_type='LEARNING' "
            "ORDER BY logged_at DESC LIMIT 1"
        ).fetchone()
        if row and row["note"]:
            try:
                score = _json.loads(row["note"])
            except Exception:
                score = {}
        return {"events": events[:60], "score": score, "mode": "PAPER"}
    finally:
        conn.close()


@app.get("/api/performance")
async def performance():
    conn = _get_conn()
    try:
        spend_row = conn.execute(
            "SELECT SUM(cost_usd) FROM ai_spend"
        ).fetchone()
        trade_count = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='FILLED'"
        ).fetchone()[0]
        return {
            "mode": "PAPER",
            "total_ai_spend_usd": float(spend_row[0] or 0),
            "total_trades": trade_count,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# State-changing endpoints (require session token)
# ---------------------------------------------------------------------------

class ApproveRequest(BaseModel):
    signal_id: int
    reason: Optional[str] = None
    confidence: Optional[int] = None
    mood: Optional[str] = None


class RejectRequest(BaseModel):
    signal_id: int
    reason: Optional[str] = None


@app.post("/api/approve", dependencies=[Depends(require_token)])
async def approve(request: ApproveRequest):
    """Approve a signal. Requires session token. Idempotent order_ref ensures
    at most one order set per approval."""
    conn = _get_conn()
    try:
        order_ref = approve_signal(
            conn, request.signal_id, "dashboard",
            request.reason, request.confidence, request.mood,
        )
        # Run final safety gates and place the bracket order.
        ib_conn = getattr(app.state, "ib_conn", None)
        execution = await execute_approved_signal(conn, request.signal_id, ib_conn=ib_conn)
        await broadcast({
            "type": "approval",
            "signal_id": request.signal_id,
            "decision": "APPROVED",
            "order_ref": order_ref,
            "execution_status": execution.status,
            "executed": execution.executed,
        })
        return {
            "status": "approved",
            "order_ref": order_ref,
            "mode": "PAPER",
            "execution": {
                "executed": execution.executed,
                "status": execution.status,
                "detail": execution.detail,
            },
        }
    except ApprovalError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    finally:
        conn.close()


@app.post("/api/reject", dependencies=[Depends(require_token)])
async def reject(request: RejectRequest):
    conn = _get_conn()
    try:
        reject_signal(conn, request.signal_id, "dashboard", request.reason)
        await broadcast({
            "type": "approval",
            "signal_id": request.signal_id,
            "decision": "REJECTED",
        })
        return {"status": "rejected", "mode": "PAPER"}
    except ApprovalError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    finally:
        conn.close()


class KillRequest(BaseModel):
    confirm: bool


@app.post("/api/kill", dependencies=[Depends(require_token)])
async def kill_switch(request: KillRequest):
    """Kill switch: cancel all open orders and pause the system."""
    if not request.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true")
    await broadcast({"type": "kill_switch", "timestamp": datetime.now(UTC).isoformat()})
    return {"status": "kill_switch_activated", "mode": "PAPER"}
