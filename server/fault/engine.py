"""組員三 US-02／US-03：規則式診斷引擎與診斷依據（T-02-02、T-02-03、T-03-01、T-03-02）。

診斷狀態（作業二 D-05）：
- 「已識別」：至少命中一條規則，回傳所有命中規則、故障名稱、嚴重程度及對應事件 ID。
- 「未識別故障」：資料完整但沒有規則命中（不保證系統正常）。
- 「資料不足」：沒有事件、缺少必要欄位或欄位格式錯誤，並列出 missing_fields / invalid_fields。

診斷依據格式（T-03-01）：事件 ID、時間、目標、觀測值、規則門檻、模擬標記。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from .rules import RULES, SEVERITY_ORDER

STATUS_IDENTIFIED = "已識別"
STATUS_UNIDENTIFIED = "未識別故障"
STATUS_INSUFFICIENT = "資料不足"

REQUIRED_FIELDS = ("event_id", "target_id", "timestamp", "connection_down", "latency_ms", "usage_pct",
                   "is_simulated")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _valid_ts(v: Any) -> bool:
    if not isinstance(v, str) or not v:
        return False
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


_FIELD_CHECKS = {
    "event_id": lambda v: isinstance(v, str) and bool(v),
    "target_id": lambda v: isinstance(v, str) and bool(v),
    "timestamp": _valid_ts,
    "connection_down": lambda v: isinstance(v, bool),
    "latency_ms": lambda v: _is_int(v) and v >= 0,
    "usage_pct": lambda v: _is_int(v) and 0 <= v <= 100,
    "is_simulated": lambda v: isinstance(v, bool),
}


def normalize_event(raw: dict) -> dict:
    """把儲存層事件（ts）轉為診斷事件欄位（timestamp）。"""
    ev = dict(raw)
    if "timestamp" not in ev and "ts" in ev:
        ev["timestamp"] = ev["ts"]
    return ev


def check_fields(event: Any) -> tuple[list[str], list[str]]:
    if not isinstance(event, dict):
        return [], ["event"]
    missing = [f for f in REQUIRED_FIELDS if event.get(f) is None]
    invalid = [f for f in REQUIRED_FIELDS if f not in missing and not _FIELD_CHECKS[f](event[f])]
    return missing, invalid


def make_evidence(rule, event: dict) -> dict:
    return {
        "rule_id": rule.rule_id,
        "event_id": event["event_id"],
        "timestamp": event["timestamp"],
        "target_id": event["target_id"],
        "field": rule.field,
        "observed_value": event[rule.field],
        "threshold": rule.threshold,
        "is_simulated": event["is_simulated"],
    }


def diagnose_events(target_id: str, events: Iterable[Any]) -> dict:
    """純函式：依事件清單診斷單一目標。

    - 只採用 target_id 相符的事件（跨目標事件不混入）。
    - 相同 event_id 的重複事件只計一次。
    - 以最新一筆觀測事件代表目前狀態（清除後重新診斷不沿用舊結果）。
    """
    seen: set[str] = set()
    mine: list[dict] = []
    malformed: list[str] = []
    for raw in events:
        if not isinstance(raw, dict):
            malformed.append("event")
            continue
        ev = normalize_event(raw)
        if ev.get("target_id") != target_id:
            continue
        eid = ev.get("event_id")
        if isinstance(eid, str) and eid in seen:
            continue
        if isinstance(eid, str):
            seen.add(eid)
        mine.append(ev)

    base = {"target_id": target_id, "hits": [], "missing_fields": [], "invalid_fields": [], "event_ids": []}
    if not mine:
        return {**base, "status": STATUS_INSUFFICIENT,
                "missing_fields": list(REQUIRED_FIELDS) if not malformed else [],
                "invalid_fields": sorted(set(malformed)),
                "message": "沒有此目標的觀測事件，資料不足"}

    latest = mine[-1]
    missing, invalid = check_fields(latest)
    base["event_ids"] = [latest.get("event_id")]
    if missing or invalid:
        return {**base, "status": STATUS_INSUFFICIENT, "missing_fields": missing, "invalid_fields": invalid,
                "message": "事件缺少必要欄位或欄位格式錯誤，資料不足"}

    hits = []
    for rule in RULES:
        if rule.match(latest):
            hits.append({
                "rule_id": rule.rule_id,
                "fault_code": rule.fault_code,
                "fault_name": rule.fault_name,
                "severity": rule.severity,
                "event_id": latest["event_id"],
                "evidence": make_evidence(rule, latest),
            })
    hits.sort(key=lambda h: (SEVERITY_ORDER[h["severity"]], h["rule_id"]))
    if not hits:
        return {**base, "status": STATUS_UNIDENTIFIED,
                "message": "未命中任何規則（未識別故障，不保證系統正常）"}
    return {**base, "status": STATUS_IDENTIFIED, "hits": hits,
            "message": "命中 " + "、".join(h["rule_id"] for h in hits)}
