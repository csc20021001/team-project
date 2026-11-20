"""AC3.1 → US-03／T3.1、T3.2：依 Message ID 查詢事件；404／4xx；不洩漏程式堆疊。"""

import pytest


def _scenario(h):
    a = h.connect("A")
    b = h.connect("B")
    a.send({"type": "message", "message_id": "M301", "to": "B", "text": "forwarded"})
    a.sync()
    b.sync()
    b.close()
    h.wait_until(lambda: not h.comm.connections.is_online("B"))
    a.send({"type": "message", "message_id": "M302", "to": "B", "text": "offline"})
    a.sync()
    return a


def test_events_are_not_mixed_and_have_required_fields(h):
    _scenario(h)
    r1 = h.client.get("/api/messages/M301")
    r2 = h.client.get("/api/messages/M302")
    assert r1.status_code == r2.status_code == 200
    e1, e2 = r1.json()["events"], r2.json()["events"]
    assert {e["message_id"] for e in e1} == {"M301"}
    assert {e["message_id"] for e in e2} == {"M302"}
    assert {e["event_id"] for e in e1}.isdisjoint({e["event_id"] for e in e2})
    for e in e1 + e2:
        for k in ("event_id", "message_id", "ts", "role", "stage"):
            assert e[k], (k, e)
    assert [e["stage"] for e in e1] == ["RECEIVED", "FORWARDED"]
    # 與記錄模組核對
    assert e1 == h.storage.get_events("M301")
    assert r1.json()["message"]["status"] == "FORWARDED"


def test_offline_message_shows_reason_not_forwarded(h):
    _scenario(h)
    body = h.client.get("/api/messages/M302").json()
    assert body["message"]["status"] == "QUEUED"
    stages = [e["stage"] for e in body["events"]]
    assert "FORWARDED" not in stages
    queued = [e for e in body["events"] if e["stage"] == "QUEUED"][0]
    assert "離線" in queued["reason"] and queued["role"] == "B"


def test_unknown_id_404(h):
    r = h.client.get("/api/messages/M404NOPE")
    assert r.status_code == 404
    assert r.json()["error"] == "NOT_FOUND"


@pytest.mark.parametrize("url", [
    "/api/messages/" + "x" * 65,
    "/api/messages/bad%20id",
    "/api/messages/%27%3B%20DROP%20TABLE",
    "/api/messages?limit=0",
    "/api/messages?limit=abc",
    "/api/messages?role=Z",
    "/api/events?limit=9999",
])
def test_illegal_params_4xx_without_stack(h, url):
    r = h.client.get(url)
    assert 400 <= r.status_code < 500
    text = r.text
    for leak in ("Traceback", "File \"", "line ", ".py", "sqlite3"):
        assert leak not in text
    assert "error" in r.json()


def test_internal_error_does_not_leak_stack(h, monkeypatch):
    from fastapi.testclient import TestClient

    def boom(_):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(h.storage, "get_message", boom)
    client = TestClient(h.app, raise_server_exceptions=False)
    r = client.get("/api/messages/M301")
    assert r.status_code == 500
    assert "secret" not in r.text and "Traceback" not in r.text
    assert r.json()["error"] == "INTERNAL_ERROR"
