"""AC3.2 → US-03／T3.3：依 README 指令實際啟動 uvicorn，以真實 WebSocket 重現
正常互傳、斷線重連與 ACK 缺少流程，並以 Message ID／連線事件核對結果。"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

websockets = pytest.importorskip("websockets")

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    db = tmp_path_factory.mktemp("db") / "it.db"
    env = dict(os.environ, MT_DB_PATH=str(db), MT_ACK_TIMEOUT="0.5", MT_RESEND_INTERVAL="0.1",
               MT_MAX_ATTEMPTS="2")
    # 與 README 相同的啟動指令
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
                if r.status == 200:
                    break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield base, f"ws://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


async def _recv_until(ws, pred, timeout=5.0):
    end = time.time() + timeout
    seen = []
    while time.time() < end:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=end - time.time()))
        except asyncio.TimeoutError:
            break
        seen.append(m)
        if pred(m):
            return m, seen
    raise AssertionError(f"expected frame not received; got {seen}")


def test_pages_are_served(server):
    base, _ = server
    for path in ("/", "/client.html", "/ops.html", "/js/core.js", "/js/client.js", "/js/ops.js"):
        with urllib.request.urlopen(base + path, timeout=5) as r:
            assert r.status == 200


def test_end_to_end_scenarios(server):
    base, wsbase = server

    async def run():
        a = await websockets.connect(wsbase + "/ws/A")
        b = await websockets.connect(wsbase + "/ws/B")
        assert json.loads(await a.recv())["type"] == "welcome"
        assert json.loads(await b.recv())["type"] == "welcome"

        # 1) 正常互傳 + ACK → DELIVERED
        await a.send(json.dumps({"type": "message", "message_id": "IT-1", "to": "B", "text": "hello"}))
        m, _ = await _recv_until(b, lambda f: f.get("type") == "message")
        assert (m["message_id"], m["text"], m["from"]) == ("IT-1", "hello", "A")
        await b.send(json.dumps({"type": "ack", "message_id": "IT-1"}))
        await _recv_until(a, lambda f: f.get("type") == "status" and f["message_id"] == "IT-1"
                          and f["status"] == "DELIVERED")

        # 2) ACK 缺少：B 不回 ACK → 逾時重送（原 ID）→ FAILED，從未 DELIVERED
        await a.send(json.dumps({"type": "message", "message_id": "IT-2", "to": "B", "text": "no ack"}))
        _, seen = await _recv_until(a, lambda f: f.get("type") == "status" and f["message_id"] == "IT-2"
                                    and f["status"] == "FAILED", timeout=8)
        assert "DELIVERED" not in [f.get("status") for f in seen if f.get("message_id") == "IT-2"]

        # 3) 斷線重連：B 斷線，A 傳 IT-3，B 重連後以原 ID 補送
        await b.close()
        await _recv_until(a, lambda f: f.get("type") == "presence" and f["role"] == "B" and not f["online"])
        await a.send(json.dumps({"type": "message", "message_id": "IT-3", "to": "B", "text": "later"}))
        await _recv_until(a, lambda f: f.get("type") == "status" and f["message_id"] == "IT-3"
                          and f["status"] == "QUEUED")
        b = await websockets.connect(wsbase + "/ws/B")
        m, _ = await _recv_until(b, lambda f: f.get("type") == "message" and f["message_id"] == "IT-3")
        assert m["redelivery"] is True
        await b.send(json.dumps({"type": "ack", "message_id": "IT-3"}))
        await _recv_until(a, lambda f: f.get("type") == "status" and f["message_id"] == "IT-3"
                          and f["status"] == "DELIVERED")
        await a.close()
        await b.close()

    asyncio.run(run())

    # 以 Message ID 核對
    _, it1 = _get(base + "/api/messages/IT-1")
    assert [e["stage"] for e in it1["events"]] == ["RECEIVED", "FORWARDED", "DELIVERED"]
    _, it2 = _get(base + "/api/messages/IT-2")
    assert it2["message"]["status"] == "FAILED"
    assert "RESENT" in [e["stage"] for e in it2["events"]]
    _, it3 = _get(base + "/api/messages/IT-3")
    assert [e["stage"] for e in it3["events"]][:2] == ["RECEIVED", "QUEUED"]
    assert it3["message"]["status"] == "DELIVERED"
    # 以連線事件核對
    _, ev = _get(base + "/api/events?role=B&stage=DISCONNECTED")
    assert ev["events"]
    code, _ = _get(base + "/api/messages/NOPE-404")
    assert code == 404
