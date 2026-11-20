"""AC1.1 → US-01／T1.1、T1.2：健康檢查、A／B 合法連線、重複與無效角色被拒、既有連線仍可互傳。"""

import pytest
from starlette.websockets import WebSocketDisconnect


def test_health_returns_200(h):
    r = h.client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_a_and_b_each_have_one_connection(h):
    a = h.connect("A")
    b = h.connect("B")
    snap = h.client.get("/api/connections").json()
    assert snap["online"] == {"A": True, "B": True}
    assert snap["connections"]["A"] == a.welcome["conn_id"]
    assert snap["connections"]["B"] == b.welcome["conn_id"]
    assert b.welcome["peers"] == {"A": True, "B": True}


@pytest.mark.parametrize("path,code,close_code", [
    ("/ws/A", "DUPLICATE_ROLE", 4409),
    ("/ws/C", "INVALID_ROLE", 4400),
    ("/ws/a", "INVALID_ROLE", 4400),
])
def test_third_connection_rejected_and_existing_still_work(h, path, code, close_code):
    a = h.connect("A")
    b = h.connect("B")
    third = h.connect_raw(path)
    err = third.receive_json()
    assert err["type"] == "error" and err["code"] == code
    with pytest.raises(WebSocketDisconnect) as exc:
        third.receive_json()
    assert exc.value.code == close_code
    # 連線表沒有被第三條連線改變
    snap = h.client.get("/api/connections").json()["connections"]
    assert snap == {"A": a.welcome["conn_id"], "B": b.welcome["conn_id"]}
    events = h.client.get("/api/events", params={"stage": "CONNECTION_REJECTED"}).json()["events"]
    assert events and code in events[-1]["reason"]
    # 既有連線仍可互傳
    a.send({"type": "message", "message_id": "M001", "to": "B", "text": "still ok"})
    a.sync()
    got = b.sync()
    assert [m["message_id"] for m in b.of("message", got)] == ["M001"]
    b.send({"type": "message", "message_id": "M002", "to": "A", "text": "yes"})
    b.sync()
    assert [m["text"] for m in a.of("message", a.sync())] == ["yes"]


def test_disconnect_cleans_connection_table(h):
    a = h.connect("A")
    b = h.connect("B")
    b.close()
    h.wait_until(lambda: not h.comm.connections.is_online("B"))
    assert h.client.get("/api/connections").json()["online"] == {"A": True, "B": False}
    presence = [m for m in a.sync() if m["type"] == "presence"]
    assert presence and presence[-1] == {"type": "presence", "role": "B", "online": False, "reason": "離線"}
    # 角色釋出後可再次連線
    b2 = h.connect("B")
    assert h.comm.connections.get("B").conn_id == b2.welcome["conn_id"]
