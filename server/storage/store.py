"""組員二：SQLite 儲存介面。

提供給組員一（通訊層）的介面（分工文件「介面與相依關係總表」）：
- 儲存介面與待送佇列：save_message / mark_forwarded / mark_queued / pending_for（T2.2）
- ACK 驗證邏輯：validate_ack / mark_delivered（T2.3、AC2.2）
- 事件入口：record_event（T3.1）
- 查詢介面：get_message / get_events / list_messages（T3.2、AC3.1）
- 補送入口：pending_for（T1.3、AC1.2）——補送一律沿用原訊息 ID
- 逾時與重送：due_for_resend / mark_failed（S03）

另提供組員三使用的觀測事件（含 connection_down / latency_ms / usage_pct 與模擬標記）
與診斷結果保存。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional


class MessageStatus:
    RECEIVED = "RECEIVED"  # 伺服器已成功保存
    QUEUED = "QUEUED"  # 接收端離線或不可達，進入待送佇列
    FORWARDED = "FORWARDED"  # 已轉送給接收端，尚未收到有效 ACK
    DELIVERED = "DELIVERED"  # 收到接收端有效 ACK
    FAILED = "FAILED"  # 重送次數用盡仍未收到 ACK

    ALL = (RECEIVED, QUEUED, FORWARDED, DELIVERED, FAILED)


class StorageError(Exception):
    """儲存層錯誤（例如寫入失敗）。"""


class DuplicateMessageId(StorageError):
    """同一個 Message ID 已被不同內容使用。"""


@dataclass
class AckResult:
    ok: bool
    code: str = "OK"
    reason: str = ""
    already_delivered: bool = False
    message: Optional[dict] = None


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT NOT NULL UNIQUE,
    from_role    TEXT NOT NULL,
    to_role      TEXT NOT NULL,
    text         TEXT NOT NULL,
    status       TEXT NOT NULL,
    status_reason TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    last_sent_ts REAL,
    delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    ts           TEXT NOT NULL,
    message_id   TEXT,
    role         TEXT,
    stage        TEXT NOT NULL,
    reason       TEXT,
    target_id    TEXT,
    is_simulated INTEGER NOT NULL DEFAULT 0,
    connection_down INTEGER,
    latency_ms   INTEGER,
    usage_pct    INTEGER,
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_message ON events(message_id);
CREATE INDEX IF NOT EXISTS idx_events_target ON events(target_id);
CREATE TABLE IF NOT EXISTS diagnoses (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    diagnosis_id TEXT NOT NULL UNIQUE,
    target_id    TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    body         TEXT NOT NULL
);
"""

# 觀測事件的階段（組員三的故障注入／清除會寫入這些事件）
OBSERVATION_STAGES = ("FAULT_INJECTED", "FAULT_CLEARED", "OBSERVATION")


class Storage:
    """以 SQLite 實作的儲存層。所有方法皆為執行緒安全。"""

    def __init__(self, db_path: str = ":memory:", clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self.fail_next_save = False  # 測試掛鉤：模擬保存失敗

    # ------------------------------------------------------------------ 共用
    def now(self) -> float:
        return self._clock()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def new_message_id() -> str:
        """由伺服器產生的訊息 ID（客戶端未提供時使用）。"""
        return "M" + uuid.uuid4().hex[:16].upper()

    @staticmethod
    def new_event_id() -> str:
        return "E" + uuid.uuid4().hex[:16].upper()

    @staticmethod
    def _msg(row: sqlite3.Row | None) -> Optional[dict]:
        if row is None:
            return None
        d = dict(row)
        d.pop("seq", None)
        d.pop("last_sent_ts", None)
        return d

    @staticmethod
    def _event(row: sqlite3.Row | None) -> Optional[dict]:
        if row is None:
            return None
        d = dict(row)
        d["is_simulated"] = bool(d["is_simulated"])
        if d["connection_down"] is not None:
            d["connection_down"] = bool(d["connection_down"])
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        return d

    # ------------------------------------------------------------- 訊息保存
    def save_message(self, message_id: str, from_role: str, to_role: str, text: str) -> tuple[dict, bool]:
        """保存訊息。回傳 (訊息, 是否新建)。

        同一 ID、同內容重送視為冪等（回傳既有訊息，created=False）；
        同一 ID、不同內容則丟出 DuplicateMessageId。
        """
        with self._lock:
            if self.fail_next_save:
                self.fail_next_save = False
                raise StorageError("simulated storage failure")
            existing = self.get_message(message_id)
            if existing is not None:
                same = (existing["from_role"], existing["to_role"], existing["text"]) == (from_role, to_role, text)
                if not same:
                    raise DuplicateMessageId(message_id)
                return existing, False
            now = utc_iso(self.now())
            try:
                self._db.execute(
                    "INSERT INTO messages(message_id, from_role, to_role, text, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (message_id, from_role, to_role, text, MessageStatus.RECEIVED, now, now),
                )
            except sqlite3.Error as exc:  # pragma: no cover - 防禦
                raise StorageError(str(exc)) from exc
            return self.get_message(message_id), True

    def get_message(self, message_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
            return self._msg(row)

    def list_messages(self, role: Optional[str] = None, limit: int = 50) -> list[dict]:
        with self._lock:
            if role:
                rows = self._db.execute(
                    "SELECT * FROM messages WHERE from_role=? OR to_role=? ORDER BY seq DESC LIMIT ?",
                    (role, role, limit),
                ).fetchall()
            else:
                rows = self._db.execute("SELECT * FROM messages ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
            return [self._msg(r) for r in reversed(rows)]

    def _set(self, message_id: str, **fields: Any) -> Optional[dict]:
        fields["updated_at"] = utc_iso(self.now())
        cols = ", ".join(f"{k}=?" for k in fields)
        self._db.execute(f"UPDATE messages SET {cols} WHERE message_id=?", (*fields.values(), message_id))
        return self.get_message(message_id)

    def mark_forwarded(self, message_id: str) -> Optional[dict]:
        """已交給接收端連線。DELIVERED 不會被降級。"""
        with self._lock:
            msg = self.get_message(message_id)
            if msg is None:
                return None
            fields: dict[str, Any] = {"attempts": msg["attempts"] + 1, "last_sent_ts": self.now()}
            if msg["status"] != MessageStatus.DELIVERED:
                fields.update(status=MessageStatus.FORWARDED, status_reason=None)
            return self._set(message_id, **fields)

    def mark_queued(self, message_id: str, reason: str) -> Optional[dict]:
        with self._lock:
            msg = self.get_message(message_id)
            if msg is None or msg["status"] == MessageStatus.DELIVERED:
                return msg
            return self._set(message_id, status=MessageStatus.QUEUED, status_reason=reason)

    def mark_failed(self, message_id: str, reason: str) -> Optional[dict]:
        with self._lock:
            msg = self.get_message(message_id)
            if msg is None or msg["status"] == MessageStatus.DELIVERED:
                return msg
            return self._set(message_id, status=MessageStatus.FAILED, status_reason=reason)

    # --------------------------------------------------------------- ACK
    def validate_ack(self, message_id: Any, role: str) -> AckResult:
        """ACK 驗證：只有「指定接收者」對「已轉送過」的訊息送出的 ACK 才有效。"""
        with self._lock:
            if not isinstance(message_id, str) or not message_id:
                return AckResult(False, "ACK_INVALID", "ACK 缺少 message_id")
            msg = self.get_message(message_id)
            if msg is None:
                return AckResult(False, "ACK_UNKNOWN_ID", f"未知的 Message ID：{message_id}")
            if msg["to_role"] != role:
                return AckResult(False, "ACK_WRONG_ROLE", f"{role} 不是 {message_id} 的接收者", message=msg)
            if msg["status"] == MessageStatus.DELIVERED:
                return AckResult(True, "OK", "重複 ACK", already_delivered=True, message=msg)
            if msg["attempts"] <= 0:
                return AckResult(False, "ACK_NOT_FORWARDED", f"{message_id} 尚未轉送，不接受 ACK", message=msg)
            return AckResult(True, message=msg)

    def mark_delivered(self, message_id: str) -> Optional[dict]:
        with self._lock:
            return self._set(
                message_id,
                status=MessageStatus.DELIVERED,
                status_reason=None,
                delivered_at=utc_iso(self.now()),
            )

    # ---------------------------------------------------- 待送佇列 / 重送
    def pending_for(self, role: str) -> list[dict]:
        """補送入口：接收端（重新）上線時應補送的訊息，依原順序、保留原 ID。

        包含 QUEUED（從未成功轉送或轉送時對方已離線）與 FORWARDED（已轉送但未 ACK，
        可能在斷線時遺失）。
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM messages WHERE to_role=? AND status IN (?,?,?) ORDER BY seq",
                (role, MessageStatus.QUEUED, MessageStatus.FORWARDED, MessageStatus.RECEIVED),
            ).fetchall()
            return [self._msg(r) for r in rows]

    def due_for_resend(self, ack_timeout: float, now: Optional[float] = None) -> list[dict]:
        """逾時判斷：已轉送但超過 ack_timeout 秒仍未 ACK 的訊息。"""
        now = self.now() if now is None else now
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM messages WHERE status=? AND last_sent_ts IS NOT NULL AND last_sent_ts + ? <= ?"
                " ORDER BY seq",
                (MessageStatus.FORWARDED, ack_timeout, now),
            ).fetchall()
            return [self._msg(r) for r in rows]

    # -------------------------------------------------------------- 事件
    def record_event(
        self,
        stage: str,
        *,
        message_id: Optional[str] = None,
        role: Optional[str] = None,
        reason: Optional[str] = None,
        target_id: Optional[str] = None,
        is_simulated: bool = False,
        connection_down: Optional[bool] = None,
        latency_ms: Optional[int] = None,
        usage_pct: Optional[int] = None,
        detail: Optional[dict] = None,
    ) -> dict:
        """事件入口：保留事件 ID、時間、角色、階段、原因與模擬標記。"""
        event_id = self.new_event_id()
        with self._lock:
            self._db.execute(
                "INSERT INTO events(event_id, ts, message_id, role, stage, reason, target_id, is_simulated,"
                " connection_down, latency_ms, usage_pct, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    utc_iso(self.now()),
                    message_id,
                    role,
                    stage,
                    reason,
                    target_id,
                    1 if is_simulated else 0,
                    None if connection_down is None else int(bool(connection_down)),
                    latency_ms,
                    usage_pct,
                    json.dumps(detail, ensure_ascii=False) if detail else None,
                ),
            )
            return self.get_event(event_id)

    def get_event(self, event_id: str) -> Optional[dict]:
        with self._lock:
            return self._event(self._db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone())

    def delete_event(self, event_id: str) -> bool:
        """維運用（例如清理舊資料）；測試用來模擬「依據已不存在」。"""
        with self._lock:
            return self._db.execute("DELETE FROM events WHERE event_id=?", (event_id,)).rowcount > 0

    def get_events(self, message_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM events WHERE message_id=? ORDER BY seq", (message_id,)).fetchall()
            return [self._event(r) for r in rows]

    def list_events(
        self, *, stages: Optional[Iterable[str]] = None, role: Optional[str] = None,
        target_id: Optional[str] = None, limit: int = 100,
    ) -> list[dict]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if stages:
            stages = list(stages)
            sql += f" AND stage IN ({','.join('?' * len(stages))})"
            args += stages
        if role:
            sql += " AND role=?"
            args.append(role)
        if target_id:
            sql += " AND target_id=?"
            args.append(target_id)
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
            return [self._event(r) for r in reversed(rows)]

    def observations_for(self, target_id: str) -> list[dict]:
        """組員三診斷用：某目標的觀測事件（依時間順序）。"""
        return self.list_events(stages=OBSERVATION_STAGES, target_id=target_id, limit=10_000)

    # ------------------------------------------------------------ 診斷結果
    def save_diagnosis(self, diagnosis: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO diagnoses(diagnosis_id, target_id, created_at, body) VALUES (?,?,?,?)",
                (diagnosis["diagnosis_id"], diagnosis["target_id"], diagnosis["created_at"],
                 json.dumps(diagnosis, ensure_ascii=False)),
            )

    def get_diagnosis(self, diagnosis_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT body FROM diagnoses WHERE diagnosis_id=?", (diagnosis_id,)).fetchone()
            return json.loads(row["body"]) if row else None

    def latest_diagnosis_id(self, target_id: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute(
                "SELECT diagnosis_id FROM diagnoses WHERE target_id=? ORDER BY seq DESC LIMIT 1", (target_id,)
            ).fetchone()
            return row["diagnosis_id"] if row else None
