# 臺股每日資料

開啟 [最新摘要](latest.md)，或到 [daily/](daily/) 選擇交易日期。

每天盤後由 GitHub Actions 收集官方最新快照，驗證後提交到本目錄。無需先設定 Google Drive 或 Claude API。

| 檔案 | 內容 |
|---|---|
| `daily/YYYY-MM-DD/quotes.csv` | 涵蓋證券逐檔行情；代碼、名稱、開高低收、漲跌、成交股數／金額／筆數 |
| `context.json` | 股票類漲跌家數、各市場成交金額前五名、自選清單 |
| `facts.md` | 不使用 AI 的繁體中文摘要 |
| `claude-prompt.md` | 可手動貼給 Claude 的資料與提示詞 |
| `analysis.json`、`analysis.md` | 當前分析或明確的 pending／disabled 狀態 |
| `analysis-cache-*.json` | 成功的 Claude 分析快取，供同一輸入重跑時重用 |
| `manifest.json` | 來源、抓取時間、筆數、保存檔案校驗碼與原始來源校驗碼 |
| `latest.md`、`latest.json` | 最近保存的交易日摘要；請同時留意分析狀態 |

原始 `raw_TWSE.json`、`raw_TPEX.json` 不放進 git，可從對應 Actions 執行的 artifact 下載（保留 14 日）；設定 Drive 後也會存到 Drive。`manifest.json` 的 `files_sha256` 只列本目錄實際保存檔案；`source_files_sha256` 記錄原始收集檔案。

首份 `2026-10-22` 快照由本機收集後提交，因此沒有對應的 Actions artifact；上述 artifact 保存方式適用於之後由 Actions 執行的收集。

目前篩選四碼股票類證券與 `00` 開頭 ETF，不包含權證等其他代碼。成交量單位是「股」，成交金額與價格單位是新臺幣；Excel 匯入時請把 symbol 設為文字以保留 0050 等前導零。

這是逐日累積的行情快照，沒有補齊過去歷史，也沒有新聞、財報、月營收、法人買賣超、盤中逐筆或指數資料。GitHub 儲存庫若公開，這些資料和自選摘要也會公開。

若 Drive／Claude 失敗，已驗證行情仍可保存，分析會標記 pending／disabled。資料有誤、不同市場交易日期不一致或當日尚無新行情時，程式不新增該日快照。手動重跑過去日期不會把 latest 回退。
