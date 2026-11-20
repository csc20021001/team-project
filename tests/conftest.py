"""共用測試工具：每個測試使用獨立的記憶體 SQLite 與全新的 App。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.main import create_app  # noqa: E402


class Peer:
    """包裝一條測試用 WebSocket；sync() 以 ping/pong 確認伺服器已處理完先前的訊框。"""

    def __init__(self, ws, role: str):
        self.ws = ws
        self.role = role
        self.inbox: list[dict] = []

    def send(self, obj) -> None:
        self.ws.send_text(obj if isinstance(obj, str) else json.dumps(obj))

    def sync(self) -> list[dict]:
        self.ws.send_text('{"type":"ping"}')
        got = []
        while True:
            m = self.ws.receive_json()
            if m.get("type") == "pong":
                break
            got.append(m)
        self.inbox.extend(got)
        return got

    def of(self, type_: str, frames=None) -> list[dict]:
        return [m for m in (self.inbox if frames is None else frames) if m.get("type") == type_]

    def statuses(self, message_id: str) -> list[str]:
        return [m["status"] for m in self.inbox if m.get("type") == "status" and m["message_id"] == message_id]

    def close(self) -> None:
        self.ws.close()


class Harness:
    def __init__(self, client: TestClient):
        self.client = client
        self.app = client.app
        self.storage = self.app.state.storage
        self.comm = self.app.state.comm
        self.faults = self.app.state.faults
        self._cms = []

    def connect(self, role: str, resume: str | None = None) -> Peer:
        url = f"/ws/{role}" + (f"?resume={resume}" if resume else "")
        cm = self.client.websocket_connect(url)
        ws = cm.__enter__()
        self._cms.append(cm)
        peer = Peer(ws, role)
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome", welcome
        peer.welcome = welcome
        peer.sync()
        return peer

    def connect_raw(self, path: str):
        cm = self.client.websocket_connect(path)
        ws = cm.__enter__()
        self._cms.append(cm)
        return ws

    def call(self, fn, *args):
        """在 App 的事件迴圈中執行 async 函式（例如重送檢查）。"""
        return self.client.portal.call(fn, *args)

    def wait_until(self, cond, timeout: float = 3.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return
            time.sleep(0.01)
        raise AssertionError("condition not met in time")

    def stages(self, message_id: str) -> list[str]:
        return [e["stage"] for e in self.storage.get_events(message_id)]

    def close_all(self) -> None:
        for cm in reversed(self._cms):
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass


@pytest.fixture
def h():
    app = create_app(":memory:", ack_timeout=5.0, max_attempts=3, resend_interval=0)
    with TestClient(app) as client:
        harness = Harness(client)
        try:
            yield harness
        finally:
            harness.close_all()
