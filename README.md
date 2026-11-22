# 軟體工程期末報告

第六組｜MessageTrace 訊息追蹤系統

## 組員三 陳劭齊｜故障注入與規則式診斷

| 檔案 | 內容 |
| --- | --- |
| `messagetrace/team3_diagnostics/diagnostics.py` | 故障注入與清除（連線中斷、回應逾時、資源過載）、參數檢查、規則式診斷 R01–R03、事件依據、修復建議表 |
| `messagetrace/team3_diagnostics/routes.py` | `POST /api/faults`、`DELETE /api/faults/{fault_id}`、`GET /api/diagnostics` |
| `tests/test_team3_diagnostics.py` | US-01～US-04 測試 |

使用者故事：US-01 注入與清除故障、US-02 規則式診斷、US-03 查閱事件依據、US-04 取得修復建議。

其他組員（通訊後端、資料儲存、前端）的程式會陸續加入；完整專案加入後即可依 README 啟動與執行全部測試。
