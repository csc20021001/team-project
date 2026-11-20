"""組員三 US-03 查閱事件依據（T-03-01～T-03-04）：依據格式、規則與事件關聯、跨目標、重複事件、缺漏資料。"""

from server.fault.engine import diagnose_events
from tests.fault.test_us02_diagnosis import ev

EVIDENCE_FIELDS = {"rule_id", "event_id", "timestamp", "target_id", "field", "observed_value", "threshold",
                   "is_simulated"}


def test_evidence_format_keeps_raw_value_and_threshold():
    r = diagnose_events("SIM-01", [ev(lat=4200, use=91)])
    for h in r["hits"]:
        assert set(h["evidence"]) == EVIDENCE_FIELDS
    ev2 = {h["rule_id"]: h["evidence"] for h in r["hits"]}
    assert ev2["R02"]["observed_value"] == 4200 and ev2["R02"]["threshold"] == ">= 3000"
    assert ev2["R03"]["observed_value"] == 91 and ev2["R03"]["threshold"] == ">= 90"
    assert ev2["R02"]["is_simulated"] is True


def test_cross_target_events_not_mixed():
    events = [ev("E1", target="SIM-01"), ev("E2", target="SIM-02", down=True)]
    assert diagnose_events("SIM-01", events)["status"] == "未識別故障"
    r2 = diagnose_events("SIM-02", events)
    assert [h["event_id"] for h in r2["hits"]] == ["E2"]
    assert all(h["evidence"]["target_id"] == "SIM-02" for h in r2["hits"])


def test_duplicate_events_counted_once():
    e = ev("E1", down=True)
    r = diagnose_events("SIM-01", [e, dict(e), dict(e)])
    assert len(r["hits"]) == 1 and r["event_ids"] == ["E1"]


def test_evidence_api_and_missing_raw_data(h):
    inj = h.client.post("/api/faults", json={"target_id": "SIM-01", "fault_type": "response_timeout",
                                             "params": {"latency_ms": 3000}}).json()
    d = h.client.post("/api/diagnosis", json={"target_id": "SIM-01"}).json()
    e = h.client.get(f"/api/diagnosis/{d['diagnosis_id']}/evidence").json()
    item = e["evidence"][0]
    assert item["status"] == "依據存在"
    assert (item["event_id"], item["observed_value"], item["threshold"], item["is_simulated"]) == \
        (inj["event_id"], 3000, ">= 3000", True)
    assert item["target_id"] == "SIM-01" and item["timestamp"]
    # 原始事件已不存在 → 明確狀態
    h.storage.delete_event(inj["event_id"])
    e2 = h.client.get(f"/api/diagnosis/{d['diagnosis_id']}/evidence").json()
    assert e2["evidence"][0]["status"] == "依據無法取得"
    assert h.client.get("/api/diagnosis/DNOPE/evidence").status_code == 404
    assert h.client.get("/api/diagnosis/bad id/evidence").status_code == 422


def test_comm_events_linked_by_message_and_target(h):
    """通訊層提供含事件 ID、目標、時間與模擬標記的證據事件。"""
    a = h.connect("A")
    h.connect("B")
    h.client.post("/api/faults", json={"target_id": "B", "fault_type": "connection_down"})
    a.send({"type": "message", "message_id": "M701", "to": "B", "text": "x"})
    a.sync()
    q = [e for e in h.storage.get_events("M701") if e["stage"] == "QUEUED"][0]
    assert q["event_id"] and q["ts"] and q["role"] == "B" and q["is_simulated"] is True
    d = h.client.post("/api/diagnosis", json={"target_id": "B"}).json()
    assert d["hits"][0]["rule_id"] == "R01"
