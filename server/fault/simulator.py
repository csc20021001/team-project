"""組員三 US-01：注入與清除故障（T-01-01～T-01-04）。

T-01-01 注入資料規格：
    {"target_id": "B", "fault_type": "response_timeout", "params": {"latency_ms": 5000}}
    - target_id：1～32 字元英數字、底線或連字號；"A"、"B" 為訊息端，"SERVER" 為伺服器本身。
    - fault_type：connection_down（連線中斷）、response_timeout（回應逾時）、resource_overload（資源過載）；
      也接受中文名稱。
    - params：
        connection_down   → 無參數（或 {"connection_down": true}）
        response_timeout  → latency_ms：整數 1～60000，預設 5000
        resource_overload → usage_pct：整數 0～100，預設 95
基準狀態（作業二 D-04）：connection_down=false、latency_ms=100、usage_pct=30。

T-01-02 每次啟用都產生一筆 is_simulated=true、帶唯一事件 ID 的觀測事件（不覆寫真實資料）。
T-01-03 參數不合法時拒絕且不產生事件；清除時停止模擬並回復基準；已在基準狀態再清除
        不報錯、也不重複產生清除事件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from server.storage import Storage

FAULT_TYPES: dict[str, str] = {
    "connection_down": "連線中斷",
    "response_timeout": "回應逾時",
    "resource_overload": "資源過載",
}
_ALIASES = {v: k for k, v in FAULT_TYPES.items()}

BASELINE: dict[str, Any] = {"connection_down": False, "latency_ms": 100, "usage_pct": 30}
TARGET_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
SERVER_TARGET = "SERVER"

LATENCY_RANGE = (1, 60_000)
USAGE_RANGE = (0, 100)
DEFAULT_LATENCY_MS = 5000
DEFAULT_USAGE_PCT = 95


class FaultValidationError(ValueError):
    def __init__(self, field_name: str, detail: str):
        super().__init__(detail)
        self.field = field_name
        self.detail = detail


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


@dataclass
class FaultInjection:
    target_id: str
    fault_type: str
    params: dict = field(default_factory=dict)

    @classmethod
    def validate(cls, payload: Any) -> "FaultInjection":
        """參數檢查（T-01-03）。缺少目標或參數超界一律拒絕。"""
        if not isinstance(payload, dict):
            raise FaultValidationError("body", "注入請求必須是 JSON 物件")
        target = payload.get("target_id")
        if not isinstance(target, str) or not target.strip():
            raise FaultValidationError("target_id", "缺少測試目標 target_id")
        if not TARGET_RE.match(target):
            raise FaultValidationError("target_id", "target_id 只能是 1～32 個英數字、底線或連字號")
        ftype = payload.get("fault_type")
        if not isinstance(ftype, str) or not ftype:
            raise FaultValidationError("fault_type", "缺少故障類型 fault_type")
        ftype = _ALIASES.get(ftype, ftype)
        if ftype not in FAULT_TYPES:
            raise FaultValidationError("fault_type", f"未知的故障類型：{payload.get('fault_type')}")
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise FaultValidationError("params", "params 必須是 JSON 物件")
        allowed = {
            "connection_down": {"connection_down"},
            "response_timeout": {"latency_ms"},
            "resource_overload": {"usage_pct"},
        }[ftype]
        extra = set(params) - allowed
        if extra:
            raise FaultValidationError("params", f"不支援的參數：{', '.join(sorted(extra))}")
        clean: dict[str, Any] = {}
        if ftype == "connection_down":
            v = params.get("connection_down", True)
            if v is not True:
                raise FaultValidationError("params.connection_down", "connection_down 只能為 true")
            clean["connection_down"] = True
        elif ftype == "response_timeout":
            v = params.get("latency_ms", DEFAULT_LATENCY_MS)
            if not _is_int(v) or not LATENCY_RANGE[0] <= v <= LATENCY_RANGE[1]:
                raise FaultValidationError("params.latency_ms", "latency_ms 必須是 1～60000 的整數（毫秒）")
            clean["latency_ms"] = v
        else:
            v = params.get("usage_pct", DEFAULT_USAGE_PCT)
            if not _is_int(v) or not USAGE_RANGE[0] <= v <= USAGE_RANGE[1]:
                raise FaultValidationError("params.usage_pct", "usage_pct 必須是 0～100 的整數（百分比）")
            clean["usage_pct"] = v
        return cls(target.strip(), ftype, clean)


Listener = Callable[[str, str, str], Awaitable[None]]  # (action, target_id, fault_type)


class FaultSimulator:
    """模擬故障的啟用、清除與查詢，並提供通訊層（組員一）的故障控制介接。"""

    def __init__(self, storage: Storage):
        self.storage = storage
        self._active: dict[str, dict[str, dict]] = {}
        self._listeners: list[Listener] = []

    # -------------------------------------------------------------- 狀態
    def snapshot(self, target_id: str) -> dict[str, Any]:
        """目前觀測值：基準值套用所有啟用中的模擬故障。"""
        snap = dict(BASELINE)
        for params in self._active.get(target_id, {}).values():
            snap.update(params)
        return snap

    def status(self, target_id: str) -> dict:
        active = self._active.get(target_id, {})
        return {
            "target_id": target_id,
            "state": "模擬啟用中" if active else "基準狀態",
            "active_faults": [
                {"fault_type": t, "fault_name": FAULT_TYPES[t], "params": p} for t, p in active.items()
            ],
            "observed": self.snapshot(target_id),
        }

    def all_status(self) -> list[dict]:
        return [self.status(t) for t in sorted(self._active)]

    def is_active(self, target_id: str, fault_type: str) -> bool:
        return fault_type in self._active.get(target_id, {})

    # ------------------------------------------------------- 啟用 / 清除
    def enable(self, payload: Any) -> dict:
        req = FaultInjection.validate(payload)
        self._active.setdefault(req.target_id, {})[req.fault_type] = req.params
        snap = self.snapshot(req.target_id)
        ev = self.storage.record_event(
            "FAULT_INJECTED",
            target_id=req.target_id,
            is_simulated=True,
            reason=f"模擬{FAULT_TYPES[req.fault_type]}",
            connection_down=snap["connection_down"],
            latency_ms=snap["latency_ms"],
            usage_pct=snap["usage_pct"],
            detail={"fault_type": req.fault_type, "params": req.params},
        )
        return {
            "event_id": ev["event_id"],
            "timestamp": ev["ts"],
            "target_id": req.target_id,
            "fault_type": req.fault_type,
            "fault_name": FAULT_TYPES[req.fault_type],
            "params": req.params,
            "is_simulated": True,
            "observed": snap,
        }

    def clear(self, target_id: str, fault_type: Optional[str] = None) -> dict:
        if not isinstance(target_id, str) or not TARGET_RE.match(target_id or ""):
            raise FaultValidationError("target_id", "target_id 格式錯誤")
        if fault_type is not None:
            fault_type = _ALIASES.get(fault_type, fault_type)
            if fault_type not in FAULT_TYPES:
                raise FaultValidationError("fault_type", f"未知的故障類型：{fault_type}")
        active = self._active.get(target_id, {})
        to_clear = [fault_type] if fault_type else list(active)
        to_clear = [t for t in to_clear if t in active]
        if not to_clear:
            # 已在基準狀態：無錯誤、不重複產生清除事件
            return {"target_id": target_id, "cleared": [], "event_id": None, "state": self.status(target_id)["state"],
                    "observed": self.snapshot(target_id)}
        for t in to_clear:
            active.pop(t, None)
        if not active:
            self._active.pop(target_id, None)
        snap = self.snapshot(target_id)
        ev = self.storage.record_event(
            "FAULT_CLEARED",
            target_id=target_id,
            is_simulated=True,
            reason="清除模擬：" + "、".join(FAULT_TYPES[t] for t in to_clear),
            connection_down=snap["connection_down"],
            latency_ms=snap["latency_ms"],
            usage_pct=snap["usage_pct"],
            detail={"cleared": to_clear},
        )
        return {"target_id": target_id, "cleared": to_clear, "event_id": ev["event_id"],
                "state": self.status(target_id)["state"], "observed": snap}

    # ------------------------------------------------ 通知（給通訊層）
    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    async def notify(self, action: str, target_id: str, fault_type: str) -> None:
        for fn in self._listeners:
            await fn(action, target_id, fault_type)

    # ------------------------------------- 故障控制介接（組員一 T2.2 使用）
    def connection_down(self, role: str) -> bool:
        return self.is_active(role, "connection_down")

    def drops_ack(self, role: str) -> bool:
        """回應逾時：該端的 ACK 被視為逾時未到達。"""
        return self.is_active(role, "response_timeout")

    def reject_reason(self, from_role: str, to_role: str) -> Optional[str]:
        """資源過載：伺服器拒收（AC2.3 的「組員三設定的伺服器拒收情境」）。"""
        for target in (SERVER_TARGET, from_role, to_role):
            if self.is_active(target, "resource_overload"):
                pct = self._active[target]["resource_overload"]["usage_pct"]
                return f"模擬資源過載（{target} usage_pct={pct}%），伺服器拒收"
        return None
