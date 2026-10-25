# 臺股策略研究

這份文件整理目前常見的臺股選股與交易策略、各策略在臺灣市場的實證結果，以及這些結論對本專案實作的影響。專案的策略模組（`tw_stock_digest/strategies/`）就是依這份研究挑選和設定參數。驗收結果見 [acceptance.md](acceptance.md)。

> 這是研究工具，不構成投資建議。下面的文獻結論來自各自的樣本期間，不保證未來仍然成立。

## 重點結論

1. **臺灣是傳統動能效應的知名例外**。Du、Huang、Liao（2009）指出臺股的下跌市況比美股更常出現，而動能策略在下跌市況後虧損更大，因此整體動能報酬不顯著。Ho 等人（2023）同樣開宗明義說臺股是動能效應的例外。所以「漲多的繼續漲」不能直接拿美股經驗套用。
2. **把報酬拆成盤中和隔夜兩段後，結果相反**。Ho 等人（2023）的盤中動能（IMOM，依過去盤中報酬排序）有顯著正報酬，隔夜動能（OMOM，依過去隔夜跳空報酬排序）則顯著為負，兩者可持續到一年。Zhang 等人（2023）也發現臺股隔夜報酬反映散戶情緒：短期延續、長期反轉，散戶交易比重越高越明顯。
3. **單純技術分析扣掉成本後沒有經濟利潤**。Chen、Huang、Lai（2011）控制資料探勘偏誤（White Reality Check、Hansen SPA）、非同步交易和交易成本後，找不到能穩定獲利的技術規則。
4. **法人買賣超有資訊價值，但外資和投信不同**。外資偏好中大型股、買超有延續性，在大型股與低淨值市價比股票的買賣能正向預測報酬（Lai、Lou、Shiu 2008；林軒白 2016）。投信偏好小型股、投資期間較短，當季前十大買超的異常報酬較高，但第四季標的隔年第一季落後大盤，符合年底作帳的說法（林軒白 2016）。
5. **52 週高點是重要錨點**。接近 52 週高點的股票有較強的後續動能（Liao、Chang、Wang 2023）；彩券型股票（極端正報酬、正偏態）的高估集中在遠離 52 週高點的股票（Wang、Lien 2023）。
6. **漲跌停會延長價格調整**。Shen、Wang（1998）發現漲跌停讓日報酬自我相關變強，作者推論前一天漲停的股票隔天可能高於均衡價格。

## 策略清單與實作對應

| 策略 | 規則（本專案設定） | 臺股實證 | 專案代號 |
|---|---|---|---|
| 傳統動能 12-1 | 依過去 12 個月（跳過最近 1 個月）報酬排序，買最強者，每月換股 | 臺股為例外，預期不顯著或為負 | `momentum_12_1` |
| 盤中動能 IMOM | 依過去 12 個月盤中報酬（收盤／開盤）累積排序，買最強者，每月換股 | 顯著為正 | `intraday_momentum` |
| 隔夜反轉 OMOM | 依過去 12 個月隔夜報酬（開盤／參考價）累積排序，買**最弱**者，每月換股 | OMOM 多空為負，所以反向操作 | `overnight_reversal` |
| 52 週高點 | 收盤價／過去 250 日最高價越接近 1 越優先，每月換股 | 正向動能，接近高點者較強 | `high_52w` |
| 低波動 | 過去 60 日報酬標準差最低者，每月換股 | 彩券型高波動股易被高估 | `low_volatility` |
| 短期反轉 | 過去 5 日報酬最低者，每週換股 | 漲跌停與量能造成短期過度反應；需扣成本檢驗 | `short_term_reversal` |
| 均線趨勢 | 收盤 > 60 日均線且 20 日均線 > 60 日均線，依 20/60 乖離排序，每週換股 | 扣除成本後無經濟利潤 | `ma_trend` |
| 外資連續買超 | 過去 20 日外資買超股數合計／20 日均量最高者，每週換股 | 中大型股有正向預測力 | `foreign_flow` |
| 投信買超 | 過去 5 日至少 4 日投信買超，依買超占量比排序，每週換股 | 當季前段標的異常報酬為正，Q4 可能作帳 | `trust_flow` |

盤後篩選（只需當日快照，也能用同一個介面回測，持有一天）：

| 篩選 | 規則 | 研究依據 | 專案代號 |
|---|---|---|---|
| 收盤漲停 | 漲幅 ≥ 9.5% | 漲停後隔天可能高於均衡價（Shen、Wang 1998） | `limit_up` |
| 強勢收高 | 收盤 = 最高價，且漲幅 > 0 | 盤中資訊反應不足，盤中強勢延續（IMOM 的單日版本） | `close_at_high` |
| 開高跳空 | 開盤較參考價 ≥ 3% | 隔夜報酬短期延續、長期反轉（Zhang 等人 2023） | `gap_up` |
| 成交值集中 | 當日成交金額最大的股票 | 資金集中度，常見盤後觀察項目 | `turnover_leaders` |

## 基本面與籌碼策略

第二批策略使用月營收、估值、融資融券與季度財報。這類資料最大的陷阱是**前視偏誤**：公司 9 月的營收要到 10 月 10 日前才公布，Q2 財報要到 8 月中才公布。本專案把每筆資料掛在「第一個確定已公開的交易日」，策略在那之前讀不到（`tw_stock_digest/fundamentals.py`）：

| 資料 | 來源 | 何時可用 |
|---|---|---|
| 月營收 | 公開資訊觀測站營業收入彙總表（上市、上櫃） | 次月 10 日公布期限後的第一個交易日 |
| 季度財報 | 公開資訊觀測站綜合損益表、資產負債表彙總表 | 各產業最晚的法定期限後：Q1 5/31、Q2 8/31、Q3 11/30、年報 3/31（保守地不讓早申報的公司占便宜） |
| 本益比、殖利率、淨值比 | 證交所 `BWIBBU_d`、櫃買中心 `peQryDate`，每月底 | 當日收盤後（隔天開盤下單） |
| 融資融券餘額 | 證交所 `MI_MARGN`、櫃買中心 `margin/balance`，每週最後交易日 | 當日收盤後 |

財報是年度累計數，單季數字由「本季累計 − 上季累計」算出。

### 臺股實證

- **月營收**：臺灣強制每月公布營收是全球少見的制度。陳柔君、劉晏睿、蕭朝興（2022）發現未預期月營收（MSURGE）能預測未來盈餘成長與股價報酬，而且和季營收、季盈餘驚喜各自有獨立的解釋力；機構投資人會在正向營收驚喜公布前加碼。洪偉倫（2026）用 2008–2025 年資料確認，控制 Fama-French 五因子與季盈餘因子後，未預期月營收仍有顯著異常報酬。邱廉松（2020）的事件研究發現 32 個產業中 25 個的營收驚喜與宣告期報酬正相關。
- **價值與殖利率**：Hsu、Lee、Chang、Fung（2015）用隨機優勢檢定發現，在臺灣「盈餘價格比（E/P）或股利價格比（D/P）比淨值市價比（B/M）更適合當價值指標」，結果在不同期間與經濟狀況下都成立。
- **融資融券**：Ting、Lu、Chou（2018）用 2006–2015 年上市股票月資料發現，散戶融資買進會把股價推高到合理價值之上、之後報酬較低；散戶融券則把價格壓低、之後報酬較高。
- **獲利能力**：Hanauer、Huber（2018）用 49 國資料發現獲利能力效果是全球現象，其中以現金基礎的毛利資產比最穩健，ROE 反而在美國以外不穩定。王芯儀（2021）用 1990–2020 年臺股資料發現五因子模型中的獲利因子（RMW）是改善定價的主要來源。

### 策略清單

| 策略 | 規則 | 換股 | 文獻預期 | 專案代號 |
|---|---|---|---|---|
| 月營收年增率 | 最新月營收年增率最高 | 每月 | 正 | `revenue_yoy` |
| 近 3 月營收年增率 | 近 3 月合計 ÷ 去年同期合計 | 每月 | 正 | `revenue_3m_yoy` |
| 未預期月營收 SURGE | （本月年增金額 − 過去 12 月平均）÷ 標準差 | 每月 | 正 | `revenue_surprise` |
| 低本益比 | E/P 最高，虧損排除 | 每月 | 正 | `low_pe` |
| 低股價淨值比 | P/B 最低 | 每月 | 較弱 | `low_pb` |
| 高殖利率 | 官方殖利率最高 | 每月 | 正 | `high_dividend_yield` |
| 融資暴增 | 4 週融資增加最多（以日均量為單位） | 每週 | 負 | `margin_surge` |
| 融資退場 | 4 週融資減少最多 | 每週 | 未直接檢驗 | `margin_retreat` |
| 高券資比 | 融券 ÷ 融資最高（融資 ≥ 500 張） | 每週 | 正 | `short_interest` |
| 高 ROE | 近 4 季淨利 ÷ 權益 | 每月 | 正（國際上較不穩定） | `roe` |
| 毛利資產比 | 近 4 季毛利 ÷ 總資產 | 每月 | 正 | `gross_profitability` |
| EPS 年增 | （本季 EPS − 去年同季）÷ 股價 | 每月 | 正 | `eps_growth` |

## 回測設定（對所有策略相同）

- **資料**：證交所 `MI_INDEX`（上市）、櫃買中心 `afterTrading/otc`（上櫃）逐日全市場行情；除權息參考價來自證交所 `TWT49U`、櫃買中心 `exDailyQ`；三大法人來自證交所 `T86`、櫃買中心 `insti/dailyTrade`。
- **還原報酬**：日報酬 = 收盤價 ÷ 當日參考價 − 1。一般日的參考價是前一日收盤價（收盤價減官方漲跌價差）；除權息日改用官方除權息參考價。這樣除息不會被當成下跌，得到的近似含息總報酬。
- **股票範圍**：四碼普通股（`1xxx`–`9xxx`），過去 20 日平均成交金額 ≥ 新臺幣 2,000 萬元，避免無法成交的小型股。ETF 只作為基準。
- **避免前視偏誤**：策略只能讀到當日收盤以前的資料（介面直接截斷）。訊號以 t 日收盤計算，**t+1 日開盤**才買進或賣出。
- **成交限制**：t+1 日沒有成交或開盤價就是漲停價時無法買進；沒有成交時無法賣出、延後到有成交的日子。
- **交易成本**：手續費買賣各 0.1425%（未打折，偏保守）、賣出證交稅 0.3%（ETF 為 0.1%）。來源：[口袋證券說明](https://www.pocket.tw/school/articles/3654/)。
- **持股**：每次選前 20 檔，等權重，換股之間不再平衡。
- **基準**：同範圍股票等權重組合（每日再平衡，未扣成本），以及元大台灣 50（0050）含息報酬。
- **參數不調整**：參數照文獻常用值固定，不針對回測期間最佳化，以免資料探勘偏誤（Chen、Huang、Lai 2011 的主要警告）。另外把回測期間切成前後兩半各自報告，檢查穩定性。

## 回測結果與文獻對照

完整數字見 [acceptance.md](acceptance.md)。價格策略期間 2024-10～2026-10、法人策略 2025-02 起、基本面策略 2023-11 起。

**怎麼判斷一個策略「有用」**：t 值在兩三年的資料裡很難達標，所以另外跑 30 組「每期隨機挑 20 檔」的組合當安慰劑，看策略**未扣成本**的超額報酬排在隨機分布的哪裡（≥ 95 百分位＝優於隨機，≤ 5 百分位＝劣於隨機）。未扣成本才是公平的訊號比較，因為隨機組合每期幾乎全部換掉；扣成本後能不能賺錢則另外看年化報酬。

### 這段期間的主軸：追熱度贏、價值與防禦輸

| 優於隨機（≥ 95 百分位） | 劣於隨機（≤ 5 百分位） |
|---|---|
| 盤中動能、均線趨勢、開高跳空、成交值集中、**未預期月營收**、**融資暴增** | 低波動、收盤漲停、強勢收高、**低本益比**、**高殖利率**、**融資退場**、**EPS 年增** |

2023–2026 年是 AI 帶動的強多頭，大型權值股主導（加權報酬指數年化約 45–55%，等權股票約 18–24%，依期間而定）。這種環境獎勵動能與資金關注度，懲罰價值股和防禦型股票，所以結果有很強的市況色彩。

### 與文獻對照

- **一致**：盤中動能為正（Ho 等人 2023）；傳統 12-1 動能與隨機無異（臺股動能例外）；收盤漲停隔天劣於隨機（Shen、Wang 1998 的推論）；**未預期月營收（SURGE）優於隨機**，扣成本後年化 +25.6%（陳柔君等 2022、洪偉倫 2026）。單純的營收年增率則與隨機無異，代表「相對自身歷史的驚喜」才是有效的資訊，原始成長率不是。
- **相反**：
  - **融資暴增**優於隨機（未扣成本超額 +33.9%，扣成本後年化 +44.7%），與 Ting 等人（2018，2006–2015 年資料）「融資買進之後報酬較低」相反。延後一週使用資料，超額報酬仍有 +17.7%，所以不是時間對齊的假象。比較合理的解釋是：多頭市場裡融資增加和價格動能同時發生；文獻預期的「高估後回落」可能需要更長時間或空頭才會出現。這個策略在空頭的風險很可能很大，不建議依此結論操作。
  - **低本益比、高殖利率**劣於隨機，與 Hsu 等人（2015）「E/P、D/P 是臺股較佳價值指標」相反，主要是市況（價值股在這波 AI 行情大幅落後），也與下一點有關。
  - **EPS 年增**劣於隨機。看選股結果，入選的多是被處分利益、投資損益灌大 EPS 的公司（投資控股、證券商、資產處分）。**在臺股用 EPS 選股，會被業外收益嚴重干擾**，改用營業利益是下一步可以做的修正。
- **不顯著**：ROE、毛利資產比、低淨值比、高券資比、外資買超、投信買超（93 百分位，接近門檻）。

### 成本仍然決定高週轉策略的命運

開高跳空、成交值集中的選股訊號優於隨機（未扣成本超額 +21.7%、+33.6%），但每天換股的成本讓年化報酬變成 −62.3% 和 −0.6%。融資暴增是少數訊號強到扣成本（每週換股）後仍大幅領先的策略。

### 結論

兩三年、偏多頭的樣本統計檢定力有限，結論應視為「這段期間的觀察」。比較站得住的是：**未預期月營收**與文獻一致且扣成本後表現好；**EPS 類指標**在臺股需要排除業外收益才可用；**高週轉策略**在臺股的成本結構下很難成立；以及這段期間任何等權選股都敵不過市值加權指數。

## 臺股制度上要注意的地方

- 漲跌幅限制 10%，漲跌停時可能買不到或賣不掉，回測已部分模擬。
- 處置股、全額交割股、暫停交易和下市在資料中只會表現為沒有成交；本專案沒有模擬分盤交易或預收款券。
- 當沖證交稅減半為 0.15%（目前延長到 2027 年底），但本專案策略都是隔日以上持有，不適用。
- 年底作帳和除權息旺季（7–9 月）會影響法人與殖利率相關策略，報告會分期檢查。

## 這份研究沒涵蓋的

- **機器學習組合**：Bui 等人（2023）用 86 個異常因子做神經網路／PLS 可得每月約 1.2%–1.5% 多空報酬，而且前 20 個重要預測因子中有 5 個與動能相關；但需要大量因子資料，超出這一版範圍。
- **借券、期貨與選擇權籌碼**；現金流量表（毛利資產比用的是會計毛利，不是 Hanauer、Huber 建議的現金基礎版本）。

## 參考文獻

- Du, D., Huang, Z., & Liao, B. (2009). [Why is there no momentum in the Taiwan stock market?](https://experts.nau.edu/en/publications/why-is-there-no-momentum-in-the-taiwan-stock-market/) *Journal of Economics and Business*, 61(2), 140–152.
- Ho, H.-W., Hsiao, Y.-J., Lo, W.-C., & Yang, N.-T. (2023). [Momentum investing and a tale of intraday and overnight returns: Evidence from Taiwan](https://scholars.ntou.edu.tw/handle/123456789/24532). *Pacific-Basin Finance Journal*, 82.
- Zhang, H., Tsai, W.-C., Weng, P.-S., & Tsai, P.-C. (2023). [Overnight returns and investor sentiment: Further evidence from the Taiwan stock market](https://ideas.repec.org/a/eee/pacfin/v80y2023ics0927538x23001646.html). *Pacific-Basin Finance Journal*, 80.
- Bui, D. G., Kong, D. R., Lin, C. Y., & Lin, T. C. (2023). [Momentum in machine learning: Evidence from the Taiwan stock market](https://scholar.nycu.edu.tw/en/publications/momentum-in-machine-learning-evidence-from-the-taiwan-stock-marke/). *Pacific-Basin Finance Journal*, 102178.
- Chen, C.-W., Huang, C.-S., & Lai, H.-W. (2011). [Data snooping on technical analysis: Evidence from the Taiwan stock market](https://ideas.repec.org/a/wsi/rpbfmp/v14y2011i02ns0219091511002238.html). *Review of Pacific Basin Financial Markets and Policies*, 14(2), 195–212.
- Lai, C. J., Lou, K. R., & Shiu, C. Y. (2008). [Does foreign investors trade predict stock return? Evidence from Taiwan](https://scholars.ncu.edu.tw/en/publications/does-foreign-investors-trade-predict-stock-return-evidence-from-t/). *International Journal of Information and Management Sciences*, 19(3), 477–494.
- 林軒白（2016）。[外資與投信之買超行為及資訊價值](https://tdr.lib.ntu.edu.tw/handle/123456789/19442)。國立臺灣大學財務金融學系碩士論文。
- Liao, L.-C., Chang, T.-P., & Wang, P.-H. (2023). [The effect of the movement in 52-week high on momentum profit: The evidence from Taiwan](https://ijbesar.ihu.gr/docs/volume16_issue1/16_01_07.pdf). *International Journal of Business and Economic Sciences Applied Research*, 16(1), 71–86.
- Wang, Z.-M., & Lien, D. (2023). [Limited attention, salient anchor, and the modified MAX effect: Evidence from Taiwan's stock market](https://ideas.repec.org/a/eee/ecofin/v67y2023ics106294082300027x.html). *The North American Journal of Economics and Finance*, 67.
- 陳柔君、劉晏睿、蕭朝興（2022）。[台灣股市的未預期月營收：綜合分析](https://lawdata.com.tw/tw/detail.aspx?no=484826)。《管理學報》，39(4)，503–545。
- 洪偉倫（2026）。[未預期營收、資產成長與樣本外預期報酬](https://thesis.lib.nccu.edu.tw/detail/5de91a46dbc69757987d294b65a5a9cc/)。國立政治大學國際經營與貿易學系碩士論文。
- 邱廉松（2020）。[上市櫃公司每月營收宣告對股價之影響](https://thesis.lib.nccu.edu.tw/detail/ee0855d75b33caded0fddf19a6dee3b7/)。國立政治大學國際經營與貿易學系碩士論文。
- Hsu, C.-H., Lee, K.-C., Chang, Y.-P., & Fung, H.-G. (2015). [Value versus growth: Taiwan evidence](https://www.emerald.com/insight/content/doi/10.1108/MF-07-2014-0202/full/html). *Managerial Finance*, 41(8), 845–856.
- Ting, P. H., Lu, C. Y., & Chou, P. H. (2018). [Margin trading, differences of opinion, and stock returns](https://pure.lib.cgu.edu.tw/en/publications/margin-trading-differences-of-opinion-and-stock-returns/). *Academia Economic Papers*, 46(3), 323–366.
- Hanauer, M., & Huber, D. (2018). [Constructing a powerful profitability factor: International evidence](https://hedgefundalpha.com/strategies/the-profitability-factor-international-evidence/)（工作論文，連結為摘要報導）.
- 王芯儀（2021）。[五因子模型於台灣股票市場報酬的實證研究](https://etd.lib.ncu.edu.tw/thesis/detail/e61ce5f5dbba4c3fac583330c3c986e5)。國立中央大學財務金融學系碩士論文。
- Shen, C.-H., & Wang, L.-R. (1998). [Daily serial correlation, trading volume and price limits: Evidence from the Taiwan stock market](https://ideas.repec.org/a/eee/pacfin/v6y1998i3-4p251-273.html). *Pacific-Basin Finance Journal*, 6(3–4), 251–273.
