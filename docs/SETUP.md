# 設定指南

## 1. 建立 GitHub 儲存庫

本次專案的儲存庫是 [studyouhreg/tw-stock-digest](https://github.com/studyouhreg/tw-stock-digest)。使用此儲存庫時，可直接下載程式，再前往第 2 節設定 Google Drive：

```bash
git clone https://github.com/studyouhreg/tw-stock-digest.git
cd tw-stock-digest
```

建立獨立的 `tw-stock-digest` 儲存庫，建議 private，將本專案全部程式與 `.github` 目錄放入預設分支。不要上傳 `output/`、`.env`、`client_secret*.json`、`gdrive-oauth.json`。

下載 ZIP 的使用者可以解壓縮後，在專案根目錄執行下列指令。先確認 `gh auth status` 顯示你要使用的帳號：

```bash
git init -b main
git add .
git commit -m "Initial Taiwan stock digest"
gh auth status
gh repo create tw-stock-digest --private --source=. --remote=origin --push
```

GitHub CLI 不是必需；亦可在 GitHub 建立儲存庫後使用一般 git push。必須包含 `.github/workflows/`，排程檔需位於預設分支。

先到 **Actions → Taiwan stock digest → Run workflow**，保留 `demo=true`。成功後下載 `tw-stock-digest-...` artifact，檢查 `demo/latest.md`。CI 也會驗證單元測試與 demo。

## 2. 個人 Google Drive：只做一次 OAuth 授權

個人 My Drive 不使用「分享資料夾給 service account」作為預設方案：service account 沒有自己的儲存配額，不能藉此成為一般 My Drive 檔案擁有者。本專案用你的 OAuth 身分，且只要求 `drive.file`。

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 建立或選擇專案，啟用 **Google Drive API**。
2. 在 Google Auth Platform 設定品牌／OAuth 同意畫面；個人帳號通常選 External。測試期間將自己加入 Test users，設定 scope `https://www.googleapis.com/auth/drive.file`。
3. 建立 **Desktop app** 類型 OAuth client，下載 JSON 存為專案根目錄的 `client_secret.json`。
4. 在你自己的電腦上執行（會開啟 Google 授權頁，不是在 Actions runner 執行）：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-setup.txt
python scripts/setup_drive.py --client-secret client_secret.json
```

Windows 的虛擬環境啟用方式為 `.venv\Scripts\activate`；日常 CLI 在需要時也可能要安裝 `tzdata`，GitHub Ubuntu runner 已有時區資料。

授權完成後，工具會以本程式建立／找到 `TW Stock Digest` 資料夾，輸出它的 `GDRIVE_FOLDER_ID`；憑證保存為 `gdrive-oauth.json`，不印出憑證內容。

**請使用工具建立的資料夾 ID。** 在 `drive.file` 權限下，手動貼任意既有資料夾 ID 不一定可存取。

5. 到 GitHub 儲存庫 **Settings → Secrets and variables → Actions → Secrets**，新增：

| Secret | 填入內容 |
|---|---|
| `GDRIVE_OAUTH_JSON` | `gdrive-oauth.json` 的完整 JSON 內容 |
| `GDRIVE_FOLDER_ID` | 工具輸出的資料夾 ID |

也可以用 GitHub CLI 在本機設定，避免把憑證貼入聊天：

```bash
gh secret set GDRIVE_OAUTH_JSON < gdrive-oauth.json
gh secret set GDRIVE_FOLDER_ID
```

**長期排程注意：** External OAuth app 若維持 Testing，含 Drive scope 的 refresh token 通常 7 天到期。要長期使用，依 Google 當前規則將 app 改為 In production，並重新執行授權工具產生新 token，再更新 Secret。是否需要驗證取決於使用範圍與 Google 顯示的要求；Publishing 不等於保證 token 永遠有效。

若要重做授權，保留舊檔並使用新的輸出檔名，例如 `--output credentials-new.json`；成功後再更新 GitHub Secret。請不要把私密 JSON 提交到 git。

若工具已保存憑證、卻在建立 Drive 資料夾時因網路失敗中斷，目前也需要使用新的 `--output` 檔名重新授權；不能直接以同一輸出檔名續跑。

6. 再次手動執行 workflow，將 `demo` 取消勾選。若盤中或假日只有舊資料，結果會是 skipped；盤後再跑即可。確認 Drive 出現交易日期資料夾及 `latest.md`。

## 3. 啟用 Claude 自動分析

這一步會產生 Anthropic API 費用；Claude Pro 網頁訂閱不等於 API 額度。

1. 在 [Claude Console](https://platform.claude.com/) 建立 API key，設定你接受的帳戶支出限制／額度。
2. 在 GitHub Secrets 新增 `ANTHROPIC_API_KEY`。
3. 在 **Variables** 新增 `ENABLE_ANALYSIS=true`。
4. 可設定 `CLAUDE_MODEL`；預設為 `claude-haiku-5-5`，請以 [官方模型清單](https://platform.claude.com/docs/en/models/overview) 與你的帳號可用模型為準。
5. 手動跑一次非 demo，檢查 Drive 中的 `analysis.md` 與 `analysis.json`；後者記錄 input/output tokens。

停用付費分析：把 `ENABLE_ANALYSIS` 改為 `false`。資料仍繼續收集。若不使用 API，可以下載／開啟 `claude-prompt.md`，手動交給 Claude；這是人工流程，不是 Drive 自動觸發。

只給 API key 而未啟用 `ENABLE_ANALYSIS` 不會付費呼叫。啟用分析卻未提供 key 會明確失敗，以免誤以為已自動分析。

## 4. 調整自選股與日期

`WATCHLIST` 例：`0050,2330,2317,2454,6488`。代碼以逗號分隔，不加 `.TW`／`.TWO`，最多 50 檔。這只調整摘要內容，CSV 仍收集兩市場的涵蓋證券。

CLI 範例：

```bash
python -m tw_stock_digest --no-drive --watchlist 0050,2330,6488
```

`--date YYYY-MM-DD`／Actions 的 `expected_date` 是驗證來源日期。例：今天早上 API 仍回傳昨天，若你確實要保存昨天快照，可指定昨天。來源一旦更新為今天，無法用這個參數取得更早資料。

## 5. 失敗排查

| 情況 | 處理 |
|---|---|
| `status=skipped`，來源日期較舊 | 檢查是否休市、尚未盤後或來源更新延遲；不需新增 AI 金鑰 |
| `stage=configuration` | 檢查兩個 Drive secrets 是否完整；OAuth 是否到期；是否開啟分析卻沒有 key |
| `stage=collect` | 下載 artifact 查看已收集的 raw；可能是 HTTP 限流、欄位異動、OHLC 或日期不一致 |
| `stage=upload_collection` | 檢查 Drive API 是否啟用、資料夾是否由本 app 建立、儲存容量與 OAuth scope |
| `stage=analyze` | 資料已保存；檢查模型 ID、API 額度、網路，或輸出被 token 上限截斷 |
| `stage=upload_analysis` | 本次 artifact 仍有分析結果；可保留它，待 Drive 恢復後重新跑，避免遺失成功的付費結果 |
| `stage=publish_latest` | 日期資料已保存；檢查 Drive 根資料夾是否有重複檔名或損壞的 latest.json |
| 長期無排程 | 確認預設分支、Actions 是否啟用；公開 repo 60 日無活動可能停用排程 |

錯誤日誌刻意不包含 API 回應 body、金鑰或 OAuth JSON，只提供失敗階段與例外類別。**沒有 Google／Anthropic 憑證時，只能驗證本機與模擬遠端邊界，不能宣稱雲端端到端已通過。**

## 6. 延伸方向

先確認每日資料可靠保存，再增加 FinMind 的歷史行情、法人與月營收；計算指標後才傳給模型。新聞應另存來源連結與時間，與數據事實分開。此版本沒有下單功能。
