"""組員二 × 組員一：逾時重送與重連補送皆保留原 Message ID。"""


def test_forwarded_but_unacked_is_redelivered_after_reconnect(h):
    a = h.connect("A")
    b = h.connect("B")
    a.send({"type": "message", "message_id": "M501", "to": "B", "text": "lost in transit"})
    a.sync()
    b.sync()  # 收到但未 ACK 就斷線
    b.close()
    h.wait_until(lambda: not h.comm.connections.is_online("B"))
    b2 = h.connect("B")
    again = b2.of("message")
    assert [m["message_id"] for m in again] == ["M501"]
    b2.send({"type": "ack", "message_id": "M501"})
    b2.sync()
    a.sync()
    assert a.statuses("M501")[-1] == "DELIVERED"


def test_timeout_resend_keeps_id_then_ack_delivers(h):
    a = h.connect("A")
    b = h.connect("B")
    a.send({"type": "message", "message_id": "M502", "to": "B", "text": "slow"})
    a.sync()
    b.sync()
    handled = h.call(h.comm.resend_tick, h.storage.now() + 5.1)
    assert handled == ["M502"]
    resent = b.of("message", b.sync())
    assert [(m["message_id"], m["redelivery"]) for m in resent] == [("M502", True)]
    assert h.stages("M502") == ["RECEIVED", "FORWARDED", "ACK_TIMEOUT", "RESENT"]
    b.send({"type": "ack", "message_id": "M502"})
    b.sync()
    a.sync()
    assert a.statuses("M502")[-1] == "DELIVERED"
    assert h.call(h.comm.resend_tick, h.storage.now() + 60) == []


def test_resend_when_receiver_offline_goes_to_queue(h):
    a = h.connect("A")
    b = h.connect("B")
    a.send({"type": "message", "message_id": "M503", "to": "B", "text": "x"})
    a.sync()
    b.sync()
    b.close()
    h.wait_until(lambda: not h.comm.connections.is_online("B"))
    h.call(h.comm.resend_tick, h.storage.now() + 6)
    assert h.storage.get_message("M503")["status"] == "QUEUED"
