"""組員一 T1.1／T3.2：健康檢查與依 Message ID 的唯讀查詢 API。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Path, Query, Request, WebSocket
from fastapi.responses import JSONResponse

from .contract import ID_RE

router = APIRouter(tags=["comm"])

ID_PATTERN = ID_RE.pattern


@router.get("/api/health")
async def health(request: Request):
    svc = request.app.state.comm
    return {"status": "ok", "service": "MessageTrace", "connections": svc.connections.peers()}


@router.get("/api/connections")
async def connections(request: Request):
    svc = request.app.state.comm
    return {"connections": svc.connections.snapshot(), "online": svc.connections.peers()}


@router.get("/api/messages/{message_id}")
async def get_message(request: Request, message_id: str = Path(..., pattern=ID_PATTERN)):
    storage = request.app.state.storage
    msg = storage.get_message(message_id)
    events = storage.get_events(message_id)
    if msg is None and not events:
        return JSONResponse(status_code=404, content={"error": "NOT_FOUND", "detail": f"查無 Message ID：{message_id}"})
    return {"message_id": message_id, "message": msg, "events": events}


@router.get("/api/messages")
async def list_messages(request: Request, role: Optional[str] = Query(None, pattern=r"^[AB]$"),
                        limit: int = Query(50, ge=1, le=200)):
    return {"messages": request.app.state.storage.list_messages(role=role, limit=limit)}


@router.get("/api/events")
async def list_events(request: Request, role: Optional[str] = Query(None, pattern=r"^[AB]$"),
                      stage: Optional[str] = Query(None, pattern=r"^[A-Z_]{1,40}$"),
                      target_id: Optional[str] = Query(None, pattern=r"^[A-Za-z0-9_-]{1,32}$"),
                      limit: int = Query(100, ge=1, le=500)):
    stages = [stage] if stage else None
    return {"events": request.app.state.storage.list_events(stages=stages, role=role, target_id=target_id,
                                                            limit=limit)}


@router.websocket("/ws/{role}")
async def ws_endpoint(websocket: WebSocket, role: str, resume: Optional[str] = None):
    await websocket.app.state.comm.handle_websocket(websocket, role, resume)
