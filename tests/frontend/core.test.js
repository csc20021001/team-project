// 組員四：frontend/js/core.js 單元測試（node --test）
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const C = require(path.join(__dirname, "..", "..", "frontend", "js", "core.js"));

test("FORWARDED 與 DELIVERED 不同標籤，只有 DELIVERED 算送達", () => {
  assert.notEqual(C.statusLabel("FORWARDED"), C.statusLabel("DELIVERED"));
  assert.equal(C.isDelivered("FORWARDED"), false);
  assert.equal(C.isDelivered("DELIVERED"), true);
});

test("狀態只前進，DELIVERED 不被覆蓋", () => {
  let s = null;
  for (const n of ["SENDING", "RECEIVED", "FORWARDED", "DELIVERED", "FORWARDED", "FAILED", "QUEUED"]) s = C.mergeStatus(s, n);
  assert.equal(s, "DELIVERED");
  assert.equal(C.mergeStatus("FORWARDED", "RECEIVED"), "FORWARDED");
  assert.equal(C.mergeStatus("QUEUED", "FORWARDED"), "FORWARDED");
  assert.equal(C.mergeStatus("FORWARDED", "FAILED"), "FAILED");
  assert.equal(C.mergeStatus("FAILED", "FORWARDED"), "FORWARDED");
  assert.equal(C.mergeStatus("FAILED", "DELIVERED"), "DELIVERED");
  assert.equal(C.mergeStatus("SENDING", "UNKNOWN"), "SENDING");
});

test("接收端畫面去重：補送／重送同 ID 只顯示一次", () => {
  const d = C.createDedup();
  assert.equal(d.accept("M101"), true);
  assert.equal(d.accept("M101"), false);
  assert.equal(d.accept("M102"), true);
  assert.equal(d.accept(""), false);
  assert.equal(d.size(), 2);
});

test("應用層 ACK 與訊息格式符合 JSON 契約", () => {
  assert.deepEqual(C.buildAck("M201"), { type: "ack", message_id: "M201" });
  assert.deepEqual(C.buildMessage("B", "hello", "M201"), { type: "message", message_id: "M201", to: "B", text: "hello" });
  assert.equal(C.peerOf("A"), "B");
  assert.equal(C.peerOf("B"), "A");
});

test("產生的 Message ID 符合伺服器格式且帶角色前綴", () => {
  const id = C.newMessageId("A", 1700000000000, 0.5);
  assert.match(id, /^A-[A-Za-z0-9_-]{1,60}$/);
  assert.notEqual(C.newMessageId("A"), C.newMessageId("A", Date.now() + 1, 0.1));
});

test("追蹤顯示格式", () => {
  const t = C.formatTrace({
    message_id: "M302",
    message: { from_role: "A", to_role: "B", text: "x", status: "QUEUED", attempts: 0, status_reason: "接收端 B 離線" },
    events: [
      { event_id: "E1", ts: "t1", stage: "RECEIVED", role: "A", reason: "已保存", is_simulated: false },
      { event_id: "E2", ts: "t2", stage: "QUEUED", role: "B", reason: "接收端 B 離線", is_simulated: true },
    ],
  });
  assert.equal(t.summary.statusLabel, "待送（對方離線）");
  assert.equal(t.summary.reason, "接收端 B 離線");
  assert.deepEqual(t.rows.map((r) => r.stageLabel), ["伺服器接收並保存", "進入待送佇列"]);
  assert.equal(t.rows[1].simulated, true);
  assert.equal(C.formatTrace({ message_id: "X", message: null, events: [] }).summary, null);
});
