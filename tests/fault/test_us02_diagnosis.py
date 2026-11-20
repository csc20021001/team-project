"""組員三 US-02 規則式診斷（T-02-01～T-02-04）：正向、反向、邊界、多規則、無命中、缺欄位、格式錯誤。"""

import pytest

from server.fault.engine import (STATUS_IDENTIFIED, STATUS_INSUFFICIENT, STATUS_UNIDENTIFIED,
                                 diagnose_events)
from server.fault.rules import RULES, rule_table


def ev(eid="E1", target="SIM-01", ts="2026-11-21T00:00:00.000+00:00", down=False, lat=100, use=30, sim=True,
       **over):
    e = {"event_id": eid, "target_id": target, "timestamp": ts, "connection_down": down, "latency_ms": lat,
         "usage_pct": use, "is_simulated": sim}
    e.update(over)
    return e


def test_rule_table_definition():
    t = rule_table()
    assert [r["rule_id"] for r in t] == ["R01", "R02", "R03"]
    assert [r["fault_name"] for r in t] == ["連線中斷", "回應逾時", "資源過載"]
    assert [r["severity"] for r in t] == ["高", "中", "中"]


@pytest.mark.parametrize("event,rule", [
    (ev(down=True), "R01"),
    (ev(lat=5000), "R02"),
    (ev(use=95), "R03"),
])
def test_positive_single_rule(event, rule):
    r = diagnose_events("SIM-01", [event])
    assert r["status"] == STATUS_IDENTIFIED
    assert [h["rule_id"] for h in r["hits"]] == [rule]
    h = r["hits"][0]
    assert h["event_id"] == "E1" and h["fault_name"] and h["severity"] in ("高", "中")


@pytest.mark.parametrize("lat,use,expected", [
    (2999, 89, []),
    (3000, 89, ["R02"]),
    (2999, 90, ["R03"]),
    (3000, 90, ["R02", "R03"]),
    (3001, 100, ["R02", "R03"]),
])
def test_boundaries(lat, use, expected):
    r = diagnose_events("SIM-01", [ev(lat=lat, use=use)])
    assert [h["rule_id"] for h in r["hits"]] == expected


def test_multi_rule_sorted_by_severity_then_id():
    r = diagnose_events("SIM-01", [ev(down=True, lat=9000, use=99)])
    assert [(h["rule_id"], h["severity"]) for h in r["hits"]] == [("R01", "高"), ("R02", "中"), ("R03", "中")]


def test_no_rule_hit_is_unidentified_not_normal():
    r = diagnose_events("SIM-01", [ev()])
    assert r["status"] == STATUS_UNIDENTIFIED and r["hits"] == []
    assert "不保證" in r["message"]


@pytest.mark.parametrize("missing", ["connection_down", "latency_ms", "usage_pct", "timestamp", "is_simulated"])
def test_missing_field_is_insufficient(missing):
    e = ev(down=True)
    del e[missing]
    r = diagnose_events("SIM-01", [e])
    assert r["status"] == STATUS_INSUFFICIENT and r["missing_fields"] == [missing] and r["hits"] == []


@pytest.mark.parametrize("field,value", [
    ("connection_down", "yes"), ("latency_ms", "3000"), ("latency_ms", -5), ("latency_ms", 3.5),
    ("usage_pct", 150), ("usage_pct", True), ("timestamp", "yesterday"), ("is_simulated", 1),
])
def test_format_error_is_insufficient(field, value):
    r = diagnose_events("SIM-01", [ev(**{field: value})])
    assert r["status"] == STATUS_INSUFFICIENT and r["invalid_fields"] == [field]


def test_no_events_is_insufficient():
    r = diagnose_events("SIM-01", [])
    assert r["status"] == STATUS_INSUFFICIENT and "latency_ms" in r["missing_fields"]


def test_non_dict_events_are_format_errors():
    r = diagnose_events("SIM-01", ["garbage", 42])
    assert r["status"] == STATUS_INSUFFICIENT and r["invalid_fields"] == ["event"]


def test_latest_event_represents_current_state():
    r = diagnose_events("SIM-01", [ev("E1", down=True), ev("E2")])
    assert r["status"] == STATUS_UNIDENTIFIED


def test_rule_match_uses_declared_fields():
    for rule in RULES:
        assert rule.field in ("connection_down", "latency_ms", "usage_pct")


def test_api_diagnosis_end_to_end(h):
    h.client.post("/api/faults", json={"target_id": "SIM-01", "fault_type": "connection_down"})
    h.client.post("/api/faults", json={"target_id": "SIM-01", "fault_type": "resource_overload",
                                       "params": {"usage_pct": 90}})
    d = h.client.post("/api/diagnosis", json={"target_id": "SIM-01"}).json()
    assert d["status"] == "已識別" and [x["rule_id"] for x in d["hits"]] == ["R01", "R03"]
    assert h.client.post("/api/diagnosis", json={"target_id": ""}).status_code == 400
    assert h.client.post("/api/diagnosis", json=[]).status_code == 400
    none = h.client.post("/api/diagnosis", json={"target_id": "NEVER"}).json()
    assert none["status"] == "資料不足"
