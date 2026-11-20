"""AC2.1 → US-02／T2.1、T2.2：雙向路由、先保存再轉送、無 ACK 只能是 FORWARDED。"""

from server.storage import Storage


def test_bidirectional_routing_json_fields(h):
    a = h.connect("A")
    b = h.connect("B")
    a.send({"type": "message", "message_id": "M201", "to": "B", "text": "hello"})
    a.sync()
    b.send({"type": "message", "message_id": "M202", "to": "A", "text": "reply"})
    b.sync()
    got_b = b.of("message")
    got_a = a.of("message", a.sync())
    assert len(got_b) == 1 and len(got_a) == 1
    mb, ma = got_b[0], got_a[0]
    assert (mb["from"], mb["to"], mb["message_id"], mb["text"]) == ("A", "B", "M201", "hello")
    assert (ma["from"], ma["to"], ma["message_id"], ma["text"]) == ("B", "A", "M202", "reply")
    for m in (ma, mb):
        assert set(m) == {"type", "message_id", "from", "to", "text", "timestamp", "redelivery"}
        assert m["redelivery"] is False


def test_saved_before_forwarded(h, monkeypatch):
    a = h.connect("A")
    b = h.connect("B")
    order = []
    real_save = h.storage.save_message
    real_send = h.comm._send_role

    def spy_save(*args, **kw):
        order.append("save")
        return real_save(*args, **kw)

    async def spy_send(role, payload):
        if payload.get("type") == "message":
            order.append("forward")
        return await real_send(role, payload)

    monkeypatch.setattr(h.storage, "save_message", spy_save)
    monkeypatch.setattr(h.comm, "_send_role", spy_send)
    a.send({"type": "message", "message_id": "M201", "to": "B", "text": "hello"})
    a.sync()
    assert order == ["save", "forward"]
    assert h.stages("M201") == ["RECEIVED", "FORWARDED"]
    stored = h.storage.get_message("M201")
    assert (stored["from_role"], stored["to_role"], stored["text"]) == ("A", "B", "hello")


def test_storage_failure_means_no_forward(h):
    a = h.connect("A")
    b = h.connect("B")
    h.storage.fail_next_save = True
    a.send({"type": "message", "message_id": "M299", "to": "B", "text": "lost"})
    errs = a.of("error", a.sync())
    assert errs[0]["code"] == "STORAGE_FAILED"
    assert b.of("message", b.sync()) == []
    assert "RECEIVED" not in a.statuses("M299")


def test_without_ack_only_forwarded_never_delivered(h):
    a = h.connect("A")
    b = h.connect("B")
    a.send({"type": "message", "message_id": "M201", "to": "B", "text": "hello"})
    a.sync()
    b.sync()  # B 收到但不回 ACK
    a.sync()
    assert a.statuses("M201") == ["RECEIVED", "FORWARDED"]
    assert h.storage.get_message("M201")["status"] == "FORWARDED"
    assert "DELIVERED" not in h.stages("M201")


def test_server_generates_message_id_when_missing(h):
    a = h.connect("A")
    b = h.connect("B")
    a.send({"type": "message", "to": "B", "text": "no id"})
    st = a.of("status", a.sync())
    mid = st[0]["message_id"]
    assert mid.startswith("M") and len(mid) > 4
    assert b.of("message", b.sync())[0]["message_id"] == mid
    assert Storage.new_message_id() != Storage.new_message_id()
