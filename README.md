# team-project：MessageTrace 訊息追蹤系統（第六組期末專案）

同一台 Windows 電腦啟動一個 Server，用兩個瀏覽器頁面扮演 Client A、B 互傳訊息。系統區分「伺服器已接收」「已轉送（FORWARDED）」「已送達（DELIVERED）」三種狀態，每則訊息的事件都能依 Message ID 查詢。系統另外提供故障注入與規則式診斷。

- 後端：Python 3.10 以上、FastAPI、WebSocket、SQLite
- 前端：靜態 HTML/JS，不需要 build，由 FastAPI 直接提供

---

## 1. 安裝（Windows PowerShell）

```powershell
cd team-project
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
```

## 2. 啟動 Server

```powershell
.venv\Scripts\python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

- 健康檢查：<http://127.0.0.1:8000/api/health>，正常時回應 HTTP 200 `{"status":"ok", ...}`
- 資料庫預設存在 `data\messagetrace.db`。要重新開始，先停掉 Server，再刪除這個檔案。

可用環境變數調整（在 PowerShell 先執行 `$env:MT_ACK_TIMEOUT = "3"`，再啟動 Server）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `MT_DB_PATH` | `data/messagetrace.db` | SQLite 檔案路徑 |
| `MT_ACK_TIMEOUT` | `5` | 轉送後等待 ACK 的秒數，逾時就重送 |
| `MT_MAX_ATTEMPTS` | `3` | 最多轉送次數（含第一次），用完標記 FAILED |
| `MT_RESEND_INTERVAL` | `1` | 每隔幾秒檢查一次逾時（`0` 表示關閉） |

## 3. 操作 A／B

1. 用瀏覽器開 <http://127.0.0.1:8000/>，按「開啟 Client A」「開啟 Client B」。也可以直接開下面兩個網址，各用一個分頁：
   - <http://127.0.0.1:8000/client.html?role=A>
   - <http://127.0.0.1:8000/client.html?role=B>
2. **互傳**：在 A 輸入文字後按「送出」。Message ID 欄可以留空（自動產生），也可以自己填，例如 `M201`。A 畫面上的訊息狀態依序變成：
   `伺服器已接收` → `已轉送（等待對方確認）` → `已送達（對方已確認）`
   只有在 B 回傳應用層 ACK 之後，才會顯示「已送達」。
3. **ACK 缺少**：在 B 取消勾選「自動回 ACK」，再由 A 傳一則訊息。A 會一直停在「已轉送」。逾時後伺服器會用原本的 ID 重送，次數用完會顯示「送達失敗（逾時）」。
4. **斷線重連**：在 B 按「中斷連線」，A 的狀態列會顯示「B 離線」。這時 A 傳出的訊息（例如 `M101`）會顯示「待送（對方離線）」。B 按「連線」後，伺服器會用原 ID 補送；B 回 ACK 後，A 才會顯示「已送達」。B 的畫面會依 Message ID 去重，同一則訊息不會重複出現。
5. **追蹤**：在「依 Message ID 追蹤」輸入 ID（或點訊息下方的 ID 連結），可以看到這則訊息的接收、轉送、待送、補送、ACK 等事件，包含時間、角色、原因，模擬事件也會標出來。
6. **重複角色**：如果再開一個 `?role=A` 的分頁，新分頁會被拒絕（`DUPLICATE_ROLE`），原本的 A 不受影響。同一個分頁重新整理時，會帶 resume token 取代自己原本的舊連線。

### 故障注入與診斷

開 <http://127.0.0.1:8000/ops.html>：

1. 選擇目標（`A`、`B` 或 `SERVER`）和故障類型（連線中斷／回應逾時／資源過載），填好參數後按「注入」。每次注入都會產生一筆帶 `is_simulated=true` 和事件 ID 的模擬事件。
   - 連線中斷（目標 A 或 B）：送往該端的訊息會進入待送佇列。清除後，伺服器自動用原 ID 補送。
   - 回應逾時（目標 A 或 B）：該端送出的 ACK 會被當成沒有到達，所以訊息會逾時重送。
   - 資源過載（目標 SERVER、A 或 B）：伺服器拒收相關訊息，回傳錯誤 `REJECTED`，事件會記下拒收原因。
2. 按「執行診斷」，用規則 R01～R03 比對。按「查看依據」可以看到事件 ID、時間、觀測值、門檻和模擬標記。
3. 「修復建議」會列出檢查步驟、處理方式、注意事項和恢復確認方法。系統**不會自動修改**任何東西；使用者按「我已確認」後，系統只記錄一筆確認事件。
4. 按「清除」後再按「重新診斷」，會顯示最新的結果；舊的診斷會標記為非目前狀態。

## 4. 測試

```powershell
.venv\Scripts\python -m pytest
```

- 全部是自動測試，包含 AC1.1～AC3.2、組員二的儲存與重送策略、組員三的 US-01～US-04，以及組員四的前端邏輯。
- `tests/comm/test_ac3_2_integration.py` 會用上面同一個 uvicorn 指令，實際啟動一個 Server，再用真正的 WebSocket 跑「正常互傳、ACK 缺少、斷線重連」三個流程，最後用 Message ID 和連線事件核對結果。
- 前端核心邏輯的測試（`tests/frontend/core.test.js`）需要 Node.js 18 以上；沒有安裝 Node 時，這個測試會自動略過。
- 如果 Windows 上出現暫存資料夾權限錯誤，可以改用 `--basetemp`，例如 `pytest --basetemp=.pytest_tmp`。

## 5. 模組與組員對應

| 目錄／檔案 | 組員 | 任務 |
|---|---|---|
| `server/main.py` | 組員一 | T1.1 FastAPI 服務、啟動設定、錯誤回應不洩漏堆疊 |
| `server/comm/api.py` | 組員一 | T1.1 健康檢查；T3.2 依 Message ID 唯讀查詢（`/api/messages/{id}`、`/api/events`） |
| `server/comm/connections.py` | 組員一 | T1.2 A／B 連線表、合法／重複／無效角色、斷線清理；T1.3 舊連線不清掉新連線 |
| `server/comm/contract.py` | 組員一（與組員二、四共同定義） | T2.1 訊息／ACK／錯誤 JSON 契約 |
| `server/comm/service.py` | 組員一 | T1.3 重連與離線通知；T2.2 雙向路由（先保存再轉送）；T2.3 ACK 介接；T3.1 事件記錄 |
| `server/storage/store.py` | 組員二 | SQLite、訊息 ID、事件入口、ACK 驗證、逾時判斷、重送、待送佇列、補送入口、查詢介面 |
| `server/fault/simulator.py` | 組員三 | US-01：T-01-01～T-01-04 注入／清除、參數檢查、模擬標記、故障控制介接 |
| `server/fault/rules.py`、`engine.py` | 組員三 | US-02：T-02-01～T-02-04 規則 R01～R03 與診斷引擎；US-03：T-03-01、T-03-02 依據格式與關聯 |
| `server/fault/service.py`、`api.py` | 組員三 | US-03：T-03-03 依據查詢；US-04：T-04-03 建議顯示與重新診斷 |
| `server/fault/suggestions.py` | 組員三 | US-04：T-04-01、T-04-02 三類故障建議表與對照 |
| `frontend/` | 組員四 | 聊天／狀態／追蹤畫面、FORWARDED 與 DELIVERED 區分、接收端 ACK、畫面去重、故障與診斷操作介面 |
| `tests/comm/` | 組員一 | AC1.1～AC3.2、T3.3 整合測試 |
| `tests/storage/` | 組員二 | 儲存、ACK 驗證、逾時、重送、補送 |
| `tests/fault/` | 組員三 | US-01～US-04 |
| `tests/frontend/` | 組員四 | 頁面與前端核心邏輯 |

### 規則對照表（組員三 T-02-01）

| 規則 | 故障 | 嚴重程度 | 條件（含邊界） |
|---|---|---|---|
| R01 | 連線中斷 | 高 | `connection_down == true` |
| R02 | 回應逾時 | 中 | `latency_ms >= 3000` |
| R03 | 資源過載 | 中 | `usage_pct >= 90` |

- 基準狀態：`connection_down=false`、`latency_ms=100`、`usage_pct=30`。
- 命中多條規則時，全部保留，先依嚴重程度排序，再依規則 ID 排序。
- 沒有命中任何規則時，結果是「未識別故障」，這不保證系統正常。
- 缺少欄位或格式錯誤時，結果是「資料不足」，並列出有問題的欄位。

## 6. JSON 契約摘要（T2.1）

客戶端送給伺服器：

```json
{"type": "message", "message_id": "M201", "to": "B", "text": "hello"}
{"type": "ack", "message_id": "M201"}
```

伺服器送給客戶端：

```json
{"type": "message", "message_id": "M201", "from": "A", "to": "B", "text": "hello", "timestamp": "...", "redelivery": false}
{"type": "status", "message_id": "M201", "status": "FORWARDED", "reason": null, "timestamp": "..."}
{"type": "presence", "role": "B", "online": false, "reason": "離線"}
{"type": "error", "code": "MISSING_FIELD", "detail": "缺少目的角色 to", "message_id": "M210"}
```

完整欄位和錯誤代碼請看 `server/comm/contract.py`。
