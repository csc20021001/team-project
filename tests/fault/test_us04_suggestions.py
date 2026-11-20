"""組員三 US-04 取得修復建議（T-04-01～T-04-04）：建議表、規則對照、使用者確認、重新診斷。"""

import pytest

from server.fault.suggestions import SUGGESTIONS, suggestions_for_result


def test_three_suggestion_tables_complete():
    assert set(SUGGESTIONS) == {"CONNECTION_DOWN", "RESPONSE_TIMEOUT", "RESOURCE_OVERLOAD"}
    for s in SUGGESTIONS.values():
        assert s["check_steps"] and s["actions"] and s["cautions"] and s["recovery_check"]


def test_multi_fault_gets_separate_suggestions_unidentified_gets_guidance():
    r = {"status": "已識別", "hits": [{"fault_code": "CONNECTION_DOWN"}, {"fault_code": "RESOURCE_OVERLOAD"}]}
    s = suggestions_for_result(r)
    assert [x["fault_code"] for x in s] == ["CONNECTION_DOWN", "RESOURCE_OVERLOAD"]
    assert all(x["requires_user_confirmation"] and x["auto_apply"] is False for x in s)
    u = suggestions_for_result({"status": "未識別故障", "hits": []})
    assert u[0]["fault_code"] is None and "check_steps" not in u[0] and u[0]["guidance"]
    i = suggestions_for_result({"status": "資料不足", "hits": []})
    assert i[0]["fault_name"] == "資料不足"


def test_suggestion_api(h):
    assert len(h.client.get("/api/suggestions").json()["suggestions"]) == 3
    s = h.client.get("/api/suggestions/RESPONSE_TIMEOUT").json()
    assert s["fault_name"] == "回應逾時" and s["auto_apply"] is False
    assert h.client.get("/api/suggestions/NOPE").status_code == 404


def test_confirm_does_not_modify_anything(h):
    h.client.post("/api/faults", json={"target_id": "B", "fault_type": "connection_down"})
    before = h.client.get("/api/faults/B").json()
    r = h.client.post("/api/suggestions/CONNECTION_DOWN/confirm", json={"target_id": "B"})
    assert r.status_code == 200 and r.json()["auto_applied"] is False
    assert h.client.get("/api/faults/B").json() == before  # 故障仍啟用，未被自動修改
    assert h.storage.get_event(r.json()["event_id"])["stage"] == "SUGGESTION_CONFIRMED"
    assert h.client.post("/api/suggestions/CONNECTION_DOWN/confirm", json={}).status_code == 400


def test_rediagnose_after_clear_shows_latest_result(h):
    h.client.post("/api/faults", json={"target_id": "SIM-01", "fault_type": "connection_down"})
    d1 = h.client.post("/api/diagnosis", json={"target_id": "SIM-01"}).json()
    assert d1["status"] == "已識別" and d1["suggestions"][0]["fault_code"] == "CONNECTION_DOWN"
    h.client.delete("/api/faults/SIM-01")
    d2 = h.client.post("/api/diagnosis", json={"target_id": "SIM-01"}).json()
    assert d2["status"] == "未識別故障" and d2["hits"] == []
    assert d2["supersedes"] == d1["diagnosis_id"] and d2["is_current"]
    old = h.client.get(f"/api/diagnosis/{d1['diagnosis_id']}").json()
    assert old["is_current"] is False  # 舊結果不視為目前狀態


@pytest.mark.parametrize("ftype,params,rule,code", [
    ("connection_down", {}, "R01", "CONNECTION_DOWN"),
    ("response_timeout", {"latency_ms": 6000}, "R02", "RESPONSE_TIMEOUT"),
    ("resource_overload", {"usage_pct": 99}, "R03", "RESOURCE_OVERLOAD"),
])
def test_end_to_end_fault_suggestion_recovery(h, ftype, params, rule, code):
    """T-04-04：故障 → 診斷 → 建議 → 清除 → 恢復確認。"""
    h.client.post("/api/faults", json={"target_id": "SIM-01", "fault_type": ftype, "params": params})
    d = h.client.post("/api/diagnosis", json={"target_id": "SIM-01"}).json()
    assert [x["rule_id"] for x in d["hits"]] == [rule]
    assert d["suggestions"][0]["fault_code"] == code
    h.client.delete("/api/faults/SIM-01")
    d2 = h.client.post("/api/diagnosis", json={"target_id": "SIM-01"}).json()
    assert rule not in [x["rule_id"] for x in d2["hits"]]
