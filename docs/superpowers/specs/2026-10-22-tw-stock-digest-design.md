# 臺股盤後資料與 Claude 摘要設計

使用者要求：以臺股開始，以 GitHub Actions 定時收集股市資訊，保存到 Google Drive，之後用 Claude 自動分析；建立可使用的專案。

## 第一版決策

採用 Python 3.11+、標準函式庫實作日終資料管線；只有初次 Google OAuth 授權需要額外安裝官方 Google OAuth 函式庫。直接讀取 TWSE 與 TPEx 官方 OpenAPI，降低第三方服務與付費資料相依。FinMind 適合後續歷史行情、財報與法人資料；twstock 是擷取套件；完整多代理分析框架目前超出此每日摘要的需求。

排程：平日臺灣時間 19:17，即 UTC cron `17 11 * * 1-5`。資料以來源的交易日期分區；預期日期與來源不同時標示過期或可能休市，不能直接宣稱休市，也不能把昨天資料標成今天。不同市場日期不一致時不呼叫 Claude。支援手動指定預期日期，但最新快照端點不能補抓任意歷史。

來源：TWSE `/v1/exchangeReport/STOCK_DAY_ALL`；TPEx `/openapi/v1/tpex_mainboard_daily_close_quotes`。保留原始回應、抓取時間、來源 URL。股票與 ETF 分開，權證等不進入摘要。價差百分比用「當日價差 / (收盤價 - 當日價差)」，標示為官方價差換算，不能聲稱為含息報酬。缺值保留 null，不補零。上市、上櫃摘要分開，不冒充加權指數。

## 資料與分析

輸出原始 JSON、股票與 ETF 報價 CSV、精簡 context.json、人工使用的 Claude prompt、規則式事實報告、manifest/status。預設自選清單 `0050,2330,2317,2454,6488` 只是可更換範例。

Claude API 預設關閉；以 `ENABLE_ANALYSIS=true` 且 `ANTHROPIC_API_KEY` 啟用，模型可設定。一次呼叫、輸出上限，避免自動重試付費請求。prompt 僅依當日已提供事實寫繁中摘要；明示無新聞、財報、歷史序列，不推測新聞成因、技術指標或交易指令。記錄模型、token 用量與輸入雜湊，Drive 有相同雜湊的完整分析可重用。Claude Pro 與 API 計費分開。

## 儲存與失敗行為

Google Drive 採人類使用者 OAuth refresh token、`drive.file` scope，由授權工具建立本專案專屬資料夾。憑證僅放 GitHub Secrets，不寫入輸出或 git。同一日期資料夾、同檔名採更新；重複檔案歧義直接報錯。latest 僅被同日或更新資料取代。

先完成本機資料輸出，再上傳資料，最後執行可選 Claude 與上傳分析；分析失敗仍保留已收集資料且工作失敗。沒有 Drive 憑證時可本機與 Actions artifact 執行；半套憑證則失敗。artifact 保留 14 日，只是暫存，不取代 Drive 長期保存。demo 使用合成資料且禁止呼叫遠端 Drive/Claude，全部輸出隔離於 output/demo。當前分析在資料更新前先標記 pending／disabled，成功結果另存 analysis-cache-<SHA256>.json；每次執行清除上一輪暫存 raw 檔，日期歸檔不受影響。

## 驗收與部署邊界

驗證民國日期、逗號數字、null、重複代碼、OHLC、來源日期、停牌、快取與上傳重跑、HTTP gzip/暫時失敗、錯誤不洩漏憑證、CLI demo。至少一次真實資料收集；Drive/Claude 需使用者授權與金鑰才能作端到端驗證。

GitHub 連接器帳號 aouhreg；目前瀏覽器帳號 studyouhreg。目標帳號尚有歧義，完成本機專案後才請使用者確認；不寫入既有不相關儲存庫。
