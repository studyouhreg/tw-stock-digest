# TW Stock Digest · 臺股每日資料與 Claude 摘要

每天盤後收集 **證交所上市、櫃買中心上櫃** 官方報價，將乾淨 CSV 與摘要直接存回 **本專案的 [data/](data/README.md)**；Google Drive 與 Claude API 可之後再啟用。

**可以先免金鑰執行。** 正常收集不需要第三方資料 API token；Actions 使用內建 GITHUB_TOKEN 提交資料。原始 JSON 保存在本機／GitHub Actions artifact，Claude 分析預設關閉。

直接閱讀：[最新資料摘要](data/latest.md) · [逐日 CSV 與資料](data/daily/)

## 先跑一次

需要 Python 3.11+。日常執行無額外套件：

```bash
python -m tw_stock_digest --demo
python -m unittest discover -s tests -v
```

開啟 `output/demo/latest.md` 看示範結果。示範是明確標示的合成資料，固定日期 2026-10-21，絕不呼叫 Google 或 Claude，且全部隔離在 `output/demo/`，不會覆寫真實資料。

盤後抓取真實資料：

```bash
python -m tw_stock_digest --no-drive --repo-dir data
```

本專案已設定 GitHub Actions：**平日臺灣時間 19:17**（UTC `17 11 * * 1-5`），亦可手動執行。排程不是準點保證；休市或官方仍回傳舊日資料時會寫出 skipped 狀態，不冒充當日行情。

## 資料如何走

1. 讀取 TWSE、TPEx 最新交易日快照，保存原始 JSON 與抓取時間。
2. 檢查交易日期、重複代碼、數值與 OHLC；產生 CSV 和規則式事實摘要。
3. 先將整理後資料存入 `data/daily/YYYY-MM-DD/`，設定 Drive 時再額外上傳原始資料與報告。
4. 啟用分析時，只送精簡 `context.json` 到 Claude；保存分析與 token 用量，可從專案快取重用相同輸入的結果。
5. 更新 `data/latest.md`；舊日期手動重跑不會把 latest 往回覆蓋。Actions 只提交 `data/`，即使可選的 Drive／Claude 失敗，也會提交已驗證行情。

**這個流程由 GitHub Actions 主動呼叫 Claude API。** 把檔案放到 Drive，不會自行觸發 Claude 網頁版。

## 產出

| 檔案 | 用途 |
|---|---|
| `raw_TWSE.json`、`raw_TPEX.json` | 保留來源欄位與原始數值，供追查 |
| `quotes.csv` | 統一上市／上櫃欄位；成交量是股、成交金額是新臺幣 |
| `context.json` | 各市場股票類漲跌家數、成交金額前五名、指定自選股 |
| `facts.md` | 無需 AI 的繁體中文事實摘要 |
| `claude-prompt.md` | 可以手動交給 Claude 的完整分析提示與資料 |
| `analysis.md`、`analysis.json` | 本次分析或明確的 pending／disabled 狀態；完成後含模型與用量 |
| `analysis-cache-<SHA256>.json` | 按完整輸入與模型保存的成功分析，供重跑重用 |
| `manifest.json` | 來源、日期、抓取時間、列數與資料檔案 SHA-256；GitHub 版本另列原始來源校驗碼 |
| `latest.md`、`latest.json` | 最近保存的交易日摘要與分析狀態；Drive／output 為最新完成報告 |
| `status.json` | 本次執行結果與失敗階段；收集失敗仍保留已取回原始檔 |

完整本機資料在 `output/daily/YYYY-MM-DD/`，本次原始回應另存 `output/raw/`。GitHub 保存在 `data/daily/YYYY-MM-DD/`，不包含體積較大的原始 JSON；它們可從 Actions artifact 下載。Drive 日期資料夾直接放於專案專屬根資料夾下。CSV 的股票代碼是字串；Excel 匯入時請將 `symbol` 指定為文字，以保留 `0050` 前導零。

## 啟用 Drive 與 Claude

完整操作請看 **[設定指南](docs/SETUP.md)**。

| GitHub 設定 | 類型 | 用途 |
|---|---|---|
| `GDRIVE_OAUTH_JSON` | Secret | 初次 Google 授權工具產生的憑證 JSON |
| `GDRIVE_FOLDER_ID` | Secret | 同一工具建立的專屬資料夾 ID |
| `ANTHROPIC_API_KEY` | Secret，可選 | Claude API 金鑰 |
| `ENABLE_ANALYSIS` | Variable，可選 | 設為 `true` 才會呼叫 Claude；預設關閉 |
| `CLAUDE_MODEL` | Variable，可選 | 預設 `claude-haiku-5-5`；請依你的 API 帳號可用模型調整 |
| `WATCHLIST` | Variable，可選 | 預設 `0050,2330,2317,2454,6488`，只是範例清單 |

整理後行情、摘要與分析提交到 git；金鑰、原始 JSON 和 demo 不提交。`drive.file` 只要求本程式建立／授權給本程式的檔案存取範圍，無需授權整個 Drive。此儲存庫目前公開，因此 data 與自選摘要也是公開內容。

## 已有專案與取捨

| 選項 | 適合什麼 | 本專案的取捨 |
|---|---|---|
| [FinMind](https://github.com/FinMind/FinMind) | 臺股歷史行情、財報等多種金融資料 | 後續加入法人、營收或歷史序列時可用；資料權限／配額需另確認 |
| [twstock](https://github.com/mlouielu/twstock) | Python 臺股價格擷取套件 | 是資料擷取工具，還要自己補排程、Drive 與分析流程 |
| [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | 較完整的多市場 LLM 分析與自動推送 | 功能與設定面較大；要使用前需核對其臺股來源與 Drive 寫入能力 |
| 此專案 | 輕量、可追查的臺股每日快照與摘要 | 小範圍完成你指定的 Actions → Drive → Claude 流程 |

## 成本與限制

- Claude Pro 網頁訂閱與 API 計費分開。此程式不使用網頁登入 cookie 或訂閱額度冒充 API。
- 每個新輸入最多一次 Claude 呼叫，輸出上限 1,800 tokens；最多 50 檔自選股，輸入上限 60,000 字元。
- 同一資料、模型與提示詞的成功分析可由 GitHub、Drive 或本機快取重用。若付費呼叫完成但結果尚未保存／提交就中斷，重跑仍可能再次計費；不宣稱 exactly-once。
- 每次更新資料先將當前分析檔標記為 pending／disabled；快取獨立保存。分析關閉或失敗不會留下冒充本次結果的舊分析。
- 月費可用 `交易日數 × (input_tokens × 輸入單價 + output_tokens × 輸出單價) / 1,000,000` 估算。實際用量保存在 `analysis.json`；模型價格以 Anthropic 官方當期頁面為準。
- Actions 和 Drive 仍受各帳號用量／儲存配額限制。GitHub 保留逐日整理資料；原始 JSON 的 artifact 只保留 14 日，長期保存原始資料可設定 Drive。
- GitHub 寫入需要每日 workflow 的 contents: write，且分支規則允許 bot 提交；若有人同時更新 main 導致非快轉，push 會失敗並保留 artifact，請重新執行，不會強制覆蓋。
- 公開 GitHub 儲存庫長時間無活動，排程可能因 60 日規則被停用；執行延遲、資料站封鎖或中斷也可能發生。可從 Actions 手動重跑，晚於來源快照保存期限則需另外接歷史 API。
- 第一版沒有新聞、法人、營收、財報或歷史序列，不能據此做新聞歸因、均線分析或回測。快照價差不是含息總報酬。
- 分析只涵蓋四碼股票類證券與 `00` 開頭 ETF，權證及其他代碼不進摘要；不冒充全市場指數／總成交額。
- 最新快照端點不支援任意歷史回補；`--date` 是**驗證預期日期**，不是向來源查詢歷史日期。
- Workflow concurrency 防止本儲存庫的工作同時更新 Drive；多個儲存庫或本機程序不要同時寫同一 Drive 資料夾。

## 官方參考

- [TWSE OpenAPI](https://openapi.twse.com.tw/)、[TPEx OpenAPI](https://www.tpex.org.tw/openapi/)
- [Google Drive OAuth scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
- [Google OAuth refresh token 到期條件](https://developers.google.com/identity/protocols/oauth2)
- [Google Drive service account 儲存限制](https://developers.google.com/workspace/drive/api/guides/handle-errors#storagequotaexceeded)
- [GitHub 排程行為](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [Claude 模型](https://platform.claude.com/docs/en/models/overview)、[價格](https://platform.claude.com/docs/en/about-claude/pricing)

這是資料整理與研究工具；模型的文字解讀需要回到數據核對。
