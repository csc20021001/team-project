"""組員三 US-04（T-04-01、T-04-02）：三類故障修復建議表。

建議只供參考，必須由使用者確認後自行執行；本模組「不」自動修改任何實際設備或狀態。
"""

from __future__ import annotations

SUGGESTIONS: dict[str, dict] = {
    "CONNECTION_DOWN": {
        "fault_code": "CONNECTION_DOWN",
        "fault_name": "連線中斷",
        "check_steps": [
            "確認目標端瀏覽器頁面仍開啟，狀態列顯示「已連線」",
            "呼叫 GET /api/connections 確認連線表中是否有該角色",
            "以 Message ID 查詢事件，確認是否出現 DISCONNECTED 或 QUEUED（模擬連線中斷）",
        ],
        "actions": [
            "重新整理或重新連線目標端頁面",
            "若為模擬故障，於故障注入頁清除「連線中斷」",
            "確認伺服器仍在執行（GET /api/health 回應 200）",
        ],
        "cautions": [
            "同一角色只能有一條連線，請勿同時開兩個相同角色頁面",
            "重連後待送訊息會以原 Message ID 補送，畫面會自動去重",
        ],
        "recovery_check": "重新診斷不再命中 R01，且待送訊息狀態轉為 FORWARDED／DELIVERED",
    },
    "RESPONSE_TIMEOUT": {
        "fault_code": "RESPONSE_TIMEOUT",
        "fault_name": "回應逾時",
        "check_steps": [
            "以 Message ID 查詢事件，確認是否只有 FORWARDED 而沒有 DELIVERED",
            "檢查是否有 ACK_DROPPED（模擬回應逾時）或 RESENT 事件",
            "確認接收端頁面已開啟「自動回 ACK」",
        ],
        "actions": [
            "若為模擬故障，於故障注入頁清除「回應逾時」",
            "確認接收端網路與頁面未凍結後重新連線",
            "等待伺服器以原 Message ID 重送並觀察 ACK",
        ],
        "cautions": [
            "未收到有效 ACK 前不得視為已送達",
            "重送次數用盡會標記 FAILED，需人工確認",
        ],
        "recovery_check": "重新診斷不再命中 R02，新訊息可在逾時前收到 DELIVERED",
    },
    "RESOURCE_OVERLOAD": {
        "fault_code": "RESOURCE_OVERLOAD",
        "fault_name": "資源過載",
        "check_steps": [
            "查詢事件是否出現 REJECTED（伺服器拒收）",
            "確認 usage_pct 觀測值與門檻 90%",
            "確認是否有異常大量訊息送入",
        ],
        "actions": [
            "若為模擬故障，於故障注入頁清除「資源過載」",
            "降低傳送頻率後再傳送新訊息",
            "必要時重新啟動伺服器（需使用者自行確認）",
        ],
        "cautions": [
            "拒收的訊息不會被保存，需由發送端重新傳送",
            "重新啟動伺服器會中斷所有連線",
        ],
        "recovery_check": "重新診斷不再命中 R03，新訊息可正常收到 RECEIVED／FORWARDED",
    },
}

UNIDENTIFIED_GUIDANCE = {
    "fault_code": None,
    "fault_name": "未識別故障",
    "guidance": [
        "目前資料未命中 R01～R03，不代表系統一定正常",
        "請補充觀測資料：connection_down、latency_ms、usage_pct 與事件時間",
        "可依 Message ID 查詢訊息事件以補充調查",
    ],
}

INSUFFICIENT_GUIDANCE = {
    "fault_code": None,
    "fault_name": "資料不足",
    "guidance": [
        "請補齊缺漏或格式錯誤的欄位後重新診斷",
        "必要欄位：event_id、target_id、timestamp、connection_down、latency_ms、usage_pct、is_simulated",
    ],
}


def suggestion_for(fault_code: str) -> dict | None:
    s = SUGGESTIONS.get(fault_code)
    if s is None:
        return None
    return {**s, "requires_user_confirmation": True, "auto_apply": False}


def suggestions_for_result(result: dict) -> list[dict]:
    """多故障分別提供建議；未識別或資料不足只提供補充資料指引。"""
    if result["status"] == "已識別":
        return [suggestion_for(h["fault_code"]) for h in result["hits"]]
    if result["status"] == "未識別故障":
        return [dict(UNIDENTIFIED_GUIDANCE)]
    return [dict(INSUFFICIENT_GUIDANCE)]
