# GitHub 資料保存設計

使用者要求先把每日資料直接更新到 GitHub 專案，並說明目前收集內容。

## 方案與決定

- 將整理後資料存入預設分支 `data/daily/YYYY-MM-DD/`，在 GitHub 直接閱讀 CSV 與 Markdown。這是本次採用方案，無需額外憑證。
- 全量原始 JSON 每日約 7 MB，仍放 Actions artifact（14 日）與可選 Drive；不將大部分屬於未納入摘要證券的原始資料重複提交到 git。
- 單用 artifact 無法在專案中持續閱讀歷史資料，因此不作為主要保存位置；獨立資料分支增加切換成本，這一版使用 main。

## 行為

- 保存 quotes.csv、context.json、facts.md、claude-prompt.md、analysis.json/md、有效分析快取及 manifest.json。
- 在呼叫 Drive／Claude 前先保存已驗證行情；後續失敗仍可提交行情，分析標記 pending／disabled。
- GitHub manifest 區分已保存檔案的 SHA-256 與來源原始檔 SHA-256，不宣稱原始 JSON 也在儲存庫。
- data/latest.md 與 latest.json 指向最近保存的交易日；舊日期重跑不能回退。
- demo 不修改 data；日期過舊、兩市場日期不一致或資料驗證失敗不新增行情。
- 相同輸入的 Claude 結果可直接讀取儲存庫快取。
- 排程保持平日臺灣 19:17。只有每日 workflow 使用 contents: write；提交只包含 data，非快轉 push 失敗須顯示失敗，不強制覆蓋。
- 首批資料重新從官方端點取得，以回傳交易日期為準。資料隨目前公開儲存庫公開。

## 驗證

覆蓋資料保存、漏原始檔與祕密檔、校驗失敗不修改既有資料、失敗後行情保留、demo／舊日資料不覆寫、快取重用；CI 驗證 unittest、demo、workflow YAML 與 git 提交範圍。
