# ADR-0008：記帳 0.5.0——補上原本那套記帳程式還沒搬的功能：對帳單匯入（描述檔＋adapter）、寫檢討的提供者

- 狀態：**已實作**（記帳 0.5.0-alpha.1，alpha；只在自動測試的假資料上跑過）。
- 範圍：`packs/money`（0.4.0-alpha.1 → 0.5.0-alpha.1）。延伸 [ADR-0004](0004-money-remake.md)；**取代**它第 5 節的
  「不含任何券商的對帳單匯入」與「不再叫外部工具」兩句（改成：匯入有，但格式全在描述檔；AI 檢討走注入的提供者）。
- 細節（指令名、欄位、Book 多的欄位）在 `packs/money/CONTRACT.md` 第 3、8、14 節。

## 背景

0.4.0 把維護者本機那套記帳程式的邏輯層搬了過來（ADR-0004）。這次逐檔比對兩邊的 `services/`，找出還沒搬的部分，
決定哪些要搬、怎麼搬才不會把「一個人、一家券商、一台電腦」的假設帶進公開的工具庫。

## 缺口表

比對方式：兩邊 `services/` 逐檔 `diff`；原本那套在 0.4.0 搬過來之後改過的檔（`git log --since` 看），逐項確認工具裡有沒有跟上。

| 原本的檔 | 決定 | 理由 |
|---|---|---|
| `htmltables.py`（網頁存下來的 html 表格 → 一列列格子） | **搬**（`engine/services/statements/tables.py`） | 只用標準函式庫；另外加了 CSV，兩種來源同一個介面 |
| `tradesimport.py`（股票成交明細） | **搬，改成 adapter `trades`** | 欄位名、表頭、交易種類的字、小計列的位置、當沖淨收付那一列全部移到描述檔；帳戶名不再有預設值（只有一個證券帳戶時自動用它，不然要指定） |
| `futuresimport.py`（期貨選擇權日報表） | **搬，改成 adapter `futures`** | 契約代號、每點多少錢、選擇權到期規則（第幾個星期幾）、均價零頭的容許值、核對的容許值都在描述檔 |
| `realizedimport.py`（已實現損益） | **搬，改成 adapter `realized`** | 欄位、當沖那一種的字、拆手續費與稅用的稅率在描述檔 |
| `claude.py`（叫一支本機腳本請 AI 寫檢討） | **搬介面、不搬實作**（`engine/services/llm.py`＋`bridge/writer.py`） | 原本綁一個本機工具、一個本機路徑、一個子程序；這裡改成注入的「寫檢討的提供者」。SYNOX 的 SDK 現在沒有給 python 工具叫 AI 的入口 → 預設沒有提供者 → `review_run` 回 `llm_unavailable`，其他照常 |
| `reviews.py` 的 `run_one`／`run_due` | **搬回來**（0.4.0 拿掉了） | 改成收提供者；額度用完延後、失敗記原因、成功照原本的規則驗證 |
| `importer.py`（舊試算表匯入） | **不搬** | 讀的是一份私人試算表的固定版面：特定工作表名、特定區塊、寫死的類別對照與固定項目；要 `openpyxl`（工具不准裝第三方套件）。通用的路已經有：`/money_import_csv`。做成描述檔等於替一個人的版面寫一份格式，別人用不到 |
| `stockimport.py`（手記的股票總帳 xlsx） | **不搬** | 同上：私人工作簿的工作表名與記法（心得表、沖單表、資金縮水幅度）、要 `openpyxl`。通用的部分（自己整理的一張交易清單）改由內建描述檔 `trades_csv`＋`/money_import_statement` 涵蓋 |
| `guide.py`（把手冊切成主題給外部聊天機器人） | **已有，不搬** | 那是為了外部機器人「一次回傳不能太長」；SYNOX 的 AI 用 `pack.json` 的 `agent.skill.topics`（`skills/*.md`）一樣是分主題載入 |
| 其他 44 個 `services/*.py`（不算 `__init__`） | **已有** | 差異只有機械改動（相對 import、去個人化的字、中性的用途代號、幣別小數位數） |
| 0.4.0 之後原本那套改過的：對明細（最後核對日、檢查模式）、未平倉的白話說明與「收掉的倉還有部位」、編輯一筆可以改店家與備註、狀態字與圖例、手機版面、誰欠誰 | **已有** | 逐項比過：`reconcile.py` 函式一樣、`wallet.py` 有 merchant／memo、`piggy.js` 有 `OPEN_WORDS` 與收倉警告、`home.js` 有圖例、`debts.py` 有 `people`／`owe` |
| 網站（`api/`）、命令列（`cli/`）、`restart.py`、`paths.py` 的環境變數 | **不搬** | SYNOX 的指令、畫面與 `pack.path_of` 取代它們（ADR-0004 第 2、4 節） |

## 決定

### 1. 對帳單：描述檔＋通用讀取器＋adapter 登記表

```
engine/formats/*.json                內建的範例描述檔（tw_trades_html、tw_futures_html、tw_realized_html、trades_csv）
<自己的檔案資料夾>/formats/*.json      使用者自己的描述檔（同一個 id 用自己的）
engine/services/statements/
  files.py     唯一碰檔案的地方，只讀：對帳單（只能在自己的檔案資料夾）、兩個地方的描述檔；有大小上限
  tables.py    html 表格（html.parser）與 CSV → 一張張表
  formats.py   描述檔的格式與檢查（共同欄位）、照描述檔解讀一格（日期、數字、商品）
  trades.py、futures.py、realized.py    adapter：KIND、REQUIRED／OPTIONAL 欄位、OPTIONS、check()、read()、run()
  __init__.py  ADAPTERS 登記表、catalog()（能用的＋壞掉的）、detect()（照表頭認，剛好一份才用）、run()
```

- **程式裡沒有任何券商的格式**：欄位名、表頭、交易種類的字、日期寫法、契約乘數、選擇權到期規則、稅率、容許值都在描述檔。
  程式裡只有跟格式無關的記帳規則（淨收付怎麼算、帳本已有的不重記、全部核對、重跑不會多、收掉的倉補記要重算）。
- **別家券商**：內容是這三種之一 → 加一份描述檔（不用改程式，使用者自己就能放）；完全不同的內容 → 加一個 adapter 模組、登記進 `ADAPTERS`。
- **描述檔有錯不猜**：列出每一個問題、那一份不能用（`/money_formats` 看得到）；沒指定格式時好幾份都對得上也不猜，要使用者指定。
- 內建描述檔的名字**不寫任何券商的名字**（範例是「台灣券商網頁對帳單常見的欄位」）；期貨的契約代號與每點多少錢是交易所公開的契約規格。
- 證明沒寫死：測試用內建的 CSV 描述檔讀同樣的交易、用自己寫的英文欄位描述檔讀另一種 CSV、用自己的描述檔加一個假契約，程式都不用改。

### 2. 寫檢討的提供者

- 介面：`provider(prompt, schema, opts) -> {"structured": dict, "model", "cost_usd"}`；額度用完丟 `ProviderQuota`、其他失敗丟 `ProviderFailed`
  （提供者自己的例外一律只留型別，不把內部細節帶出去）。
- 從哪裡來（`bridge/writer.py`）：掛在 pack 上的（`writer.register(pack, fn)`）→ SYNOX 給工具用的 AI（現在的 SDK 沒有，`from_synox` 回 None）→ 沒有。
- 沒有提供者：`/money_review_run` 回 `llm_unavailable`，什麼都不寫；SYNOX 的 AI 照舊用 `/money_report review=yes` → `/money_review_save`（0.4.0 的路不變）。
- 不叫任何外部程式、不讀任何路徑或環境變數、不碰任何密鑰。

### 3. 接到 SYNOX

- 新指令（都不是畫面的動作：畫面還不能上傳檔案、畫面的動作已經 39／40）：`import_statement`（WRITE）、`formats`（READ）、`review_run`（WRITE）。
  `pack.json` 的 `web.app.actions`／`reads`／`callers` 不用改。
- `bridge/session.py` 的 `Book` 多一個 `reviewer`（契約檔的改動，CONTRACT.md 第 8 節已寫）。
- 閘門（`tests/test_gates.py`）：`pathlib` 多准 `statements/files.py`、`html`（標準函式庫的 html.parser）只准 `statements/tables.py`。
  `requires.pip` 維持空的。

## 已知代價

- 選擇權放到到期當作 0 元歸零：價內到期有拿到錢要自己補（回報裡列出來）。到期日不算國定假日順延。
- 自動認格式只看表頭：兩份描述檔表頭一樣時要使用者指定 `format=`。
- 畫面還沒有對帳單匯入（SYNOX 畫面不能上傳檔案）：檔案要先放進自己的檔案資料夾，再打指令。
- 寫檢討的提供者要等 SYNOX 的 SDK 給 python 工具叫 AI 的入口才會自動有；在那之前只有測試的假提供者與「AI 助理自己寫」那條路。
