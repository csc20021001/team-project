"""組員三：診斷服務（串接儲存層、診斷引擎、依據查詢與建議；T-02-02、T-03-03、T-04-03）。"""

from __future__ import annotations

import uuid
from typing import Optional

from server.storage import Storage
from server.storage.store import utc_iso

from .engine import diagnose_events, normalize_event
from .suggestions import suggestion_for, suggestions_for_result

EVIDENCE_AVAILABLE = "依據存在"
EVIDENCE_MISSING = "依據無法取得"


class DiagnosisService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def diagnose(self, target_id: str) -> dict:
        """執行（或重新執行）診斷。每次都讀取最新事件，不沿用舊結果。"""
        previous = self.storage.latest_diagnosis_id(target_id)
        events = [normalize_event(e) for e in self.storage.observations_for(target_id)]
        result = diagnose_events(target_id, events)
        result["diagnosis_id"] = "D" + uuid.uuid4().hex[:12].upper()
        result["created_at"] = utc_iso(self.storage.now())
        result["supersedes"] = previous
        result["suggestions"] = suggestions_for_result(result)
        self.storage.save_diagnosis(result)
        return self.get(result["diagnosis_id"])

    def get(self, diagnosis_id: str) -> Optional[dict]:
        d = self.storage.get_diagnosis(diagnosis_id)
        if d is None:
            return None
        d["is_current"] = self.storage.latest_diagnosis_id(d["target_id"]) == diagnosis_id
        return d

    def evidence(self, diagnosis_id: str) -> Optional[dict]:
        """依診斷結果取回原始事件；原始事件已不存在時回傳明確狀態。"""
        d = self.get(diagnosis_id)
        if d is None:
            return None
        items = []
        for hit in d["hits"]:
            raw = self.storage.get_event(hit["event_id"])
            if raw is None:
                items.append({"rule_id": hit["rule_id"], "event_id": hit["event_id"],
                              "status": EVIDENCE_MISSING})
                continue
            ev = normalize_event(raw)
            ref = hit["evidence"]
            items.append({
                "status": EVIDENCE_AVAILABLE,
                "rule_id": hit["rule_id"],
                "fault_name": hit["fault_name"],
                "event_id": ev["event_id"],
                "timestamp": ev["timestamp"],
                "target_id": ev["target_id"],
                "field": ref["field"],
                "observed_value": ev.get(ref["field"]),
                "threshold": ref["threshold"],
                "is_simulated": ev["is_simulated"],
            })
        return {"diagnosis_id": diagnosis_id, "target_id": d["target_id"], "status": d["status"],
                "is_current": d["is_current"], "evidence": items}

    def confirm_suggestion(self, fault_code: str, target_id: str) -> Optional[dict]:
        """記錄使用者已確認建議；只寫事件，不修改任何故障或連線狀態。"""
        s = suggestion_for(fault_code)
        if s is None:
            return None
        ev = self.storage.record_event("SUGGESTION_CONFIRMED", target_id=target_id,
                                       reason=f"使用者確認依「{s['fault_name']}」建議自行處理")
        return {"fault_code": fault_code, "target_id": target_id, "event_id": ev["event_id"],
                "auto_applied": False, "note": "系統不自動修改設備；請依建議自行處理後重新診斷"}
