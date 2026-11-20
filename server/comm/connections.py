"""組員一 T1.2／T1.3：A／B 連線對照表。

- 只接受合法角色 A、B；無效角色拒絕（INVALID_ROLE）。
- 同一角色已有連線時，新的連線一律拒絕（DUPLICATE_ROLE），除非帶著該角色上次取得的
  resume_token（同一個瀏覽器頁面重新連線），此時新連線取代舊連線。
- 斷線清理只移除「自己那一條」連線：舊連線晚到的斷線事件不能清掉新連線。
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .contract import ROLES


@dataclass
class Connection:
    role: str
    conn_id: str
    ws: Any
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload: dict) -> None:
        async with self.send_lock:
            await self.ws.send_json(payload)


@dataclass
class RegisterResult:
    ok: bool
    code: str = "OK"
    detail: str = ""
    conn: Optional[Connection] = None
    replaced: Optional[Connection] = None
    resume_token: str = ""


class ConnectionTable:
    def __init__(self) -> None:
        self._conns: dict[str, Connection] = {}
        self._tokens: dict[str, str] = {}

    def register(self, role: str, ws: Any, resume_token: Optional[str] = None) -> RegisterResult:
        if role not in ROLES:
            return RegisterResult(False, "INVALID_ROLE", f"無效角色：{role!r}，只接受 A 或 B")
        token_ok = bool(resume_token) and secrets.compare_digest(resume_token, self._tokens.get(role, ""))
        current = self._conns.get(role)
        if current is not None and not token_ok:
            return RegisterResult(False, "DUPLICATE_ROLE", f"角色 {role} 已有連線")
        conn = Connection(role, uuid.uuid4().hex[:12], ws)
        if not token_ok:
            self._tokens[role] = secrets.token_urlsafe(16)
        self._conns[role] = conn
        return RegisterResult(True, conn=conn, replaced=current, resume_token=self._tokens[role])

    def unregister(self, role: str, conn_id: str) -> bool:
        """只在目前登記的就是這條連線時才移除；回傳是否真的移除。"""
        current = self._conns.get(role)
        if current is not None and current.conn_id == conn_id:
            del self._conns[role]
            return True
        return False

    def get(self, role: str) -> Optional[Connection]:
        return self._conns.get(role)

    def is_online(self, role: str) -> bool:
        return role in self._conns

    def peers(self) -> dict[str, bool]:
        return {r: r in self._conns for r in ROLES}

    def snapshot(self) -> dict[str, Optional[str]]:
        return {r: (self._conns[r].conn_id if r in self._conns else None) for r in ROLES}

    def all(self) -> list[Connection]:
        return list(self._conns.values())
