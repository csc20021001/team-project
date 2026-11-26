"""Member 1: service health, role checks, socket lifecycle, replay, and query API."""


def test_health_and_role_validation(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.post("/api/messages", json={"sender": "C", "recipient": "A", "body": "x"}).status_code == 422
    assert client.post("/api/messages", json={"sender": "A", "recipient": "A", "body": "x"}).status_code == 422
    assert client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": " "}).status_code == 422
    assert client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "x", "extra": 1}).status_code == 422


def test_http_send_and_readonly_message_event_query(client):
    result = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "hello"})
    assert result.status_code == 200 and result.json()["status"] == "OFFLINE"
    mid = result.json()["message_id"]
    detail = client.get(f"/api/messages/{mid}").json()
    assert detail["message"]["body"] == "hello"
    assert detail["message"]["status"] == "OFFLINE"
    assert [event["type"] for event in detail["events"]] == ["RECEIVED", "OFFLINE"]
    assert client.get("/api/messages/missing").status_code == 404
    assert client.get("/api/events", params={"message_id": mid}).json()


def test_socket_role_connection_and_invalid_role_rejected(client):
    from starlette.websockets import WebSocketDisconnect
    with client.websocket_connect("/ws/A") as socket:
        assert socket.receive_json() == {"type": "connected", "role": "A"}
        socket.send_json({"type": "send", "sender": "B", "recipient": "A", "body": "spoof"})
        assert socket.receive_json()["error"] == "invalid_message"
        socket.send_json({"type": "send", "recipient": "B", "body": "   "})
        assert socket.receive_json()["error"] == "invalid_message"
    try:
        with client.websocket_connect("/ws/C"):
            assert False, "invalid role must be rejected before accepting"
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


def test_duplicate_connection_replaces_old_without_old_cleanup_removing_new(client):
    with client.websocket_connect("/ws/A") as first:
        assert first.receive_json()["type"] == "connected"
        with client.websocket_connect("/ws/A") as second:
            assert second.receive_json()["type"] == "connected"
            first.send_json({"type": "send", "recipient": "B", "body": "old"})
            # The superseded endpoint is closed; the registry still belongs to socket two.
            try:
                first.receive_json()
            except Exception:
                pass
            assert client.get("/api/health").json()["connected_roles"] == ["A"]
            second.send_json({"type": "send", "recipient": "B", "body": "new"})
            assert second.receive_json()["status"] == "OFFLINE"
            assert client.get("/api/health").json()["connected_roles"] == ["A"]


def test_offline_notice_and_reconnect_replays_queued_message(client):
    queued = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "queued"}).json()
    mid = queued["message_id"]
    with client.websocket_connect("/ws/B") as receiver:
        receiver.receive_json()
        delivery = receiver.receive_json()
        assert delivery["type"] == "message" and delivery["message_id"] == mid
        assert delivery["body"] == "queued"
        message = client.get(f"/api/messages/{mid}").json()["message"]
        assert message["status"] == "FORWARDED" and message["attempts"] == 1


def test_reconnect_resends_pending_message_with_same_identifier(client):
    original = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "again"}).json()
    with client.websocket_connect("/ws/B") as first:
        first.receive_json()
        first.receive_json()
    with client.websocket_connect("/ws/B") as second:
        second.receive_json()
        replay = second.receive_json()
        assert replay["message_id"] == original["message_id"]
        assert replay["attempt"] == 2


def test_events_endpoint_bounds_and_health_connection_snapshot(client):
    assert client.get("/api/events", params={"limit": 501}).status_code == 422
    assert client.get("/api/events", params={"limit": 0}).status_code == 422
    with client.websocket_connect("/ws/B") as receiver:
        receiver.receive_json()
        assert "B" in client.get("/api/health").json()["connected_roles"]
    assert client.get("/api/health").json()["connected_roles"] == []
