"""組員二：SQLite 儲存、訊息 ID、事件、ACK 驗證、逾時、重送、待送佇列與補送。"""

import pytest

from server.storage import DuplicateMessageId, MessageStatus, Storage, StorageError


class Clock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def st():
    clock = Clock()
    s = Storage(":memory:", clock=clock)
    s.clock = clock
    yield s
    s.close()


def test_sqlite_file_persists(tmp_path):
    p = tmp_path / "x.db"
    s = Storage(str(p))
    s.save_message("M1", "A", "B", "hi")
    s.record_event("RECEIVED", message_id="M1", role="A")
    s.close()
    s2 = Storage(str(p))
    assert s2.get_message("M1")["text"] == "hi"
    assert [e["stage"] for e in s2.get_events("M1")] == ["RECEIVED"]
    s2.close()


def test_message_ids_unique():
    ids = {Storage.new_message_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(i.startswith("M") for i in ids)


def test_save_is_idempotent_and_rejects_conflicting_duplicate(st):
    m, created = st.save_message("M1", "A", "B", "hi")
    assert created and m["status"] == MessageStatus.RECEIVED and m["attempts"] == 0
    m2, created2 = st.save_message("M1", "A", "B", "hi")
    assert not created2 and m2["message_id"] == "M1"
    with pytest.raises(DuplicateMessageId):
        st.save_message("M1", "A", "B", "other")


def test_simulated_save_failure(st):
    st.fail_next_save = True
    with pytest.raises(StorageError):
        st.save_message("M1", "A", "B", "hi")
    assert st.get_message("M1") is None


def test_event_fields(st):
    e = st.record_event("QUEUED", message_id="M9", role="B", reason="接收端 B 離線")
    assert e["event_id"].startswith("E") and e["ts"] and e["stage"] == "QUEUED"
    assert (e["message_id"], e["role"], e["reason"], e["is_simulated"]) == ("M9", "B", "接收端 B 離線", False)


@pytest.mark.parametrize("mid,role,code", [
    ("M1", "A", "ACK_WRONG_ROLE"),
    ("M404", "B", "ACK_UNKNOWN_ID"),
    (None, "B", "ACK_INVALID"),
])
def test_ack_validation_rejects(st, mid, role, code):
    st.save_message("M1", "A", "B", "hi")
    st.mark_forwarded("M1")
    r = st.validate_ack(mid, role)
    assert not r.ok and r.code == code
    assert st.get_message("M1")["status"] == MessageStatus.FORWARDED


def test_ack_before_forward_rejected(st):
    st.save_message("M1", "A", "B", "hi")
    st.mark_queued("M1", "offline")
    r = st.validate_ack("M1", "B")
    assert not r.ok and r.code == "ACK_NOT_FORWARDED"


def test_valid_ack_then_duplicate(st):
    st.save_message("M1", "A", "B", "hi")
    st.mark_forwarded("M1")
    assert st.validate_ack("M1", "B").ok
    st.mark_delivered("M1")
    r = st.validate_ack("M1", "B")
    assert r.ok and r.already_delivered


def test_delivered_never_downgraded(st):
    st.save_message("M1", "A", "B", "hi")
    st.mark_forwarded("M1")
    st.mark_delivered("M1")
    st.mark_forwarded("M1")
    st.mark_queued("M1", "x")
    st.mark_failed("M1", "x")
    assert st.get_message("M1")["status"] == MessageStatus.DELIVERED


def test_timeout_and_resend_selection(st):
    st.save_message("M1", "A", "B", "one")
    st.save_message("M2", "A", "B", "two")
    st.mark_forwarded("M1")
    st.clock.t += 2
    st.mark_forwarded("M2")
    assert st.due_for_resend(5) == []
    st.clock.t += 3.5  # M1 已 5.5 秒、M2 3.5 秒
    assert [m["message_id"] for m in st.due_for_resend(5)] == ["M1"]
    assert [m["message_id"] for m in st.due_for_resend(3)] == ["M1", "M2"]
    st.mark_forwarded("M1")  # 重送：沿用原 ID，次數 +1
    m1 = st.get_message("M1")
    assert m1["attempts"] == 2 and m1["message_id"] == "M1"


def test_pending_queue_preserves_order_and_ids(st):
    for i, t in enumerate(["a", "b", "c"]):
        st.save_message(f"M10{i}", "A", "B", t)
        st.mark_queued(f"M10{i}", "B offline")
    st.save_message("M200", "B", "A", "other direction")
    st.mark_queued("M200", "A offline")
    st.mark_forwarded("M101")
    st.mark_delivered("M101")
    assert [m["message_id"] for m in st.pending_for("B")] == ["M100", "M102"]
    assert [m["message_id"] for m in st.pending_for("A")] == ["M200"]


def test_failed_status_and_reason(st):
    st.save_message("M1", "A", "B", "hi")
    st.mark_forwarded("M1")
    st.mark_failed("M1", "重送 3 次仍未收到 ACK")
    m = st.get_message("M1")
    assert m["status"] == MessageStatus.FAILED and "ACK" in m["status_reason"]


def test_list_messages_and_events_filters(st):
    st.save_message("M1", "A", "B", "x")
    st.save_message("M2", "B", "A", "y")
    assert [m["message_id"] for m in st.list_messages(limit=1)] == ["M2"]
    assert len(st.list_messages(role="A")) == 2
    st.record_event("CONNECTED", role="A")
    st.record_event("DISCONNECTED", role="B")
    assert [e["stage"] for e in st.list_events(role="B")] == ["DISCONNECTED"]
    assert [e["stage"] for e in st.list_events(stages=["CONNECTED"])] == ["CONNECTED"]
