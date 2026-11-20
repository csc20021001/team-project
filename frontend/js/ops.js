/* 組員四（介面）＋組員三（功能）：故障注入／清除、診斷、依據與建議操作畫面。 */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var lastDiagnosis = null;

  function api(method, url, body) {
    return fetch(url, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) { return r.json().then(function (b) { return { code: r.status, body: b }; }); });
  }

  function el(tag, text, cls) {
    var e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }

  function syncParams() {
    var t = $("ftype").value;
    $("latencyLbl").style.display = t === "response_timeout" ? "" : "none";
    $("usageLbl").style.display = t === "resource_overload" ? "" : "none";
  }

  function refreshActive() {
    api("GET", "/api/faults").then(function (r) {
      var box = $("active");
      box.innerHTML = "";
      if (!r.body.faults.length) { box.textContent = "全部目標皆為基準狀態"; return; }
      r.body.faults.forEach(function (f) {
        box.appendChild(el("div", f.target_id + "：" + f.active_faults.map(function (a) {
          return a.fault_name + " " + JSON.stringify(a.params);
        }).join("、") + "　觀測值 " + JSON.stringify(f.observed)));
      });
    });
  }

  function inject() {
    var t = $("ftype").value;
    var params = {};
    if (t === "response_timeout") params.latency_ms = Number($("latency").value);
    if (t === "resource_overload") params.usage_pct = Number($("usage").value);
    api("POST", "/api/faults", { target_id: $("target").value, fault_type: t, params: params }).then(function (r) {
      $("injectResult").className = r.code === 201 ? "muted" : "err";
      $("injectResult").textContent = r.code === 201
        ? "已注入 " + r.body.fault_name + "（事件 " + r.body.event_id + "，模擬標記 is_simulated=true）"
        : "拒絕：" + r.body.detail;
      refreshActive();
    });
  }

  function clear(all) {
    var url = "/api/faults/" + encodeURIComponent($("target").value) + (all ? "" : "?fault_type=" + $("ftype").value);
    api("DELETE", url).then(function (r) {
      $("injectResult").className = r.code === 200 ? "muted" : "err";
      $("injectResult").textContent = r.code !== 200 ? "錯誤：" + r.body.detail
        : r.body.cleared.length ? "已清除（事件 " + r.body.event_id + "），請重新診斷"
        : "目前已是基準狀態，無需清除";
      refreshActive();
    });
  }

  function renderSuggestions(d) {
    var box = $("suggest");
    box.innerHTML = "";
    d.suggestions.forEach(function (s) {
      var card = el("div", undefined, "card");
      card.appendChild(el("strong", s.fault_name));
      if (s.guidance) {
        var ul = el("ul");
        s.guidance.forEach(function (g) { ul.appendChild(el("li", g)); });
        card.appendChild(ul);
      } else {
        [["檢查步驟", s.check_steps], ["處理方式", s.actions], ["注意事項", s.cautions]].forEach(function (p) {
          card.appendChild(el("div", p[0], "muted"));
          var ul = el("ul");
          p[1].forEach(function (x) { ul.appendChild(el("li", x)); });
          card.appendChild(ul);
        });
        card.appendChild(el("div", "恢復確認：" + s.recovery_check));
        var btn = el("button", "我已確認，將自行處理");
        btn.onclick = function () {
          api("POST", "/api/suggestions/" + s.fault_code + "/confirm", { target_id: d.target_id }).then(function (r) {
            btn.disabled = true;
            btn.textContent = "已記錄確認（" + r.body.event_id + "）；處理後請重新診斷";
          });
        };
        card.appendChild(btn);
      }
      box.appendChild(card);
    });
  }

  function diagnose() {
    api("POST", "/api/diagnosis", { target_id: $("target").value }).then(function (r) {
      var box = $("diag");
      box.innerHTML = "";
      $("evRows").innerHTML = "";
      if (r.code !== 200) { box.appendChild(el("div", "錯誤：" + r.body.detail, "err")); return; }
      var d = r.body;
      lastDiagnosis = d;
      box.appendChild(el("div", "診斷 " + d.diagnosis_id + "（目標 " + d.target_id + "）：" + d.status));
      box.appendChild(el("div", d.message, "muted"));
      if (d.missing_fields.length) box.appendChild(el("div", "缺漏欄位：" + d.missing_fields.join("、"), "err"));
      if (d.invalid_fields.length) box.appendChild(el("div", "格式錯誤：" + d.invalid_fields.join("、"), "err"));
      d.hits.forEach(function (h) {
        box.appendChild(el("div", h.rule_id + " " + h.fault_name + "（嚴重程度 " + h.severity + "，事件 " + h.event_id + "）"));
      });
      $("evidence").disabled = !d.hits.length;
      renderSuggestions(d);
    });
  }

  function evidence() {
    if (!lastDiagnosis) return;
    api("GET", "/api/diagnosis/" + lastDiagnosis.diagnosis_id + "/evidence").then(function (r) {
      var rows = $("evRows");
      rows.innerHTML = "";
      r.body.evidence.forEach(function (e) {
        var tr = el("tr");
        var cells = e.status === "依據存在"
          ? [e.rule_id, e.event_id, e.timestamp, e.field + "=" + e.observed_value, e.threshold, e.is_simulated ? "是" : "否"]
          : [e.rule_id, e.event_id, e.status, "", "", ""];
        cells.forEach(function (c) { tr.appendChild(el("td", String(c))); });
        rows.appendChild(tr);
      });
      if (!r.body.is_current) rows.appendChild(el("tr", "（此為舊診斷結果，請重新診斷）", "err"));
    });
  }

  api("GET", "/api/rules").then(function (r) {
    r.body.rules.forEach(function (x) {
      var tr = el("tr");
      [x.rule_id, x.fault_name, x.severity, x.field, x.threshold].forEach(function (c) { tr.appendChild(el("td", c)); });
      $("rules").appendChild(tr);
    });
  });
  $("ftype").onchange = syncParams;
  $("inject").onclick = inject;
  $("clearOne").onclick = function () { clear(false); };
  $("clearAll").onclick = function () { clear(true); };
  $("diagnose").onclick = diagnose;
  $("rediagnose").onclick = diagnose;
  $("evidence").onclick = evidence;
  syncParams();
  refreshActive();
})();
