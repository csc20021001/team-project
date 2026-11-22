"""Validated fault scenarios and evidence-linked, deterministic diagnostics."""

import json
import math
import time
import uuid


FAULT_SPECS = {
    "disconnect": {"description": "Recipient connection is unavailable", "params": {}},
    "timeout": {"description": "Recipient response is delayed", "params": {"delay_seconds": (0.0, 3600.0)}},
    "overload": {"description": "Recipient capacity is exhausted", "params": {"load": (0.0, 1.0), "threshold": (0.0, 1.0)}},
}

RULES = {
    "R01": {
        "title": "Connection interruption",
        "recommendation": "Restore the recipient connection, then replay queued messages.",
        "fault": "disconnect",
        "event_types": ("OFFLINE", "DISCONNECT"),
    },
    "R02": {
        "title": "Delivery timeout / missing ACK",
        "recommendation": "Check recipient responsiveness and network latency; retry after recovery.",
        "fault": "timeout",
        "event_types": ("TIMEOUT",),
    },
    "R03": {
        "title": "Resource overload",
        "recommendation": "Reduce load or increase capacity, then retry queued messages.",
        "fault": "overload",
        "event_types": ("OVERLOAD",),
    },
}

RECOMMENDATION_TABLE = [
    {"rule": rule_id, "fault": rule["fault"], "title": rule["title"], "action": rule["recommendation"]}
    for rule_id, rule in RULES.items()
]


def validate_fault(target, kind, params):
    """Reject unknown keys, non-finite numbers, and values outside documented bounds."""
    if target not in ("A", "B"):
        raise ValueError("target must be A or B")
    if kind not in FAULT_SPECS:
        raise ValueError("kind must be disconnect, timeout, or overload")
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    allowed = FAULT_SPECS[kind]["params"]
    unknown = set(params) - set(allowed)
    if unknown:
        raise ValueError("unknown parameter(s): " + ", ".join(sorted(unknown)))
    normalized = {}
    for name, value in params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        low, high = allowed[name]
        if not low <= value <= high:
            raise ValueError(f"{name} must be between {low:g} and {high:g}")
        normalized[name] = float(value)
    if kind == "overload":
        load = normalized.get("load", 1.0)
        threshold = normalized.get("threshold", 0.8)
        normalized = {"load": load, "threshold": threshold}
        if load <= threshold:
            raise ValueError("overload load must be greater than threshold")
    return normalized


def inject(store, target, kind, params=None):
    params = validate_fault(target, kind, params if params is not None else {})
    fault_id, now = str(uuid.uuid4()), time.time()
    with store.db() as conn:
        conn.execute(
            "INSERT INTO faults VALUES(?,?,?,?,?,?)",
            (fault_id, target, kind, 1, json.dumps(params), now),
        )
    event = store.event(
        "FAULT_INJECTED", source="system", target=target, simulated=True,
        details={"fault_id": fault_id, "kind": kind, "params": params},
    )
    return {"fault_id": fault_id, "target": target, "kind": kind, "params": params, "active": True, "event": event}


def clear(store, fault_id):
    with store.db() as conn:
        row = conn.execute("SELECT * FROM faults WHERE id=?", (fault_id,)).fetchone()
        if not row:
            return False
        if not row["active"]:
            return False
        conn.execute("UPDATE faults SET active=0 WHERE id=?", (fault_id,))
    store.event(
        "FAULT_CLEARED", source="system", target=row["target"], simulated=True,
        details={"fault_id": fault_id, "kind": row["kind"]},
    )
    return True


def active_fault(store, target):
    return store.active_fault(target)


def _validated_events(rows):
    """Normalize legacy and current rows while safely skipping malformed evidence."""
    events = []
    malformed = []
    for row in rows:
        try:
            value = dict(row)
            details = value.get("details", {})
            if isinstance(details, str):
                details = json.loads(details)
            if not isinstance(details, dict):
                raise ValueError("details must be an object")
            required = ("id", "type", "timestamp", "target", "simulated")
            missing = [key for key in required if key not in value or value[key] is None]
            if missing:
                malformed.append({"event_id": value.get("id"), "missing": missing})
                continue
            value["details"] = details
            value["simulated"] = bool(value["simulated"])
            events.append(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            malformed.append({"event_id": row.get("id") if isinstance(row, dict) else None, "error": "invalid_event_format"})
    return events, malformed


def _causal_fault_events(all_events, message, message_events):
    """Include only the injected fault IDs recorded by this message's outcome."""
    relevant = []
    linked_fault_ids = {
        event["details"].get("fault_id") for event in message_events
        if event["type"] in ("OFFLINE", "TIMEOUT", "OVERLOAD") and event["details"].get("fault_id")
    }
    for event in all_events:
        if event["type"] != "FAULT_INJECTED" or event["target"] != message["recipient"]:
            continue
        if event["details"].get("fault_id") in linked_fault_ids:
            relevant.append(event)
    return relevant


def diagnose(store, message_id=None, target=None):
    """Evaluate R01-R03; preserve all distinct evidence and rule hits."""
    if message_id:
        result = store.query(message_id)
        if not result:
            raise KeyError("message not found")
        message = result["message"]
        raw_events = result["events"]
        if target and target != message["recipient"]:
            raise KeyError("target does not match message recipient")
    else:
        message = None
        raw_events = store.event_list(1000)
        if target:
            raw_events = [e for e in raw_events if e["target"] == target]

    events, malformed = _validated_events(raw_events)
    if message:
        evidence_pool = events + _causal_fault_events(store.event_list(1000), message, events)
    else:
        evidence_pool = events
    # Evidence identity is stable: duplicate injection/event rows do not duplicate references.
    unique = {event["id"]: event for event in evidence_pool}
    evidence_pool = sorted(unique.values(), key=lambda e: (e["timestamp"], e["id"]))
    types = {event["type"] for event in events}
    hits = []

    for rule_id, rule in RULES.items():
        rule_events = []
        for event in evidence_pool:
            detail_kind = event["details"].get("kind")
            if detail_kind == rule["fault"] and event["type"] in ("FAULT_INJECTED", "FAULT_CLEARED"):
                rule_events.append(event)
            elif event["type"] in rule["event_types"]:
                rule_events.append(event)
        if rule_id == "R02" and "FORWARDED" in types and "ACKED" not in types:
            rule_events.extend(e for e in events if e["type"] == "FORWARDED")
        # A cleared fault remains useful historical evidence, but an injection alone is
        # diagnostic only when it affected a message or is part of global analysis.
        if message and rule_id and not any(e.get("message_id") == message_id for e in rule_events):
            triggering = {"disconnect": "OFFLINE", "timeout": "TIMEOUT", "overload": "OVERLOAD"}[rule["fault"]]
            if not any(e["type"] == triggering for e in events):
                rule_events = []
        rule_events = sorted({e["id"]: e for e in rule_events}.values(), key=lambda e: (e["timestamp"], e["id"]))
        if rule_events:
            hits.append({
                "rule": rule_id, "title": rule["title"],
                "evidence": rule_events, "evidence_event_ids": [e["id"] for e in rule_events],
                "recommendation": rule["recommendation"],
            })

    return {
        "message_id": message_id, "target": target or (message["recipient"] if message else None),
        "diagnoses": hits, "recommendations": RECOMMENDATION_TABLE,
        "events": events, "malformed_events": malformed,
        "diagnosed_at": time.time(), "rules_evaluated": list(RULES),
    }
