"""組員一 T2.1：訊息、ACK 與錯誤 JSON 契約（與組員二、組員四共同定義）。

客戶端 → 伺服器
    訊息：{"type": "message", "message_id": "M201", "to": "B", "text": "hello"}
          message_id 可省略（由伺服器產生）；"from" 可省略，若提供必須等於連線角色。
    ACK ：{"type": "ack", "message_id": "M201"}
    心跳：{"type": "ping"}

伺服器 → 客戶端
    歡迎：{"type": "welcome", "role": "A", "conn_id": "...", "resume_token": "...", "peers": {"A": true, "B": false}}
    訊息：{"type": "message", "message_id": "M201", "from": "A", "to": "B", "text": "hello",
           "timestamp": "...", "redelivery": false}
    狀態：{"type": "status", "message_id": "M201", "status": "RECEIVED|QUEUED|FORWARDED|DELIVERED|FAILED",
           "reason": null, "timestamp": "..."}
    上下線：{"type": "presence", "role": "B", "online": false, "reason": "..."}
    錯誤：{"type": "error", "code": "INVALID_JSON", "detail": "...", "message_id": null}
    心跳：{"type": "pong"}

錯誤代碼：INVALID_JSON、INVALID_FORMAT、INVALID_TYPE、MISSING_FIELD、INVALID_FIELD、INVALID_ROLE、
INVALID_TARGET、FROM_MISMATCH、DUPLICATE_ROLE、DUPLICATE_MESSAGE_ID、REJECTED、STORAGE_FAILED、
ACK_INVALID、ACK_UNKNOWN_ID、ACK_WRONG_ROLE、ACK_NOT_FORWARDED。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

ROLES = ("A", "B")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_TEXT_LEN = 2000
MAX_FRAME_LEN = 16_384


class ContractError(Exception):
    def __init__(self, code: str, detail: str, message_id: Optional[str] = None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.message_id = message_id


@dataclass
class ClientMessage:
    message_id: Optional[str]
    to: str
    text: str


@dataclass
class ClientAck:
    message_id: str


@dataclass
class ClientPing:
    pass


def _safe_id(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and ID_RE.match(v) else None


def parse_client(raw: Any, sender: str) -> ClientMessage | ClientAck | ClientPing:
    """驗證客戶端送來的一筆 WebSocket 文字；不合法時丟出 ContractError（不會轉送）。"""
    if not isinstance(raw, str):
        raise ContractError("INVALID_FORMAT", "只接受 JSON 文字訊框")
    if len(raw) > MAX_FRAME_LEN:
        raise ContractError("INVALID_FORMAT", f"訊框超過 {MAX_FRAME_LEN} 字元")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise ContractError("INVALID_JSON", "不是合法的 JSON") from None
    if not isinstance(data, dict):
        raise ContractError("INVALID_FORMAT", "JSON 必須是物件")
    mid_raw = data.get("message_id")
    mid = _safe_id(mid_raw)
    kind = data.get("type")
    if kind == "ping":
        return ClientPing()
    if kind == "ack":
        if mid_raw is None:
            raise ContractError("MISSING_FIELD", "ACK 缺少 message_id")
        if mid is None:
            raise ContractError("INVALID_FIELD", "message_id 格式錯誤（1～64 個英數字、底線或連字號）")
        return ClientAck(mid)
    if kind != "message":
        raise ContractError("INVALID_TYPE", "type 必須是 message、ack 或 ping", mid)
    if mid_raw is not None and mid is None:
        raise ContractError("INVALID_FIELD", "message_id 格式錯誤（1～64 個英數字、底線或連字號）")
    to = data.get("to")
    if to is None or to == "":
        raise ContractError("MISSING_FIELD", "缺少目的角色 to", mid)
    if to not in ROLES:
        raise ContractError("INVALID_ROLE", f"目的角色必須是 {' 或 '.join(ROLES)}", mid)
    if to == sender:
        raise ContractError("INVALID_TARGET", "不能傳送給自己", mid)
    frm = data.get("from")
    if frm is not None and frm != sender:
        raise ContractError("FROM_MISMATCH", f"from 必須等於連線角色 {sender}", mid)
    text = data.get("text")
    if text is None:
        raise ContractError("MISSING_FIELD", "缺少文字 text", mid)
    if not isinstance(text, str) or not text.strip():
        raise ContractError("INVALID_FIELD", "text 必須是非空字串", mid)
    if len(text) > MAX_TEXT_LEN:
        raise ContractError("INVALID_FIELD", f"text 不可超過 {MAX_TEXT_LEN} 字元", mid)
    return ClientMessage(mid, to, text)


# ------------------------------------------------------------ 伺服器 → 客戶端
def welcome(role: str, conn_id: str, resume_token: str, peers: dict) -> dict:
    return {"type": "welcome", "role": role, "conn_id": conn_id, "resume_token": resume_token, "peers": peers}


def deliver(msg: dict, redelivery: bool) -> dict:
    return {"type": "message", "message_id": msg["message_id"], "from": msg["from_role"], "to": msg["to_role"],
            "text": msg["text"], "timestamp": msg["created_at"], "redelivery": redelivery}


def status(message_id: str, status_: str, timestamp: str, reason: Optional[str] = None) -> dict:
    return {"type": "status", "message_id": message_id, "status": status_, "reason": reason, "timestamp": timestamp}


def presence(role: str, online: bool, reason: str = "") -> dict:
    return {"type": "presence", "role": role, "online": online, "reason": reason}


def error(code: str, detail: str, message_id: Optional[str] = None) -> dict:
    return {"type": "error", "code": code, "detail": detail, "message_id": message_id}
