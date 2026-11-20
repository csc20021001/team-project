"""AC2.2 → US-02／T2.3：合法 ACK 才通知 DELIVERED；錯誤角色、未知 ID、缺少 ACK 均不得通知送達。"""


def _setup(h):
    a = h.connect("A")
    b = h.connect("B")
    a.send({"type": "message", "message_id": "M201", "to": "B", "text": "hello"})
    a.send({"type": "message", "message_id": "M203", "to": "B", "text": "never acked"})
    a.sync()
    b.sync()
    return a, b


def test_valid_ack_from_b_marks_delivered(h, monkeypatch):
    a, b = _setup(h)
    calls = []
    real = h.storage.validate_ack
    monkeypatch.setattr(h.storage, "validate_ack", lambda mid, role: calls.append((mid, role)) or real(mid, role))
    b.send({"type": "ack", "message_id": "M201"})
    b.sync()
    a.sync()
    assert calls == [("M201", "B")]  # 經過組員二的驗證介面
    assert a.statuses("M201") == ["RECEIVED", "FORWARDED", "DELIVERED"]
    assert h.storage.get_message("M201")["status"] == "DELIVERED"


def test_ack_from_wrong_role_is_rejected(h):
    a, b = _setup(h)
    a.send({"type": "ack", "message_id": "M201"})  # A 不是 M201 的接收者
    errs = a.of("error", a.sync())
    assert errs[-1]["code"] == "ACK_WRONG_ROLE"
    assert "DELIVERED" not in a.statuses("M201")
    assert h.storage.get_message("M201")["status"] == "FORWARDED"
    assert "ACK_REJECTED" in h.stages("M201")


def test_ack_with_unknown_id_is_rejected(h):
    a, b = _setup(h)
    b.send({"type": "ack", "message_id": "M999"})
    errs = b.of("error", b.sync())
    assert errs[-1]["code"] == "ACK_UNKNOWN_ID"
    a.sync()
    assert all(m.get("status") != "DELIVERED" for m in a.inbox)
    rejected = h.client.get("/api/events", params={"stage": "ACK_REJECTED"}).json()["events"]
    assert rejected[-1]["detail"]["ack_message_id"] == "M999"


def test_missing_ack_never_delivered_and_times_out(h):
    a, b = _setup(h)
    b.send({"type": "ack", "message_id": "M201"})
    b.sync()
    # M203 沒有 ACK：逾時後以原 ID 重送，用盡次數則 FAILED，始終不得 DELIVERED
    t = h.storage.now()
    for i in range(1, 4):
        h.call(h.comm.resend_tick, t + 6 * i)
        b.sync()
    a.sync()
    assert "DELIVERED" not in a.statuses("M203")
    assert a.statuses("M203")[-1] == "FAILED"
    resent = [m for m in b.inbox if m["type"] == "message" and m["message_id"] == "M203"]
    assert len(resent) == 3 and all(m["message_id"] == "M203" for m in resent)
    assert h.storage.get_message("M203")["status"] == "FAILED"


def test_duplicate_ack_does_not_notify_twice(h):
    a, b = _setup(h)
    b.send({"type": "ack", "message_id": "M201"})
    b.send({"type": "ack", "message_id": "M201"})
    b.sync()
    a.sync()
    assert a.statuses("M201").count("DELIVERED") == 1
