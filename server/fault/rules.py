"""組員三 US-02（T-02-01）：規則對照表 R01～R03。

| 規則 | 故障代碼          | 故障名稱 | 嚴重程度 | 判斷欄位        | 門檻（含邊界）  |
|------|-------------------|----------|----------|-----------------|-----------------|
| R01  | CONNECTION_DOWN   | 連線中斷 | 高       | connection_down | == true         |
| R02  | RESPONSE_TIMEOUT  | 回應逾時 | 中       | latency_ms      | >= 3000 ms      |
| R03  | RESOURCE_OVERLOAD | 資源過載 | 中       | usage_pct       | >= 90 %         |

邊界：latency_ms=2999 不命中、3000 命中；usage_pct=89 不命中、90 命中。
多規則：全部命中規則都保留，先依嚴重程度（高→中），再依規則 ID 排序。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

SEVERITY_ORDER = {"高": 0, "中": 1}

LATENCY_THRESHOLD_MS = 3000
USAGE_THRESHOLD_PCT = 90


@dataclass(frozen=True)
class Rule:
    rule_id: str
    fault_code: str
    fault_name: str
    severity: str
    field: str
    threshold: str
    predicate: Callable[[Any], bool]

    def match(self, event: dict) -> bool:
        return bool(self.predicate(event[self.field]))


RULES: tuple[Rule, ...] = (
    Rule("R01", "CONNECTION_DOWN", "連線中斷", "高", "connection_down", "== true", lambda v: v is True),
    Rule("R02", "RESPONSE_TIMEOUT", "回應逾時", "中", "latency_ms", f">= {LATENCY_THRESHOLD_MS}",
         lambda v: v >= LATENCY_THRESHOLD_MS),
    Rule("R03", "RESOURCE_OVERLOAD", "資源過載", "中", "usage_pct", f">= {USAGE_THRESHOLD_PCT}",
         lambda v: v >= USAGE_THRESHOLD_PCT),
)

RULES_BY_CODE = {r.fault_code: r for r in RULES}


def rule_table() -> list[dict]:
    return [
        {"rule_id": r.rule_id, "fault_code": r.fault_code, "fault_name": r.fault_name,
         "severity": r.severity, "field": r.field, "threshold": r.threshold}
        for r in RULES
    ]
