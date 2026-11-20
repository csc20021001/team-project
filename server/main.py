"""組員一 T1.1：FastAPI 服務進入點。

啟動（Windows PowerShell）：
    .venv\\Scripts\\python -m uvicorn server.main:app --host 127.0.0.1 --port 8000

環境變數：
    MT_DB_PATH        SQLite 檔案路徑（預設 data/messagetrace.db）
    MT_ACK_TIMEOUT    ACK 逾時秒數（預設 5）
    MT_MAX_ATTEMPTS   最多轉送次數（含第一次，預設 3）
    MT_RESEND_INTERVAL 逾時檢查間隔秒數（預設 1；0 表示關閉背景重送）
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.comm import CommService
from server.comm.api import router as comm_router
from server.fault import DiagnosisService, FaultSimulator
from server.fault.api import router as fault_router
from server.storage import Storage

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
log = logging.getLogger("messagetrace")


def create_app(
    db_path: Optional[str] = None,
    *,
    ack_timeout: Optional[float] = None,
    max_attempts: Optional[int] = None,
    resend_interval: Optional[float] = None,
) -> FastAPI:
    if db_path is None:
        db_path = os.environ.get("MT_DB_PATH", "data/messagetrace.db")
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    ack_timeout = float(os.environ.get("MT_ACK_TIMEOUT", 5)) if ack_timeout is None else ack_timeout
    max_attempts = int(os.environ.get("MT_MAX_ATTEMPTS", 3)) if max_attempts is None else max_attempts
    resend_interval = (float(os.environ.get("MT_RESEND_INTERVAL", 1))
                       if resend_interval is None else resend_interval)

    storage = Storage(db_path)
    faults = FaultSimulator(storage)
    comm = CommService(storage, faults, ack_timeout=ack_timeout, max_attempts=max_attempts)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(comm.resend_loop(resend_interval)) if resend_interval > 0 else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="MessageTrace 訊息追蹤系統", version="1.0.0", lifespan=lifespan)
    app.state.storage = storage
    app.state.faults = faults
    app.state.comm = comm
    app.state.diagnosis = DiagnosisService(storage)

    # 錯誤回應一律為精簡 JSON，不洩漏程式堆疊（AC3.1）
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = [".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": "INVALID_PARAMETER", "fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": "HTTP_ERROR", "detail": str(exc.detail)})

    @app.exception_handler(Exception)
    async def _internal(_: Request, exc: Exception):
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"error": "INTERNAL_ERROR", "detail": "伺服器內部錯誤"})

    app.include_router(comm_router)
    app.include_router(fault_router)
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


_app: Optional[FastAPI] = None


def __getattr__(name: str):
    """`uvicorn server.main:app` 取用時才建立應用程式（避免測試匯入時建立資料檔）。"""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
