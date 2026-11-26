"""MessageTrace API and role-checked A/B WebSocket transport."""

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from ..team2_store.store import Store
from ..team2_store.routes import register_delivery_routes, register_event_route
from ..team3_diagnostics.diagnostics import active_fault
from ..team3_diagnostics.routes import register_routes


DB_PATH = os.getenv("MESSAGETRACE_DB", "messagetrace.db")
ACK_TIMEOUT = max(0.1, float(os.getenv("MESSAGETRACE_ACK_TIMEOUT", "8")))
MAX_ATTEMPTS = max(1, int(os.getenv("MESSAGETRACE_MAX_ATTEMPTS", "5")))
store = Store(DB_PATH)
app = FastAPI(title="MessageTrace", version="1.1.0")
peers = {"A": None, "B": None}
peer_locks = {"A": asyncio.Lock(), "B": asyncio.Lock()}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageIn(StrictModel):
    sender: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    body: str = Field(min_length=1, max_length=10000)


async def send_json(ws, data):
    if ws is None:
        return False
    try:
        await ws.send_json(data)
        return True
    except (WebSocketDisconnect, OSError, RuntimeError):
        # Replacement or network loss can close a socket between a receive and reply.
        return False


def _event_for_status(msg, status, fault=None):
    types = {"OFFLINE": "OFFLINE", "TIMEOUT": "TIMEOUT", "OVERLOAD": "OVERLOAD"}
    if status in types:
        store.event(types[status], msg["id"], msg["sender"], msg["recipient"], bool(fault),
                    {"fault_id": fault["id"]} if fault else {})


async def dispatch(msg):
    """Persist delivery state before sending; each retry retains the original ID."""
    current = store.get(msg["id"])
    if not current or current["status"] == "ACKED":
        return "ACKED" if current else "UNKNOWN"
    target = current["recipient"]
    fault = active_fault(store, target)
    if fault:
        if fault["kind"] == "disconnect":
            store.update(current["id"], "OFFLINE", "simulated_disconnect")
            _event_for_status(current, "OFFLINE", fault)
            return "OFFLINE"
        if fault["kind"] == "timeout":
            store.update(current["id"], "TIMEOUT", "simulated_timeout", increment=True)
            _event_for_status(current, "TIMEOUT", fault)
            return "TIMEOUT"
        if fault["kind"] == "overload":
            store.update(current["id"], "RETRYING", "simulated_overload", increment=True)
            _event_for_status(current, "OVERLOAD", fault)
            return "OVERLOAD"
    ws = peers.get(target)
    if ws is None:
        store.update(current["id"], "OFFLINE", "recipient_offline")
        store.event("OFFLINE", current["id"], current["sender"], target)
        return "OFFLINE"
    try:
        attempt = store.update(current["id"], "FORWARDED", increment=True, sent=True)
        store.event("FORWARDED", current["id"], current["sender"], target,
                    details={"attempt": attempt["attempts"]})
        delivered = await send_json(ws, {
            "type": "message", "message_id": current["id"], "sender": current["sender"],
            "recipient": target, "body": current["body"], "created": current["created"],
            "attempt": attempt["attempts"],
        })
        if not delivered:
            store.update(current["id"], "OFFLINE", "socket_closed_during_send")
            store.event("DISCONNECT", current["id"], current["sender"], target,
                        details={"error": "socket_closed_during_send"})
            return "OFFLINE"
        return "FORWARDED"
    except Exception as exc:
        store.update(current["id"], "OFFLINE", f"transport_error:{type(exc).__name__}")
        store.event("DISCONNECT", current["id"], current["sender"], target,
                    details={"error": type(exc).__name__})
        return "OFFLINE"


async def timeout_sweeper():
    """Turn expired forwards into retryable records and redeliver while connected."""
    while True:
        await asyncio.sleep(min(1.0, ACK_TIMEOUT / 2))
        for msg in expire_forwarded():
            if msg["attempts"] >= MAX_ATTEMPTS:
                continue
            if peers.get(msg["recipient"]):
                await dispatch(msg)


def expire_forwarded(now=None):
    """Persist timeout evidence once, then return records the retry worker can handle."""
    import time

    current_time = time.time() if now is None else now
    expired = store.stale_forwarded(current_time - ACK_TIMEOUT)
    retryable = []
    for msg in expired:
        if msg["attempts"] >= MAX_ATTEMPTS:
            store.update(msg["id"], "TIMEOUT", "max_attempts_reached")
            store.event("TIMEOUT", msg["id"], msg["sender"], msg["recipient"],
                        details={"attempts": msg["attempts"], "exhausted": True})
            continue
        changed = store.update(msg["id"], "RETRYING", "ack_timeout")
        store.event("TIMEOUT", msg["id"], msg["sender"], msg["recipient"],
                    details={"timeout_seconds": ACK_TIMEOUT, "attempts": changed["attempts"]})
        retryable.append(changed)
    return retryable


@app.on_event("startup")
async def start_sweeper():
    app.state.timeout_task = asyncio.create_task(timeout_sweeper())


@app.on_event("shutdown")
async def stop_sweeper():
    task = getattr(app.state, "timeout_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "MessageTrace", "connected_roles": [r for r, ws in peers.items() if ws]}


def validate_roles(sender, recipient):
    if sender not in peers or recipient not in peers or sender == recipient:
        raise HTTPException(422, "sender and recipient must be distinct A/B roles")


@app.post("/api/messages")
async def send_message(req: MessageIn):
    validate_roles(req.sender, req.recipient)
    if not req.body.strip():
        raise HTTPException(422, "body must not be blank")
    msg = store.create(req.sender, req.recipient, req.body)
    status = await dispatch(msg)
    return {"message_id": msg["id"], "status": status}


@app.get("/api/messages/{message_id}")
def get_message(message_id: str):
    result = store.query(message_id)
    if not result:
        raise HTTPException(404, "message not found")
    return result


register_routes(app, lambda: store)
register_event_route(app, lambda: store)
register_delivery_routes(app, lambda: store, dispatch, lambda: MAX_ATTEMPTS)


@app.websocket("/ws/{role}")
async def websocket(role: str, ws: WebSocket):
    if role not in peers:
        await ws.close(code=1008)
        return
    await ws.accept()
    async with peer_locks[role]:
        previous = peers[role]
        peers[role] = ws
        if previous and previous is not ws:
            try:
                await previous.close(code=4001, reason="replaced by a newer connection")
            except Exception:
                pass
    store.event("CONNECTED", source=role, target=role, details={"reconnected": previous is not None})
    await send_json(ws, {"type": "connected", "role": role})
    for pending in store.pending(role):
        await dispatch(pending)
    try:
        while True:
            data = await ws.receive_json()
            if not isinstance(data, dict):
                await send_json(ws, {"type": "error", "error": "object_required"})
                continue
            kind = data.get("type")
            if kind == "ack":
                message_id = data.get("message_id")
                ok, reason = store.ack(message_id, role) if isinstance(message_id, str) else (False, "invalid_message_id")
                await send_json(ws, {"type": "ack_result", "message_id": message_id,
                                     "status": "ACKED" if ok else "REJECTED", "reason": reason})
                msg = store.get(message_id) if isinstance(message_id, str) else None
                if msg and ok:
                    await send_json(peers.get(msg["sender"]), {
                        "type": "delivery_status", "message_id": message_id, "status": "ACKED",
                    })
            elif kind == "send":
                sender = data.get("sender", role)
                recipient, body = data.get("recipient"), data.get("body")
                if (sender != role or recipient not in peers or recipient == role or
                        not isinstance(body, str) or not body.strip() or len(body) > 10000):
                    await send_json(ws, {"type": "error", "error": "invalid_message"})
                    continue
                msg = store.create(role, recipient, body)
                status = await dispatch(msg)
                await send_json(ws, {"type": "delivery_status", "message_id": msg["id"], "status": status})
            else:
                await send_json(ws, {"type": "error", "error": "unknown_type"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        async with peer_locks[role]:
            # A stale socket's finally block must never erase its replacement.
            if peers.get(role) is ws:
                peers[role] = None
                store.event("DISCONNECTED", source=role, target=role)


@app.get("/", response_class=HTMLResponse)
def home():
    return Path(__file__).parents[1].joinpath("team4_frontend", "index.html").read_text(encoding="utf8")
