"""組員一：通訊服務——WebSocket 生命週期、雙向路由、ACK 介接與事件記錄。

T1.2  連線登記／拒絕／斷線清理（ConnectionTable）
T1.3  重連與離線通知：上下線廣播 presence；重連時呼叫組員二的補送入口（pending_for），以原 ID 補送
T2.2  雙向路由：驗證 → 故障控制（組員三）→ 先保存（組員二）→ 再轉送；接收端離線或模擬斷線時進待送佇列
T2.3  ACK：交給組員二 validate_ack；轉送後只回報 FORWARDED，驗證通過才回報 DELIVERED
T3.1  所有接收、轉送、連線、錯誤事件都交給組員二 record_event
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

from server.fault import FaultSimulator
from server.storage import DuplicateMessageId, MessageStatus, Storage, StorageError
from server.storage.store import utc_iso

from . import contract
from .connections import Connection, ConnectionTable

log = logging.getLogger("messagetrace.comm")

CLOSE_INVALID_ROLE = 4400
CLOSE_DUPLICATE_ROLE = 4409
CLOSE_REPLACED = 4000


class CommService:
    def __init__(self, storage: Storage, faults: FaultSimulator, *, ack_timeout: float = 5.0,
                 max_attempts: int = 3):
        self.storage = storage
        self.faults = faults
        self.connections = ConnectionTable()
        self.ack_timeout = ack_timeout
        self.max_attempts = max_attempts
        faults.add_listener(self._on_fault_change)

    # ------------------------------------------------------------ helpers
    def _now(self) -> str:
        return utc_iso(self.storage.now())

    async def _send(self, conn: Optional[Connection], payload: dict) -> bool:
        if conn is None:
            return False
        try:
            await conn.send(payload)
            return True
        except Exception:  # 連線已斷，交給斷線流程處理
            return False

    async def _send_role(self, role: str, payload: dict) -> bool:
        return await self._send(self.connections.get(role), payload)

    async def _notify_sender(self, msg: dict, status: str, reason: Optional[str] = None) -> None:
        await self._send_role(msg["from_role"], contract.status(msg["message_id"], status, self._now(), reason))

    async def _broadcast_presence(self, role: str, online: bool, reason: str) -> None:
        for c in self.connections.all():
            if c.role != role:
                await self._send(c, contract.presence(role, online, reason))

    # ------------------------------------------------- WebSocket 生命週期
    async def handle_websocket(self, ws: WebSocket, role: str, resume_token: Optional[str]) -> None:
        await ws.accept()
        reg = self.connections.register(role, ws, resume_token)
        if not reg.ok:
            self.storage.record_event("CONNECTION_REJECTED", role=role if role in contract.ROLES else None,
                                      reason=f"{reg.code}: {reg.detail}")
            await ws.send_json(contract.error(reg.code, reg.detail))
            await ws.close(code=CLOSE_INVALID_ROLE if reg.code == "INVALID_ROLE" else CLOSE_DUPLICATE_ROLE,
                           reason=reg.code)
            return
        conn = reg.conn
        if reg.replaced is not None:
            self.storage.record_event("CONNECTION_REPLACED", role=role,
                                      reason=f"同一頁面重連，取代舊連線 {reg.replaced.conn_id}")
            try:
                await reg.replaced.ws.close(code=CLOSE_REPLACED, reason="REPLACED")
            except Exception:
                pass
        self.storage.record_event("CONNECTED", role=role, detail={"conn_id": conn.conn_id})
        await conn.send(contract.welcome(role, conn.conn_id, reg.resume_token, self.connections.peers()))
        await self._broadcast_presence(role, True, "上線")
        await self.flush_pending(role)
        try:
            while True:
                raw = await ws.receive_text()
                await self.handle_frame(conn, raw)
        except WebSocketDisconnect:
            pass
        except Exception:  # pragma: no cover - 防禦：不讓例外外洩
            log.exception("websocket handler error")
        finally:
            await self._on_disconnect(conn)

    async def _on_disconnect(self, conn: Connection) -> None:
        if self.connections.unregister(conn.role, conn.conn_id):
            self.storage.record_event("DISCONNECTED", role=conn.role, reason="連線中斷",
                                      detail={"conn_id": conn.conn_id})
            await self._broadcast_presence(conn.role, False, "離線")
        else:
            # 舊連線晚到的關閉事件：不清除新連線（T1.3）
            self.storage.record_event("STALE_CLOSE_IGNORED", role=conn.role,
                                      reason="舊連線關閉，不影響目前連線", detail={"conn_id": conn.conn_id})

    # --------------------------------------------------------- 收到訊框
    async def handle_frame(self, conn: Connection, raw: Any) -> None:
        try:
            parsed = contract.parse_client(raw, conn.role)
        except contract.ContractError as e:
            self.storage.record_event("INVALID", role=conn.role, message_id=e.message_id,
                                      reason=f"{e.code}: {e.detail}")
            await self._send(conn, contract.error(e.code, e.detail, e.message_id))
            return
        if isinstance(parsed, contract.ClientPing):
            await self._send(conn, {"type": "pong"})
        elif isinstance(parsed, contract.ClientAck):
            await self.handle_ack(conn, parsed.message_id)
        else:
            await self.route_message(conn, parsed)

    # ------------------------------------------------------------ 路由
    async def route_message(self, conn: Connection, m: contract.ClientMessage) -> None:
        sender = conn.role
        mid = m.message_id or self.storage.new_message_id()
        reject = self.faults.reject_reason(sender, m.to)
        if reject:
            self.storage.record_event("REJECTED", message_id=mid, role=sender, reason=reject, is_simulated=True)
            await self._send(conn, contract.error("REJECTED", reject, mid))
            return
        try:
            msg, created = self.storage.save_message(mid, sender, m.to, m.text)
        except DuplicateMessageId:
            self.storage.record_event("INVALID", message_id=mid, role=sender,
                                      reason="DUPLICATE_MESSAGE_ID: 此 ID 已被不同內容使用")
            await self._send(conn, contract.error("DUPLICATE_MESSAGE_ID", "此 Message ID 已被不同內容使用", mid))
            return
        except StorageError as e:
            self.storage.record_event("STORAGE_FAILED", message_id=mid, role=sender, reason=str(e))
            await self._send(conn, contract.error("STORAGE_FAILED", "訊息保存失敗，未轉送", mid))
            return
        if not created:
            # 發送端以同一 ID 重送：冪等，只回報目前狀態
            await self._send(conn, contract.status(mid, msg["status"], self._now(), msg["status_reason"]))
            return
        self.storage.record_event("RECEIVED", message_id=mid, role=sender, reason="已保存")
        await self._send(conn, contract.status(mid, MessageStatus.RECEIVED, self._now()))
        await self.deliver(msg)

    async def deliver(self, msg: dict, stage: str = "FORWARDED") -> str:
        """轉送一則已保存的訊息；回傳最後狀態（FORWARDED 或 QUEUED）。"""
        to = msg["to_role"]
        mid = msg["message_id"]
        reason, simulated = None, False
        if self.faults.connection_down(to):
            reason, simulated = f"模擬連線中斷：{to} 不可達", True
        elif not self.connections.is_online(to):
            reason = f"接收端 {to} 離線"
        else:
            ok = await self._send_role(to, contract.deliver(msg, redelivery=stage != "FORWARDED"))
            if not ok:
                reason = f"轉送給 {to} 失敗"
        if reason:
            current = self.storage.get_message(mid)
            if current and current["status"] == MessageStatus.DELIVERED:
                return MessageStatus.DELIVERED
            self.storage.mark_queued(mid, reason)
            self.storage.record_event("QUEUED", message_id=mid, role=to, reason=reason, is_simulated=simulated)
            await self._notify_sender(msg, MessageStatus.QUEUED, reason)
            return MessageStatus.QUEUED
        updated = self.storage.mark_forwarded(mid)
        self.storage.record_event(stage, message_id=mid, role=to, reason=f"已交給 {to}，等待 ACK",
                                  detail={"attempt": updated["attempts"]})
        if updated["status"] != MessageStatus.DELIVERED:
            # 轉送階段只回報 FORWARDED，不得回報 DELIVERED
            await self._notify_sender(msg, MessageStatus.FORWARDED)
        return updated["status"]

    async def flush_pending(self, role: str) -> int:
        """補送：接收端上線（或模擬斷線清除）後，以原 Message ID 補送待送訊息。"""
        if self.faults.connection_down(role) or not self.connections.is_online(role):
            return 0
        n = 0
        for msg in self.storage.pending_for(role):
            await self.deliver(msg, stage="REDELIVERED")
            n += 1
        return n

    # ------------------------------------------------------------- ACK
    async def handle_ack(self, conn: Connection, message_id: str) -> None:
        role = conn.role
        if self.faults.drops_ack(role):
            self.storage.record_event("ACK_DROPPED", message_id=message_id, role=role,
                                      reason="模擬回應逾時：ACK 未送達伺服器", is_simulated=True)
            return
        result = self.storage.validate_ack(message_id, role)
        if not result.ok:
            self.storage.record_event("ACK_REJECTED", message_id=message_id if result.message else None,
                                      role=role, reason=f"{result.code}: {result.reason}",
                                      detail={"ack_message_id": message_id})
            await self._send(conn, contract.error(result.code, result.reason, message_id))
            return
        if result.already_delivered:
            self.storage.record_event("ACK_DUPLICATE", message_id=message_id, role=role, reason="重複 ACK，忽略")
            return
        msg = self.storage.mark_delivered(message_id)
        self.storage.record_event("DELIVERED", message_id=message_id, role=role, reason="收到有效 ACK")
        await self._notify_sender(msg, MessageStatus.DELIVERED)

    # ------------------------------------------------- 逾時與重送（組員二策略）
    async def resend_tick(self, now: Optional[float] = None) -> list[str]:
        """檢查逾時未 ACK 的訊息：未達上限以原 ID 重送，達上限標記 FAILED。"""
        handled = []
        for msg in self.storage.due_for_resend(self.ack_timeout, now):
            mid = msg["message_id"]
            self.storage.record_event("ACK_TIMEOUT", message_id=mid, role=msg["to_role"],
                                      reason=f"{self.ack_timeout:g} 秒內未收到 ACK（第 {msg['attempts']} 次轉送）")
            if msg["attempts"] >= self.max_attempts:
                reason = f"重送 {msg['attempts']} 次仍未收到 ACK"
                self.storage.mark_failed(mid, reason)
                self.storage.record_event("FAILED", message_id=mid, role=msg["to_role"], reason=reason)
                await self._notify_sender(msg, MessageStatus.FAILED, reason)
            else:
                await self.deliver(msg, stage="RESENT")
            handled.append(mid)
        return handled

    async def resend_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.resend_tick()
            except Exception:  # pragma: no cover
                log.exception("resend tick failed")

    # ------------------------------------------------ 故障變更（組員三通知）
    async def _on_fault_change(self, action: str, target_id: str, fault_type: str) -> None:
        if fault_type != "connection_down" or target_id not in contract.ROLES:
            return
        if action == "enable":
            await self._broadcast_presence(target_id, False, "模擬連線中斷")
        else:
            if self.connections.is_online(target_id):
                await self._broadcast_presence(target_id, True, "模擬連線中斷已清除")
            await self.flush_pending(target_id)
