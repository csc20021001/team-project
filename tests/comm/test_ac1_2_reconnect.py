"""AC1.2 → US-01／T1.3：B 離線時 A 傳 M101，B 重連後以原 ID 補送，收到有效 ACK 才通知送達。"""

import pytest
from starlette.websockets import WebSocketDisconnect

from server.comm.connections import ConnectionTable


def test_offline_then_reconnect_redelivers_with_original_id(h):
    a = h.connect("A")
    b = h.connect("B")
    b.close()
    h.wait_until(lambda: not h.comm.connections.is_online("B"))
    a.sync()

    # 舊連線已從連線表移除
    assert h.comm.connections.get("B") is None

    a.send({"type": "message", "message_id": "M101", "to": "B", "text": "while offline"})
    a.sync()
    assert a.statuses("M101") == ["RECEIVED", "QUEUED"]
    assert h.storage.get_message("M101")["status"] == "QUEUED"
    queued = [e for e in h.storage.get_events("M101") if e["stage"] == "QUEUED"]
    assert "離線" in queued[0]["reason"]

    # B 重連：新連線有效，通訊層呼叫補送入口，以原 ID 傳送
    b2 = h.connect("B")
    assert h.comm.connections.get("B").conn_id == b2.welcome["conn_id"]
    redelivered = b2.of("message")
    assert [(m["message_id"], m["text"], m["redelivery"]) for m in redelivered] == [("M101", "while offline", True)]
    a.sync()
    assert any(m["type"] == "presence" and m["role"] == "B" and m["online"] for m in a.inbox)
    assert "DELIVERED" not in a.statuses("M101")
    assert a.statuses("M101")[-1] == "FORWARDED"

    # 收到有效 ACK 後才通知送達
    b2.send({"type": "ack", "message_id": "M101"})
    b2.sync()
    a.sync()
    assert a.statuses("M101")[-1] == "DELIVERED"
    assert h.stages("M101") == ["RECEIVED", "QUEUED", "REDELIVERED", "DELIVERED"]


def test_stale_close_does_not_remove_new_connection_table_level():
    table = ConnectionTable()
    old = table.register("B", object())
    new = table.register("B", object(), resume_token=old.resume_token)
    assert new.ok and new.replaced is old.conn
    # 舊連線晚到的斷線清理：不得移除新連線
    assert table.unregister("B", old.conn.conn_id) is False
    assert table.get("B") is new.conn
    assert table.unregister("B", new.conn.conn_id) is True
    assert table.get("B") is None


def test_resume_replaces_old_connection_and_old_close_is_ignored(h):
    a = h.connect("A")
    b = h.connect("B")
    token = b.welcome["resume_token"]
    old_conn_id = b.welcome["conn_id"]
    # 同一頁面帶 resume_token 重連：取代舊連線
    b2 = h.connect("B", resume=token)
    assert b2.welcome["conn_id"] != old_conn_id
    # 舊連線被伺服器以 4000 關閉；之後舊連線的斷線清理晚到
    with pytest.raises(WebSocketDisconnect) as exc:
        b.ws.receive_json()
    assert exc.value.code == 4000
    b.ws.close()
    h.wait_until(lambda: any(e["stage"] == "STALE_CLOSE_IGNORED"
                             for e in h.storage.list_events(role="B")))
    assert h.comm.connections.get("B").conn_id == b2.welcome["conn_id"]
    a.send({"type": "message", "message_id": "M102", "to": "B", "text": "to new conn"})
    a.sync()
    assert [m["message_id"] for m in b2.of("message", b2.sync())] == ["M102"]


def test_wrong_resume_token_is_rejected_as_duplicate(h):
    h.connect("B")
    ws = h.connect_raw("/ws/B?resume=not-the-token")
    assert ws.receive_json()["code"] == "DUPLICATE_ROLE"
