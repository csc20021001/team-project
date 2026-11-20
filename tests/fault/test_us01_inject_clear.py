"""組員三 US-01 注入與清除故障（T-01-01～T-01-04）。"""

import pytest

from server.fault.simulator import BASELINE, FaultInjection, FaultSimulator, FaultValidationError
from server.storage import Storage


@pytest.fixture
def sim():
    return FaultSimulator(Storage(":memory:"))


# T-01-01 欄位規格與範例 / T-01-02 三類模擬事件帶模擬標記與事件 ID
@pytest.mark.parametrize("ftype,params,observed", [
    ("connection_down", {}, {"connection_down": True, "latency_ms": 100, "usage_pct": 30}),
    ("response_timeout", {"latency_ms": 5000}, {"connection_down": False, "latency_ms": 5000, "usage_pct": 30}),
    ("resource_overload", {"usage_pct": 95}, {"connection_down": False, "latency_ms": 100, "usage_pct": 95}),
    ("回應逾時", {}, {"connection_down": False, "latency_ms": 5000, "usage_pct": 30}),
])
def test_three_fault_types_produce_simulated_events(sim, ftype, params, observed):
    r = sim.enable({"target_id": "SIM-01", "fault_type": ftype, "params": params})
    assert r["is_simulated"] is True and r["event_id"].startswith("E")
    assert r["observed"] == observed
    ev = sim.storage.get_event(r["event_id"])
    assert ev["is_simulated"] is True and ev["target_id"] == "SIM-01" and ev["stage"] == "FAULT_INJECTED"
    assert (ev["connection_down"], ev["latency_ms"], ev["usage_pct"]) == tuple(observed.values())


def test_event_ids_unique_and_real_data_not_overwritten(sim):
    real = sim.storage.record_event("OBSERVATION", target_id="SIM-01", connection_down=False, latency_ms=120,
                                    usage_pct=40)
    ids = {sim.enable({"target_id": "SIM-01", "fault_type": "connection_down"})["event_id"] for _ in range(5)}
    assert len(ids) == 5
    assert sim.storage.get_event(real["event_id"])["latency_ms"] == 120
    assert sim.storage.get_event(real["event_id"])["is_simulated"] is False


# T-01-03 參數檢查
@pytest.mark.parametrize("payload,field", [
    ({"fault_type": "connection_down"}, "target_id"),
    ({"target_id": "", "fault_type": "connection_down"}, "target_id"),
    ({"target_id": "   ", "fault_type": "connection_down"}, "target_id"),
    ({"target_id": "bad target!", "fault_type": "connection_down"}, "target_id"),
    ({"target_id": "B"}, "fault_type"),
    ({"target_id": "B", "fault_type": "meteor"}, "fault_type"),
    ({"target_id": "B", "fault_type": "response_timeout", "params": {"latency_ms": 0}}, "params.latency_ms"),
    ({"target_id": "B", "fault_type": "response_timeout", "params": {"latency_ms": 60001}}, "params.latency_ms"),
    ({"target_id": "B", "fault_type": "response_timeout", "params": {"latency_ms": "5000"}}, "params.latency_ms"),
    ({"target_id": "B", "fault_type": "response_timeout", "params": {"latency_ms": True}}, "params.latency_ms"),
    ({"target_id": "B", "fault_type": "resource_overload", "params": {"usage_pct": 101}}, "params.usage_pct"),
    ({"target_id": "B", "fault_type": "resource_overload", "params": {"usage_pct": -1}}, "params.usage_pct"),
    ({"target_id": "B", "fault_type": "resource_overload", "params": {"cpu": 1}}, "params"),
    ({"target_id": "B", "fault_type": "connection_down", "params": {"connection_down": False}},
     "params.connection_down"),
    ({"target_id": "B", "fault_type": "connection_down", "params": [1]}, "params"),
    ("not an object", "body"),
])
def test_invalid_parameters_rejected_without_event(sim, payload, field):
    with pytest.raises(FaultValidationError) as exc:
        sim.enable(payload)
    assert exc.value.field == field
    assert sim.storage.list_events() == []


@pytest.mark.parametrize("ftype,params", [
    ("response_timeout", {"latency_ms": 1}), ("response_timeout", {"latency_ms": 60000}),
    ("resource_overload", {"usage_pct": 0}), ("resource_overload", {"usage_pct": 100}),
])
def test_parameter_boundaries_accepted(ftype, params):
    assert FaultInjection.validate({"target_id": "B", "fault_type": ftype, "params": params}).params == params


def test_clear_restores_baseline_and_second_clear_is_noop(sim):
    sim.enable({"target_id": "B", "fault_type": "connection_down"})
    sim.enable({"target_id": "B", "fault_type": "response_timeout", "params": {"latency_ms": 4000}})
    r = sim.clear("B")
    assert sorted(r["cleared"]) == ["connection_down", "response_timeout"]
    assert r["observed"] == BASELINE and r["event_id"]
    ev = sim.storage.get_event(r["event_id"])
    assert ev["stage"] == "FAULT_CLEARED" and ev["is_simulated"]
    n = len(sim.storage.list_events())
    again = sim.clear("B")
    assert again["cleared"] == [] and again["event_id"] is None and again["state"] == "基準狀態"
    assert len(sim.storage.list_events()) == n  # 不重複產生清除事件


def test_clear_single_type(sim):
    sim.enable({"target_id": "B", "fault_type": "connection_down"})
    sim.enable({"target_id": "B", "fault_type": "resource_overload"})
    r = sim.clear("B", "connection_down")
    assert r["cleared"] == ["connection_down"]
    assert r["observed"] == {"connection_down": False, "latency_ms": 100, "usage_pct": 95}
    assert sim.status("B")["state"] == "模擬啟用中"


# T-01-04 串接 API：啟用、拒絕、清除
def test_api_enable_reject_clear(h):
    r = h.client.post("/api/faults", json={"target_id": "SIM-01", "fault_type": "resource_overload",
                                           "params": {"usage_pct": 92}})
    assert r.status_code == 201 and r.json()["is_simulated"] is True
    assert h.client.get("/api/faults/SIM-01").json()["state"] == "模擬啟用中"
    bad = h.client.post("/api/faults", json={"target_id": "SIM-01", "fault_type": "resource_overload",
                                             "params": {"usage_pct": 200}})
    assert bad.status_code == 400 and bad.json()["field"] == "params.usage_pct"
    assert h.client.post("/api/faults", json={"fault_type": "connection_down"}).status_code == 400
    c = h.client.delete("/api/faults/SIM-01")
    assert c.status_code == 200 and c.json()["cleared"] == ["resource_overload"]
    assert h.client.delete("/api/faults/SIM-01").json()["cleared"] == []
    assert h.client.delete("/api/faults/SIM-01?fault_type=meteor").status_code == 400
    assert h.client.get("/api/faults").json()["faults"] == []


def test_comm_reacts_to_connection_down_and_clear(h):
    """故障控制介接：模擬 B 連線中斷時訊息進待送，清除後自動以原 ID 補送。"""
    a = h.connect("A")
    b = h.connect("B")
    h.client.post("/api/faults", json={"target_id": "B", "fault_type": "connection_down"})
    a.send({"type": "message", "message_id": "M601", "to": "B", "text": "during fault"})
    a.sync()
    assert a.statuses("M601") == ["RECEIVED", "QUEUED"]
    q = [e for e in h.storage.get_events("M601") if e["stage"] == "QUEUED"][0]
    assert q["is_simulated"] and "模擬連線中斷" in q["reason"]
    assert b.of("message", b.sync()) == []
    h.client.delete("/api/faults/B")
    got = b.of("message", b.sync())
    assert [m["message_id"] for m in got] == ["M601"]


def test_comm_response_timeout_drops_ack(h):
    a = h.connect("A")
    b = h.connect("B")
    h.client.post("/api/faults", json={"target_id": "B", "fault_type": "response_timeout",
                                       "params": {"latency_ms": 8000}})
    a.send({"type": "message", "message_id": "M602", "to": "B", "text": "x"})
    a.sync()
    b.sync()
    b.send({"type": "ack", "message_id": "M602"})
    b.sync()
    a.sync()
    assert "DELIVERED" not in a.statuses("M602")
    ev = [e for e in h.storage.get_events("M602") if e["stage"] == "ACK_DROPPED"]
    assert ev and ev[0]["is_simulated"]
