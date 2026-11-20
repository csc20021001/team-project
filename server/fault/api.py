"""組員三：故障注入／診斷 HTTP API（T-01-04、T-03-03、T-04-03）。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Path, Query, Request
from fastapi.responses import JSONResponse

from .rules import rule_table
from .simulator import TARGET_RE, FaultValidationError
from .suggestions import SUGGESTIONS, suggestion_for

router = APIRouter(prefix="/api", tags=["fault"])

TARGET_PATTERN = TARGET_RE.pattern
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def _bad(e: FaultValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "INVALID_FAULT_REQUEST", "field": e.field,
                                                  "detail": e.detail})


@router.post("/faults", status_code=201)
async def inject_fault(request: Request, payload: Any = Body(...)):
    sim = request.app.state.faults
    try:
        result = sim.enable(payload)
    except FaultValidationError as e:
        return _bad(e)
    await sim.notify("enable", result["target_id"], result["fault_type"])
    return result


@router.delete("/faults/{target_id}")
async def clear_fault(request: Request, target_id: str = Path(..., pattern=TARGET_PATTERN),
                      fault_type: Optional[str] = Query(None)):
    sim = request.app.state.faults
    try:
        result = sim.clear(target_id, fault_type)
    except FaultValidationError as e:
        return _bad(e)
    for t in result["cleared"]:
        await sim.notify("clear", target_id, t)
    return result


@router.get("/faults")
async def list_faults(request: Request):
    return {"faults": request.app.state.faults.all_status()}


@router.get("/faults/{target_id}")
async def fault_status(request: Request, target_id: str = Path(..., pattern=TARGET_PATTERN)):
    return request.app.state.faults.status(target_id)


@router.get("/rules")
async def rules():
    return {"rules": rule_table()}


@router.post("/diagnosis")
async def run_diagnosis(request: Request, payload: Any = Body(...)):
    target = payload.get("target_id") if isinstance(payload, dict) else None
    if not isinstance(target, str) or not TARGET_RE.match(target):
        return JSONResponse(status_code=400, content={"error": "INVALID_TARGET", "detail": "target_id 缺少或格式錯誤"})
    return request.app.state.diagnosis.diagnose(target)


@router.get("/diagnosis/{diagnosis_id}")
async def get_diagnosis(request: Request, diagnosis_id: str = Path(..., pattern=ID_PATTERN)):
    d = request.app.state.diagnosis.get(diagnosis_id)
    if d is None:
        return JSONResponse(status_code=404, content={"error": "NOT_FOUND", "detail": "查無此診斷"})
    return d


@router.get("/diagnosis/{diagnosis_id}/evidence")
async def get_evidence(request: Request, diagnosis_id: str = Path(..., pattern=ID_PATTERN)):
    d = request.app.state.diagnosis.evidence(diagnosis_id)
    if d is None:
        return JSONResponse(status_code=404, content={"error": "NOT_FOUND", "detail": "查無此診斷"})
    return d


@router.get("/suggestions")
async def list_suggestions():
    return {"suggestions": [suggestion_for(c) for c in SUGGESTIONS]}


@router.get("/suggestions/{fault_code}")
async def get_suggestion(fault_code: str = Path(..., pattern=r"^[A-Z_]{1,40}$")):
    s = suggestion_for(fault_code)
    if s is None:
        return JSONResponse(status_code=404, content={"error": "NOT_FOUND", "detail": "查無此故障代碼"})
    return s


@router.post("/suggestions/{fault_code}/confirm")
async def confirm_suggestion(request: Request, fault_code: str = Path(..., pattern=r"^[A-Z_]{1,40}$"),
                             payload: Any = Body(...)):
    target = payload.get("target_id") if isinstance(payload, dict) else None
    if not isinstance(target, str) or not TARGET_RE.match(target):
        return JSONResponse(status_code=400, content={"error": "INVALID_TARGET", "detail": "target_id 缺少或格式錯誤"})
    r = request.app.state.diagnosis.confirm_suggestion(fault_code, target)
    if r is None:
        return JSONResponse(status_code=404, content={"error": "NOT_FOUND", "detail": "查無此故障代碼"})
    return r
