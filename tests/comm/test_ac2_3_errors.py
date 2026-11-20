"""AC2.3 → US-02／T2.1、T2.2：格式錯誤回傳明確原因且不轉送；伺服器拒收留下原因；之後仍可傳送。"""

import pytest


@pytest.mark.parametrize("frame,code", [
    ("{not json", "INVALID_JSON"),
    ('["array"]', "INVALID_FORMAT"),
    ({"type": "message", "message_id": "M210", "text": "no target"}, "MISSING_FIELD"),
    ({"type": "message", "message_id": "M211", "to": "C", "text": "bad target"}, "INVALID_ROLE"),
    ({"type": "message", "message_id": "M212", "to": "A", "text": "to self"}, "INVALID_TARGET"),
    ({"type": "message", "message_id": "M213", "to": "B"}, "MISSING_FIELD"),
    ({"type": "message", "message_id": "M214", "to": "B", "text": "   "}, "INVALID_FIELD"),
    ({"type": "message", "message_id": "bad id!", "to": "B", "text": "x"}, "INVALID_FIELD"),
    ({"type": "teleport"}, "INVALID_TYPE"),
    ({"type": "ack"}, "MISSING_FIELD"),
])
def test_invalid_frames_get_clear_error_and_are_not_forwarded(h, frame, code):
    a = h.connect("A")
    b = h.connect("B")
    a.send(frame)
    errs = a.of("error", a.sync())
    assert len(errs) == 1
    assert errs[0]["code"] == code and errs[0]["detail"]
    assert b.of("message", b.sync()) == []
    invalid = h.client.get("/api/events", params={"stage": "INVALID"}).json()["events"]
    assert code in invalid[-1]["reason"]
    # 服務仍可接受下一筆合法訊息
    a.send({"type": "message", "message_id": "M220", "to": "B", "text": "next ok"})
    a.sync()
    assert [m["message_id"] for m in b.of("message", b.sync())] == ["M220"]


def test_from_mismatch(h):
    a = h.connect("A")
    h.connect("B")
    a.send({"type": "message", "from": "X", "to": "B", "text": "spoof"})
    assert a.of("error", a.sync())[0]["code"] == "FROM_MISMATCH"


def test_server_rejection_by_fault_scenario(h):
    a = h.connect("A")
    b = h.connect("B")
    r = h.client.post("/api/faults", json={"target_id": "SERVER", "fault_type": "resource_overload",
                                           "params": {"usage_pct": 97}})
    assert r.status_code == 201
    a.send({"type": "message", "message_id": "M230", "to": "B", "text": "overloaded"})
    frames = a.sync()
    assert "RECEIVED" not in a.statuses("M230")  # 拒收時不回報成功接收
    err = a.of("error", frames)[0]
    assert err["code"] == "REJECTED" and "資源過載" in err["detail"]
    assert b.of("message", b.sync()) == []
    assert h.storage.get_message("M230") is None
    ev = h.storage.get_events("M230")
    assert [e["stage"] for e in ev] == ["REJECTED"] and "拒收" in ev[0]["reason"] and ev[0]["is_simulated"]
    # 清除後可接受下一筆合法訊息
    assert h.client.delete("/api/faults/SERVER").status_code == 200
    a.send({"type": "message", "message_id": "M231", "to": "B", "text": "ok now"})
    a.sync()
    assert a.statuses("M231") == ["RECEIVED", "FORWARDED"]


def test_duplicate_message_id_with_different_content(h):
    a = h.connect("A")
    h.connect("B")
    a.send({"type": "message", "message_id": "M240", "to": "B", "text": "first"})
    a.send({"type": "message", "message_id": "M240", "to": "B", "text": "different"})
    errs = a.of("error", a.sync())
    assert errs[0]["code"] == "DUPLICATE_MESSAGE_ID"
    assert h.storage.get_message("M240")["text"] == "first"
