/*
 * 組員四：前端核心邏輯（不依賴 DOM，可在瀏覽器與 Node 測試中共用）。
 * - 狀態合併：區分 FORWARDED 與 DELIVERED，狀態不倒退
 * - 接收端畫面去重：以 Message ID 去重，補送／重送不重複顯示
 * - 接收端應用層 ACK
 * - 依 Message ID 的追蹤顯示格式（T3.2）
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.MTCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var STATUS_RANK = { SENDING: 0, RECEIVED: 1, QUEUED: 2, FORWARDED: 3, FAILED: 3, DELIVERED: 5, REJECTED: 5, ERROR: 5 };

  var STATUS_LABEL = {
    SENDING: "傳送中",
    RECEIVED: "伺服器已接收",
    QUEUED: "待送（對方離線）",
    FORWARDED: "已轉送（等待對方確認）",
    DELIVERED: "已送達（對方已確認）",
    FAILED: "送達失敗（逾時）",
    REJECTED: "伺服器拒收",
    ERROR: "錯誤",
  };

  var STAGE_LABEL = {
    RECEIVED: "伺服器接收並保存",
    FORWARDED: "轉送給接收端",
    REDELIVERED: "重連補送",
    RESENT: "逾時重送",
    QUEUED: "進入待送佇列",
    DELIVERED: "收到有效 ACK（送達）",
    ACK_REJECTED: "ACK 被拒絕",
    ACK_DROPPED: "ACK 未到達（模擬回應逾時）",
    ACK_DUPLICATE: "重複 ACK",
    ACK_TIMEOUT: "ACK 逾時",
    FAILED: "送達失敗",
    REJECTED: "伺服器拒收",
    INVALID: "格式錯誤",
    STORAGE_FAILED: "保存失敗",
    CONNECTED: "連線建立",
    DISCONNECTED: "連線中斷",
    CONNECTION_REJECTED: "連線被拒",
    CONNECTION_REPLACED: "連線被取代",
    STALE_CLOSE_IGNORED: "舊連線關閉（忽略）",
    FAULT_INJECTED: "注入模擬故障",
    FAULT_CLEARED: "清除模擬故障",
    SUGGESTION_CONFIRMED: "使用者確認建議",
  };

  /** 合併狀態：只往前進；DELIVERED 之後不會被 FORWARDED／FAILED 覆蓋。
   *  例外：QUEUED → FORWARDED 與 FAILED → FORWARDED（重送）允許。 */
  function mergeStatus(oldStatus, newStatus) {
    if (!oldStatus) return newStatus;
    if (!(newStatus in STATUS_RANK)) return oldStatus;
    if (oldStatus === "DELIVERED" || oldStatus === "REJECTED") return oldStatus;
    if (newStatus === "DELIVERED") return newStatus;
    if (oldStatus === "FAILED" && newStatus === "FORWARDED") return newStatus;
    return STATUS_RANK[newStatus] >= STATUS_RANK[oldStatus] ? newStatus : oldStatus;
  }

  function isDelivered(status) {
    return status === "DELIVERED";
  }

  /** 接收端畫面去重器。 */
  function createDedup() {
    var seen = new Set();
    return {
      /** 第一次看到回傳 true（應顯示），否則 false。 */
      accept: function (messageId) {
        if (!messageId || seen.has(messageId)) return false;
        seen.add(messageId);
        return true;
      },
      has: function (messageId) { return seen.has(messageId); },
      size: function () { return seen.size; },
    };
  }

  function newMessageId(role, now, rand) {
    now = now === undefined ? Date.now() : now;
    rand = rand === undefined ? Math.random() : rand;
    return role + "-" + now.toString(36).toUpperCase() + "-" + Math.floor(rand * 1679616).toString(36).toUpperCase();
  }

  function buildMessage(to, text, messageId) {
    return { type: "message", message_id: messageId, to: to, text: text };
  }

  /** 應用層 ACK：收到訊息（包含重複的補送）一律回 ACK，讓伺服器得以結束重送。 */
  function buildAck(messageId) {
    return { type: "ack", message_id: messageId };
  }

  function peerOf(role) {
    return role === "A" ? "B" : "A";
  }

  function statusLabel(s) {
    return STATUS_LABEL[s] || s;
  }

  /** /api/messages/{id} 回應 → 追蹤畫面列。 */
  function formatTrace(resp) {
    var msg = resp.message;
    return {
      messageId: resp.message_id,
      summary: msg
        ? { from: msg.from_role, to: msg.to_role, text: msg.text, status: msg.status,
            statusLabel: statusLabel(msg.status), attempts: msg.attempts, reason: msg.status_reason || "" }
        : null,
      rows: (resp.events || []).map(function (e) {
        return {
          time: e.ts, stage: e.stage, stageLabel: STAGE_LABEL[e.stage] || e.stage,
          role: e.role || "", reason: e.reason || "", simulated: !!e.is_simulated, eventId: e.event_id,
        };
      }),
    };
  }

  return {
    STATUS_RANK: STATUS_RANK, STATUS_LABEL: STATUS_LABEL, STAGE_LABEL: STAGE_LABEL,
    mergeStatus: mergeStatus, isDelivered: isDelivered, createDedup: createDedup,
    newMessageId: newMessageId, buildMessage: buildMessage, buildAck: buildAck, peerOf: peerOf,
    statusLabel: statusLabel, formatTrace: formatTrace,
  };
});
