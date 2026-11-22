"""Member 3: US-01..US-04 fault lifecycle, rules, evidence, advice, and rediagnosis."""

import pytest

from messagetrace.team1_transport import app as transport
from messagetrace.team3_diagnostics import diagnostics


@pytest.mark.parametrize("kind,params", [
    ("disconnect", {}), ("timeout", {"delay_seconds": 3600}),
    ("overload", {"load": 1.0, "threshold": 0.0}),
])
def test_us01_fault_injection_clear_and_simulation_evidence(client, kind, params):
    fault = client.post("/api/faults", json={"target": "B", "kind": kind, "params": params})
    assert fault.status_code == 200
    payload = fault.json()
    assert payload["active"] and payload["event"]["simulated"]
    assert payload["event"]["details"]["kind"] == kind
    sent = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": kind}).json()
    assert sent["status"] in {"OFFLINE", "TIMEOUT", "OVERLOAD"}
    assert client.delete(f"/api/faults/{payload['fault_id']}").json()["active"] is False
    assert client.delete(f"/api/faults/{payload['fault_id']}").status_code == 404
    history = client.get(f"/api/messages/{sent['message_id']}").json()["events"]
    assert any(event["type"] == "FAULT_INJECTED" and event["simulated"] for event in client.get("/api/events").json())
    assert any(event["type"] in {"OFFLINE", "TIMEOUT", "OVERLOAD"} for event in history)


@pytest.mark.parametrize("body", [
    {"target": "C", "kind": "disconnect"},
    {"target": "A", "kind": "random"},
    {"target": "B", "kind": "timeout", "params": {"delay_seconds": -0.01}},
    {"target": "B", "kind": "timeout", "params": {"delay_seconds": 3600.01}},
    {"target": "B", "kind": "disconnect", "params": {"unknown": 1}},
    {"target": "B", "kind": "overload", "params": {"load": 0.8, "threshold": 0.8}},
    {"target": "B", "kind": "overload", "params": {"load": True}},
    {"target": "B", "kind": "disconnect", "params": []},
])
def test_us01_rejects_invalid_targets_kinds_parameters_and_boundaries(client, body):
    assert client.post("/api/faults", json=body).status_code == 422


def test_us01_rejects_non_finite_values_before_persistence():
    with pytest.raises(ValueError, match="finite number"):
        diagnostics.validate_fault("B", "timeout", {"delay_seconds": float("nan")})


def test_us02_rules_are_independent_can_hit_multiple_and_have_boundaries(client):
    fault = client.post("/api/faults", json={"target": "B", "kind": "disconnect"}).json()
    msg = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "down"}).json()
    client.delete(f"/api/faults/{fault['fault_id']}")
    # Add a second, independent timeout clue for the same delivery; both rules must survive.
    transport.store.event("TIMEOUT", msg["message_id"], "A", "B", True,
                          {"fault_id": fault["fault_id"], "boundary": 0})
    diagnosis = client.get("/api/diagnostics", params={"message_id": msg["message_id"]}).json()
    rules = {result["rule"] for result in diagnosis["diagnoses"]}
    assert rules == {"R01", "R02"}
    assert diagnosis["rules_evaluated"] == ["R01", "R02", "R03"]
    assert len(diagnosis["recommendations"]) == 3
    assert all(result["evidence_event_ids"] for result in diagnosis["diagnoses"])


def test_us02_no_match_and_r03_load_threshold_edge(client):
    empty = client.get("/api/diagnostics", params={"target": "B"}).json()
    assert empty["diagnoses"] == []
    accepted = client.post("/api/faults", json={
        "target": "B", "kind": "overload", "params": {"load": 0.801, "threshold": 0.8},
    })
    assert accepted.status_code == 200
    rejected_boundary = client.post("/api/faults", json={
        "target": "A", "kind": "overload", "params": {"load": 0.8, "threshold": 0.8},
    })
    assert rejected_boundary.status_code == 422


def test_us02_r02_matches_forwarded_without_ack_but_clears_after_ack(client):
    with client.websocket_connect("/ws/B") as receiver:
        receiver.receive_json()
        sent = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "ack boundary"}).json()
        receiver.receive_json()
        mid = sent["message_id"]
        unacked = client.get("/api/diagnostics", params={"message_id": mid}).json()
        assert "R02" in {hit["rule"] for hit in unacked["diagnoses"]}
        receiver.send_json({"type": "ack", "message_id": mid})
        receiver.receive_json()
        acked = client.get("/api/diagnostics", params={"message_id": mid}).json()
        assert "R02" not in {hit["rule"] for hit in acked["diagnoses"]}


def test_us02_missing_and_malformed_evidence_are_reported_not_crashes(client):
    incomplete = {"id": "missing-target", "type": "TIMEOUT", "timestamp": 1.0,
                  "target": None, "simulated": True, "details": {}}
    malformed = {"id": "bad-json", "type": "OVERLOAD", "timestamp": 1.0,
                 "target": "B", "simulated": True, "details": "{"}
    events, failures = diagnostics._validated_events([incomplete, malformed])
    assert events == []
    assert len(failures) == 2
    report = client.get("/api/diagnostics").json()
    assert "malformed_events" in report


def test_us02_diagnosis_reports_corrupt_persisted_event_json(client):
    message = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "bad event"}).json()
    with transport.store.db() as conn:
        conn.execute("UPDATE events SET details=? WHERE message_id=? AND type='OFFLINE'", ("{broken", message["message_id"]))
    report = client.get("/api/diagnostics", params={"message_id": message["message_id"]}).json()
    assert any(item["error"] == "invalid_event_format" for item in report["malformed_events"])


def test_us03_evidence_links_and_target_filter_do_not_leak_other_recipient_faults(client):
    for role in ("A", "B"):
        client.post("/api/faults", json={"target": role, "kind": "disconnect"})
    message = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "trace"}).json()
    report = client.get("/api/diagnostics", params={"message_id": message["message_id"]}).json()
    references = {event_id for result in report["diagnoses"] for event_id in result["evidence_event_ids"]}
    assert report["target"] == "B"
    assert all(event["target"] == "B" for result in report["diagnoses"] for event in result["evidence"])
    assert all(event["id"] in references for result in report["diagnoses"] for event in result["evidence"])
    assert client.get("/api/diagnostics", params={"message_id": message["message_id"], "target": "A"}).status_code == 404


def test_us03_duplicate_event_query_is_stable_and_missing_message_is_404(client):
    message = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "one"}).json()
    mid = message["message_id"]
    transport.store.event("TIMEOUT", mid, "A", "B", True, {"source": "duplicate"})
    first = client.get("/api/diagnostics", params={"message_id": mid}).json()
    second = client.get("/api/diagnostics", params={"message_id": mid}).json()
    ids1 = [eid for result in first["diagnoses"] for eid in result["evidence_event_ids"]]
    ids2 = [eid for result in second["diagnoses"] for eid in result["evidence_event_ids"]]
    assert ids1 == ids2 and len(ids1) == len(set(ids1))
    assert client.get("/api/diagnostics", params={"message_id": "absent"}).status_code == 404


def test_us04_recommendation_table_has_one_reviewable_action_per_fault(client):
    table = client.get("/api/diagnostics").json()["recommendations"]
    assert {row["fault"] for row in table} == {"disconnect", "timeout", "overload"}
    assert all(row["rule"] and row["title"] and len(row["action"]) > 20 for row in table)


def test_us04_clear_and_reinject_then_rediagnose_reflects_new_state(client):
    first = client.post("/api/faults", json={"target": "B", "kind": "disconnect"}).json()
    down = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "first"}).json()
    before = client.get("/api/diagnostics", params={"message_id": down["message_id"]}).json()
    assert "R01" in {hit["rule"] for hit in before["diagnoses"]}
    client.delete(f"/api/faults/{first['fault_id']}")
    with client.websocket_connect("/ws/B") as receiver:
        receiver.receive_json()
        replay = receiver.receive_json()
        assert replay["message_id"] == down["message_id"]
        receiver.send_json({"type": "ack", "message_id": replay["message_id"]})
        receiver.receive_json()
        healthy = client.post("/api/messages", json={"sender": "A", "recipient": "B", "body": "second"}).json()
        delivery = receiver.receive_json()
        assert delivery["message_id"] == healthy["message_id"]
        receiver.send_json({"type": "ack", "message_id": healthy["message_id"]})
        receiver.receive_json()
    after = client.get("/api/diagnostics", params={"message_id": healthy["message_id"]}).json()
    assert after["diagnoses"] == []
    assert client.get("/api/diagnostics", params={"message_id": down["message_id"]}).json()["diagnoses"] == before["diagnoses"]
