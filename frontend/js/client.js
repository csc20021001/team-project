/* 組員四：Client A／B 頁面（聊天、狀態、追蹤畫面、接收端 ACK、畫面去重）。 */
(function () {
  "use strict";
  var C = window.MTCore;
  var params = new URLSearchParams(location.search);
  var role = (params.get("role") || "").toUpperCase();
  var $ = function (id) { return document.getElementById(id); };

  if (role !== "A" && role !== "B") {
    document.body.innerHTML = '<p style="padding:16px">請用 <a href="/client.html?role=A">?role=A</a> 或 <a href="/client.html?role=B">?role=B</a> 開啟。</p>';
    return;
  }
  var peer = C.peerOf(role);
  document.title = "MessageTrace Client " + role;
  $("title").textContent = "Client " + role + "（傳給 " + peer + "）";

  var ws = null;
  var manualClose = false;
  var retryMs = 1000;
  var dedup = C.createDedup();
  var outgoing = {}; // message_id -> { el, status }
  var tokenKey = "mt_resume_" + role;

  function storageGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function storageSet(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* ignore */ } }

  function log(text, cls) {
    var d = document.createElement("div");
    d.textContent = new Date().toLocaleTimeString() + "  " + text;
    if (cls) d.className = cls;
    $("log").prepend(d);
  }

  function setSelf(online) {
    $("selfDot").className = "dot" + (online ? " on" : "");
    $("selfState").textContent = online ? role + " 已連線" : role + " 未連線";
  }
  function setPeer(online, reason) {
    $("peerDot").className = "dot" + (online ? " on" : "");
    $("peerState").textContent = peer + (online ? " 在線" : " 離線") + (reason ? "（" + reason + "）" : "");
  }

  function bubble(kind, id, text, from) {
    var b = document.createElement("div");
    b.className = "bubble " + kind;
    var t = document.createElement("div");
    t.textContent = text;
    var meta = document.createElement("div");
    meta.className = "meta";
    var idLink = document.createElement("a");
    idLink.href = "#";
    idLink.textContent = id;
    idLink.onclick = function (e) { e.preventDefault(); $("traceId").value = id; trace(); };
    meta.appendChild(document.createTextNode(kind === "out" ? "我 → " + peer + "  " : from + " → 我  "));
    meta.appendChild(idLink);
    b.appendChild(t);
    b.appendChild(meta);
    if (kind === "out") {
      var badge = document.createElement("span");
      badge.className = "badge";
      meta.appendChild(document.createTextNode("  "));
      meta.appendChild(badge);
    }
    $("chat").appendChild(b);
    $("chat").scrollTop = $("chat").scrollHeight;
    return b;
  }

  function setStatus(id, status, reason) {
    var o = outgoing[id];
    if (!o) return;
    o.status = C.mergeStatus(o.status, status);
    var badge = o.el.querySelector(".badge");
    badge.className = "badge st-" + o.status;
    badge.textContent = C.statusLabel(o.status);
    badge.title = reason || "";
  }

  function addOutgoing(id, text, status) {
    if (outgoing[id]) { setStatus(id, status); return; }
    outgoing[id] = { el: bubble("out", id, text), status: null };
    setStatus(id, status);
  }

  function showIncoming(m) {
    if (!dedup.accept(m.message_id)) {
      log("重複訊息 " + m.message_id + " 已去重（未重複顯示）");
      return;
    }
    bubble("in", m.message_id, m.text, m.from);
  }

  function sendJson(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); return true; }
    return false;
  }

  function onMessage(ev) {
    var m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    switch (m.type) {
      case "welcome":
        storageSet(tokenKey, m.resume_token);
        setSelf(true);
        setPeer(!!m.peers[peer]);
        retryMs = 1000;
        log("已連線（conn " + m.conn_id + "）");
        resync();
        break;
      case "presence":
        if (m.role === peer) { setPeer(m.online, m.reason); log(peer + (m.online ? " 上線" : " 離線") + (m.reason ? "：" + m.reason : "")); }
        break;
      case "message":
        // 接收端應用層 ACK：重複的補送也回 ACK，讓伺服器停止重送
        if ($("autoAck").checked) sendJson(C.buildAck(m.message_id));
        else log("未回 ACK：" + m.message_id + "（自動 ACK 已關閉）", "err");
        showIncoming(m);
        break;
      case "status":
        setStatus(m.message_id, m.status, m.reason);
        break;
      case "error":
        log("錯誤 " + m.code + "：" + m.detail + (m.message_id ? "（" + m.message_id + "）" : ""), "err");
        if (m.message_id && outgoing[m.message_id] && outgoing[m.message_id].status === "SENDING")
          setStatus(m.message_id, m.code === "REJECTED" ? "REJECTED" : "ERROR", m.detail);
        if (m.code === "DUPLICATE_ROLE" || m.code === "INVALID_ROLE") manualClose = true;
        break;
    }
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    manualClose = false;
    var proto = location.protocol === "https:" ? "wss://" : "ws://";
    var token = storageGet(tokenKey);
    var url = proto + location.host + "/ws/" + role + (token ? "?resume=" + encodeURIComponent(token) : "");
    ws = new WebSocket(url);
    ws.onmessage = onMessage;
    ws.onclose = function (e) {
      setSelf(false);
      log("連線關閉（code " + e.code + (e.reason ? " " + e.reason : "") + "）");
      if (e.code === 4000) manualClose = true; // 被同頁面的新連線取代
      if (!manualClose && $("autoReconnect").checked) {
        setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 2, 10000);
      }
    };
  }

  /** 重連後同步：補上離線期間的狀態與訊息（收到的訊息依 ID 去重）。 */
  function resync() {
    fetch("/api/messages?role=" + role + "&limit=200").then(function (r) { return r.json(); }).then(function (d) {
      (d.messages || []).forEach(function (m) {
        if (m.from_role === role) addOutgoing(m.message_id, m.text, m.status);
      });
    }).catch(function () {});
  }

  function send() {
    var text = $("text").value.trim();
    if (!text) return;
    var id = $("msgId").value.trim() || C.newMessageId(role);
    if (!sendJson(C.buildMessage(peer, text, id))) { log("尚未連線，無法送出", "err"); return; }
    addOutgoing(id, text, "SENDING");
    $("text").value = "";
    $("msgId").value = "";
  }

  function trace() {
    var id = $("traceId").value.trim();
    if (!id) return;
    fetch("/api/messages/" + encodeURIComponent(id)).then(function (r) {
      return r.json().then(function (body) { return { code: r.status, body: body }; });
    }).then(function (res) {
      var rows = $("traceRows");
      rows.innerHTML = "";
      if (res.code !== 200) {
        $("traceSummary").textContent = res.code === 404 ? "查無此 Message ID" : "查詢參數不合法（HTTP " + res.code + "）";
        return;
      }
      var t = C.formatTrace(res.body);
      $("traceSummary").textContent = t.summary
        ? t.messageId + "：" + t.summary.from + " → " + t.summary.to + "「" + t.summary.text + "」 狀態：" +
          t.summary.statusLabel + (t.summary.reason ? "（" + t.summary.reason + "）" : "") + "，轉送 " + t.summary.attempts + " 次"
        : t.messageId + "：訊息未保存（僅有事件）";
      t.rows.forEach(function (r) {
        var tr = document.createElement("tr");
        [r.time, r.stageLabel + (r.simulated ? "（模擬）" : ""), r.role, r.reason].forEach(function (v, i) {
          var td = document.createElement("td");
          td.textContent = v;
          if (i === 1 && r.simulated) td.className = "sim";
          tr.appendChild(td);
        });
        rows.appendChild(tr);
      });
    }).catch(function () { $("traceSummary").textContent = "查詢失敗"; });
  }

  $("send").onclick = send;
  $("text").addEventListener("keydown", function (e) { if (e.key === "Enter") send(); });
  $("connect").onclick = connect;
  $("disconnect").onclick = function () { manualClose = true; if (ws) ws.close(1000, "user"); };
  $("traceBtn").onclick = trace;
  setSelf(false);
  setPeer(false);
  connect();
  setInterval(function () { sendJson({ type: "ping" }); }, 20000);
})();
