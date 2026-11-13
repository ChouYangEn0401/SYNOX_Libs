# ADR-0003：工具怎麼附一個完整的網頁畫面（app），以及記帳的 app

- 狀態：**提案，等維護者核准**（2026-11-14）。這份文件裡沒有任何程式已經寫了。同日第二版：照維護者的回覆加了「框架是選用的」、匯出、「有在用」的規則，刪類別維持不問。
- 範圍：SYNOX_Libs 的工具怎麼附 app；`packs/money`（0.2.0 → 0.3.0）的 app。
- 依據：SYNOX 的 `docs/adr/0023-tool-apps-services.md`（主程式那一側的設計：誰開埠、登入、隔離、開關與提醒）與它的實作計畫
  `docs/lab/2026-11-14-tool-apps-implementation-plan.md`（下面叫「計畫」；C1–C11 是那份計畫的合約編號）。**SYNOX 那邊的工作包 1、2、3、6 合進去之後，這裡才動手。**

## 背景

維護者要的：工具裝進 SYNOX 之後，每個也是自己的 app——在 SYNOX 主機台或 Telegram 按「開啟服務」，SYNOX 在這台電腦上開一個只有本機連得到的網址，
放這個工具的完整畫面（例如一本可以看、可以改的帳）；不用就關掉。Telegram 照舊是下指令的地方，畫面是拿來看與整理的。

現在工具只有一小塊唯讀面板（`panel.html`＋`web.api`，例如記帳的「這個月花了多少」），不能改資料，而且面板跟 SYNOX 主機台是同一個來源。

## 決定

### 1. 工具怎麼附 app

**app 是工具自己選用的東西**（維護者 2026-11-14 的想法：框架由官方提供，工具要用才宣告；不用的工具就沒有這一面）。這份設計就是這樣，明講三點：

1. **沒有 `web.app` 的工具完全沒有 app 表面**：SYNOX 不會替它開埠、沒有任何網頁路由、狀態清單裡沒有它。這裡的九個工具裡，沒做 app 的那幾個什麼都不用改。
2. **選用的方式是宣告，不是 import**：`pack.json` 寫 `web.app`（含 `"kit": 1`＝照第 1 版共用元件寫的）。框架本身——伺服器那一側的檢查、瀏覽器那一側的共用元件——在 SYNOX 主程式裡，不在這個工具庫裡。
   理由：工具之間不能互相 import，這裡共用的程式都是用 `scripts/sync_shared.py` 一份一份複製進每個 `pack.py` 的；安全檢查如果這樣複製，就會有好幾份、各自停在不同版本，別人做的工具還能改掉自己那一份。放在 SYNOX 只有一份、工具改不到。
3. **官方工具多守一條**：畫面程式**不准自己連線**（不准出現 `fetch(`、`XMLHttpRequest`、`sendBeacon`、`WebSocket`），讀、做動作、匯出一律經過 `SynoxApp.*`。
   SYNOX 對一般工具只把這件事列成提醒；這個工具庫用自己的測試（`tests/test_apps_common.py`，掃每個有 `web.app` 的工具）把它當成不准。
   這樣每個官方 app 的防偽造、確認框、匯出前的提醒、「人還在」的訊號都是同一份程式，不會有哪個工具漏做。

| 項目 | 做法 |
|---|---|
| 檔案 | 工具資料夾裡多一個 `app/`：`index.html`（入口）、自己的 `.js`、`.css`、圖。**只能放畫面用的檔**（副檔名白名單在 SYNOX 的第 1 關）；`app/` 裡不放 `.py` |
| 宣告 | `pack.json` 的 `web.app`：`entry`、`root`、`title`、`kit`、`reads`（可讀的資料）、`actions`（可做的動作與危險程度）、`exports`（可匯出的東西，選填）。原本的 `web.panel`／`web.api` 留著不動 |
| 誰開伺服器 | SYNOX。工具**不開埠、不連網、不多要任何能力**；`capabilities` 不變 |
| 讀資料 | `pack.app_read("名字", fn)`，`fn(user, params) -> 可轉 JSON`；只回這個人自己的（`pack.store.of(user)`） |
| 改資料 | `pack.app_action("名字", fn)`，`fn(user, values) -> dict`。**每個動作一定有同名的指令**：欄位、檢查、危險程度、權限都用那個指令的（SYNOX 載入時核對，不一樣就整個 app 不掛） |
| 一份邏輯 | 每個會改資料的功能寫成一個**核心函式**（吃資料與參數、回結果，不碰 Telegram）；指令把結果排成訊息，動作把同一個結果回成 JSON。不准有「只有畫面能做」或「畫面的檢查比較鬆」的路 |
| 畫面 | 純 HTML／CSS／JS，沒有建置步驟、沒有外部資源、不內嵌程式（`<script>` 一律 `src=`）。入口頁載入 SYNOX 提供的 `/_synox/kit.js`、`/_synox/kit.css`（讀、做動作、確認框、表單、頁首列都在裡面）；工具只寫自己的頁。畫面上的字一律 `textContent` |
| 匯出 | `pack.app_export("名字", fn)`，`fn(user, params) -> kit.Download(檔名, 類型, 位元組)`。工具只負責產生內容；「先講清楚沒有加密」「多人共用的 SYNOX 不給沒加密的」「記一行稽核」都是 SYNOX 做的，工具不用寫、也繞不過 |
| 不輪詢、不自己報「還在」 | 畫面不做定時的背景請求。「有在用」由 SYNOX 判斷：對這個 app 的請求，加上共用元件在「分頁看得到、而且使用者剛有動滑鼠／打字／捲動／點」時送的訊號（一分鐘最多一次）。工具**不要**自己送這個訊號——開著沒人看的分頁不該讓服務一直開著。資料在進頁、切分頁、做完動作之後重抓 |
| 閒置設定 | 多久沒用要關，是使用者在 SYNOX 一個工具一個工具選的（預設 30 分鐘提醒、120 分鐘自動關）。**工具不能在宣告裡替自己選**，官方工具也一樣：我們這邊出去的東西預設就是最保守的 |
| 舊的 SYNOX | `pack.py` 用 `if hasattr(pack, "app_read"):` 包住登記；`pack.json` 的 `requires.sdk` 寫 `">=0.2.3,<0.3"`、`requires.synox` 寫有這個功能的那一版（發版時定）。舊的 SYNOX 會直接說版本不合、不裝，不會裝成半套 |
| 測試 | 用 SYNOX 的 `synox.web.apps.testing.AppClient`（假瀏覽器，直接呼叫，不開埠）。每個動作都要有一條「跟打指令的結果一樣」的測試 |
| 打包 | `scripts/build.py` 不用改流程（`app/` 的檔本來就會進 `integrity`）；`index.json` 每筆多 `"app": true/false`，給目錄頁標「有完整畫面」 |
| 公開檢查 | `app/` 裡的檔一樣過 `scripts/public_check.py`；畫面的範例資料用假的 |

### 2. 記帳的 app（money 0.3.0）

**定位不變**（ADR-0002）：money 維持簡單。app 不加記帳功能，只是把已經有的指令變成看得到、點得到的畫面。唯一新增的指令是「改一筆」（畫面一定需要，Telegram 也因此多一個）。

#### 2.1 頁面（一個入口頁，四個分頁）

| 分頁 | 看到什麼 | 能做什麼 |
|---|---|---|
| 總覽 | 選月份；這個月花了多少、預算條（還剩／超過）、收入、轉帳（標「不算花費」）；各類別的長條；有設上限的類別的提醒 | 切月份 |
| 帳目 | 表格：日期、種類、類別、金額、備註、用什麼付；上方篩選（月份、種類、類別、關鍵字）；篩選後的合計 | ＋記一筆（支出／收入／轉帳）、改一筆、刪一筆（危險：會先問你）、撤銷最後一筆、匯出（照目前的篩選；2.7） |
| 類別 | 照大類分組：名稱、種類、關鍵字、每月上限 | 新增、改大類／種類／加關鍵字、刪類別（已記的帳不動）、設／拿掉上限、載入範本 |
| 預算 | 每月總預算、這個月用了多少 | 設定、拿掉 |

- 每一頁右上固定一顆「＋ 記一筆」。
- 空的時候：「還沒有記帳。按『＋ 記一筆』，或在 Telegram 打 `/money 120 午餐`。」
- 長條與比例條用 CSS 畫，不用圖表函式庫。金額格式化在畫面（`SynoxApp.fmt.number`），**加總與判斷都在伺服器**（畫面不自己算錢）。
- 檔案：`app/index.html`、`app/money.js`、`app/money.css`。`money.js` 只做「抓資料 → 畫出來 → 送動作」。

#### 2.2 讀（`web.app.reads`）

| 名字 | 參數（都是選填的字串） | 回什麼 |
|---|---|---|
| `month` | `month`＝`YYYY-MM`（預設這個月） | `{"month", "expense", "income", "transfer", "budget", "left", "by_category": {"expense": [{"category", "amount"}], "income": […], "transfer": […]}, "caps": [{"category", "cap", "used", "left", "over"}], "months": ["2026-11", …]}`（`months`＝有帳的月份，新到舊，最多 24 個） |
| `entries` | `month`、`kind`、`category`、`keyword`、`limit`（預設 100、最多 500）、`before`（編號，往前翻頁） | `{"entries": [{"id", "kind", "amount", "category", "memo", "date", "account"}], "sums": {"expense", "income", "transfer"}, "more": true/false}`（新的在前） |
| `categories` | — | `{"categories": [{"name", "group", "kind", "keywords", "cap"}], "templates": [{"key", "title", "about", "items": ["早餐", …]}], "kinds": {"expense": "支出", "income": "收入", "transfer": "轉帳"}, "unsorted": "待分類"}` |

參數的檢查用現有的 `check_month`、`check_kind`、`kit.positive_int`；不合 → `kit.AppError(訊息, code="invalid", status=400)`，訊息跟指令那條同一句。

#### 2.3 動作（`web.app.actions`；每個都是同名的指令）

| 動作 | 指令 | 危險程度 | 欄位（跟指令同一份 `fields`） |
|---|---|---|---|
| `add` | `/money` | 會寫入 | amount、category、memo、date、account |
| `in` | `/money_in` | 會寫入 | 同上 |
| `transfer` | `/money_transfer` | 會寫入 | 同上 |
| `edit` | `/money_edit`（**新**） | 會寫入 | n（編號，必填）、amount、category、memo、date、account、kind |
| `undo` | `/money_undo` | 會寫入 | — |
| `rm` | `/money_rm` | **危險** | n |
| `budget` | `/money_budget` | 會寫入 | amount（0＝拿掉） |
| `cap` | `/money_cap` | 會寫入 | category、amount（0＝拿掉） |
| `cat_add` | `/money_cat_add` | 會寫入 | name、group、keywords、kind |
| `cat_rm` | `/money_cat_rm` | 會寫入 | name |
| `template` | `/money_template` | 會寫入 | name |

- 危險程度**照現有指令**，不因為是畫面而改。`cat_rm` 維持「會寫入」、畫面上刪類別不另外問（維護者 2026-11-14 定的：已記的帳不動，刪錯了再加回來就好）。要改危險程度就改指令，兩邊一起變。
- `rm` 的確認框文字由工具給（`describe`）：「刪掉 #12　11/08　午餐 120 元（跟同事）？刪了不能復原。」——就是 `/money_list` 的那一行。
- 動作回傳：`add`／`in`／`transfer`／`edit` 回 `{"entry": {…}, "notes": ["（從「咖啡」猜是 飲料…）", "預算還剩 3,200 元", …]}`；`notes` 就是 Telegram 回覆裡第一行以後的那些句子，畫面拿來當提示。
  其他動作回改完之後的那一塊資料（例如 `budget` 回 `{"budget", "spent"}`、`cat_add` 回 `{"created", "category"}`、`rm`／`undo` 回 `{"removed": {…}}`）。
- 找不到（`#99` 不存在、沒有這個類別、沒有可以撤銷的帳）→ `kit.AppError`，訊息跟指令回的那句一樣（去掉開頭的 ❌／💰）。

#### 2.4 新指令 `/money_edit`

`/money_edit <編號> [amount=] [category=] [memo=] [date=] [account=] [kind=]`（會寫入；AI 可以用）。

- 只改有給的欄位；`memo=-`、`account=-` 表示清空，`category=-` 表示改回「待分類」。畫面上把欄位清空時送的就是 `-`。
- 改 `category` 而沒給 `kind`：類別是自己清單裡的 → `kind` 跟著那個類別走；不是 → `kind` 不變。不做關鍵字猜類別（那是記的時候的事）。
- 改 `date`：`date` 換掉，`at` 不動（`at` 是「寫進來的時間」）。金額要大於 0。至少要給一個要改的欄位。
- 回覆：「✏️ 改好了：#12 11/08  午餐 150 元（跟同事）」＋（改了金額、類別、日期或種類時）跟記帳時一樣的預算與上限提醒。

#### 2.5 `pack.py` 怎麼拆（行為不變的重構）

現在的邏輯是 `install()` 裡的閉包，結果直接 `api.send_message`。拆成模組層的核心函式（吃 `data` 與已檢查的 `values`，回 dict；不碰 `api`、`pack`）：

`op_add(data, values, forced, today)`、`op_edit(data, values, today)`、`op_undo(data)`、`op_rm(data, n)`、`op_budget(data, amount)`、`op_cap(data, name, amount)`、
`op_cat_add(data, …)`、`op_cat_rm(data, name)`、`op_template(data, name)`；看的：`view_month(data, key)`、`view_entries(data, filters)`、`view_categories(data)`。
找不到／不合規則用一個模組內的小例外（例如 `Refuse(message)`）表示；指令那層接住回「❌ …」，動作那層轉成 `kit.AppError`。

`install()` 裡每個功能剩三段：指令（`update(user, op)` → 排成現在那幾行字）、動作（`update(user, op)` → 回 dict）、登記。
**現有 `tests/test_money.py` 的 22 個測試一個字都不改就要過**——這是「指令的行為沒變」的證據。`summary()`（面板用）改成叫 `view_month`，回傳的形狀不變。

資料格式不變（`schema` 還是 2）；`edit` 不需要新欄位。所以 0.3.0 → 0.2.0 退得回去。

#### 2.6 `pack.json`

`version` → `0.3.0`；`web` 加 `app`（`entry: "app/index.html"`、`root: "app"`、`title: "記帳本"`、`kit: 1`、上面的 `reads` 與 `actions`、`exports: ["entries_csv"]`）；`requires.sdk` → `">=0.2.3,<0.3"`；`requires.synox` → 有工具服務的那一版。
`capabilities` 不加任何東西；`storage`、`data`、`access`、`surfaces` 不變。`stage` 留在 `alpha`。

從 0.2.0 升級時，SYNOX 的升級報告會出現紅字「新加了完整的網頁畫面（工具服務）」、每個動作、「畫面多了匯出」——這是預期的，寫進 CHANGELOG。

#### 2.7 匯出（維護者 2026-11-14：要可以匯出，不要擋個人使用者）

| 項目 | 做法 |
|---|---|
| 名字 | `entries_csv`（`web.app.exports`） |
| 參數 | 跟 `entries` 的篩選一樣：`month`、`kind`、`category`、`keyword`（都選填；都不給＝全部）。沒有筆數上限。檢查用同一組 `check_*`，不合 → `kit.AppError` |
| 內容 | UTF-8、開頭帶 BOM（Excel 打開中文才不會亂碼）、CRLF 換行。欄位：`編號,日期,種類,類別,金額,備註,用什麼付`；種類寫中文（支出／收入／轉帳）；金額是純數字、不加千分位；照日期舊到新 |
| 防試算表公式 | 文字欄位（類別、備註、用什麼付）開頭是 `=`、`+`、`-`、`@`、Tab、換行的，前面加一個 `'`（不然 Excel 會把別人塞進備註的字當公式執行） |
| 檔名 | `money-<月份或 all>.csv`（例如 `money-2026-11.csv`） |
| 核心函式 | `view_entries(data, filters)` 的結果 → `to_csv(rows) -> bytes`（模組層、純函式，用標準函式庫的 `csv`） |
| 個人版 | 可以下載。下載前 SYNOX 一定先顯示：「匯出的檔案沒有加密，打得開這個檔的人都看得到內容。存在你放心的地方，用完就刪。」 |
| 伺服器版（多人共用） | **沒有匯出**：SYNOX 直接擋（工具的函式不會被叫到），按鈕是灰的、旁邊一句說明。等「加密的匯出」定案之後才有（見「要維護者決定的」） |
| Telegram | 沒有 `/money_export`（這一輪不讓 bot 送檔） |

工具這邊**不寫**任何下載、加密、提醒的程式；`money.js` 只有一行 `SynoxApp.download("entries_csv", 目前的篩選)`。

### 3. 測試（`tests/test_money_app.py`；不開埠、不連網）

1. `packs/money` 過 SYNOX 的第 1、2 關，信任層級仍是 T1（`app/money.js` 沒有把它變成 T2）；`tests/test_gates.py`、`test_build.py` 照舊過。
2. SYNOX 的 `specs_of` 對 money 沒有 problem：每個動作都有同名、同危險程度的指令。
3. **同一件事兩條路結果一樣**：對 2.3 的每個動作，一個新的 bot 用指令做、另一個用 `AppClient.do` 做（同一組值、同一個假的「現在」），兩邊保管箱裡的資料相同（`at` 除外）。
4. 檢查訊息一樣：金額不是數字、日期看不懂、種類不對、類別名太長——動作回的 `fields` 訊息等於指令回的那句。
5. `rm`：沒確認 → 409 與確認文字（含那一筆的內容）；確認後真的刪掉；`#99` → `refused`。
6. `edit`：只改有給的；`-` 清空；改類別時 `kind` 的規則；編號不存在；什麼都沒給 → 拒；改完的預算提醒；Telegram 的 `/money_edit` 同樣的案例。
7. 讀：三個讀的形狀；`entries` 的每種篩選、`limit` 上限、`before` 翻頁；`month` 給壞的值 → 400；`month` 的數字等於 `/money_month` 的數字。
8. 伺服器版兩個人各自進 app，互相看不到、改不到（接在現有 `test_isolation.py` 的做法）；動作之後磁碟上仍是密文。
9. 假的舊 SYNOX（`pack` 物件沒有 `app_read`）：`install()` 照樣成功、指令都在。
10. `scripts/build.py money`：`.synoxpack` 的 `integrity` 有 `app/` 的每個檔；`index.json` 那一筆 `"app": true`。
11. `scripts/public_check.py` 乾淨；`app/` 的檔過 SYNOX `appscan` 的規則（沒有對外網址、沒有內嵌程式）。
12. 匯出：個人版走兩步之後拿到的位元組——開頭是 BOM、表頭對、筆數等於同樣篩選的 `entries`、金額沒有千分位；備註是 `=1+1`、`@x`、`-5` 的三筆在檔裡前面都多了 `'`；
    備註裡有逗號、引號、換行的照 CSV 規則包起來（用 `csv` 讀回來內容一樣）；檔名對。伺服器版 → 被 SYNOX 擋、`to_csv` 沒被叫到。壞的 `month` → 400。
13. `tests/test_apps_common.py`（新，掃每個有 `web.app` 的工具；現在只有 money）：`app/` 的 `.js` 裡沒有 `fetch(`、`XMLHttpRequest`、`sendBeacon`、`WebSocket`、`setInterval`、`innerHTML`、`/api/alive`；
    入口頁載入了 `/_synox/kit.js` 與 `kit.css`；`pack.json` 的 `kit` 是 `1`；`web.app` 裡沒有不認得的鍵；每個動作都有同名指令（第 2 項的通用版）。之後每個新 app 自動被它檢查。

### 4. 其他要跟著改的

- `packs/money/README.md`、`SKILL.md`（加 `/money_edit`；一段「完整畫面」：怎麼開、怎麼關、關了資料還在）、`CHANGELOG.md`（未發佈）、`catalog.json` 不用動。
- `scripts/build.py` 的 `entry_for` 加 `"app"`；`tests/test_build.py` 補一條。
- 工具說明頁（`docs/site/money.html` 與線上版）這一輪不改；之後跟教學一起補「完整畫面」一節。

### 5. 其他工具的順序

| 順序 | 工具 | 為什麼排這裡 |
|---|---|---|
| 1 | money | 維護者舉的例子；有讀、有寫、有危險動作，能把整條路走一遍 |
| 2 | todo | 最單純的清單增修刪，用來確認共用元件夠用、不用每個工具重寫表單 |
| 3 | calendar | 多一個月曆格的畫面；日期解析（`_shared/zhdate.py`）已經有 |
| 4 | bullet | 一天一頁的畫面 |
| 5 | notes | 現在只有很少的指令；要先決定它要不要長成可以編輯的筆記，再做畫面 |
| 不做 | notion、todoist、google-calendar、apple-reminders | 資料在對方的服務，畫面也在對方那邊 |

每個工具照第 1 節同一套做；有新的共用需求（例如月曆格）先加進 SYNOX 的共用元件，不各寫各的。

## 不做（這一輪）

- 匯入、圖表函式庫、多幣別、帳戶對帳——ADR-0002 已經說 money 不長成完整記帳軟體。
- 伺服器版的匯出（等加密格式定案）；用 Telegram 匯出；CSV 以外的格式。
- 畫面自動更新（Telegram 記了一筆，畫面要自己重新整理或切分頁才看得到）。
- 把 `panel.html` 拿掉或改掉。
- 工具自己開伺服器、連網、或放任何要建置的前端框架。

## 維護者已經決定的（2026-11-14）

| 題目 | 決定 | 寫在哪 |
|---|---|---|
| 記帳 app 要不要匯出 | 要。個人版下載沒加密的 CSV（先講清楚）；多人共用的只給加密的 | 2.7 |
| 畫面上刪類別要不要先問 | 照指令：會寫入、不問 | 2.3 |
| app 框架 | 工具自己選用（宣告），沒選用就沒有 app 這一面 | 第 1 節開頭 |

## 還沒定的（維護者討論中）

| 題目 | 選項 | 現在先這樣 |
|---|---|---|
| 伺服器版的加密匯出用什麼格式 | A 伺服器產生有密碼的 zip／B 在瀏覽器裡加密（密碼不離開瀏覽器，伺服器的管理員看不到密碼）／C 先不做 | **不決定、不實作**；伺服器版沒有匯出。兩種做法的事實整理在 SYNOX ADR 0023「還沒定的」 |

不管最後選哪一種，加密都在 SYNOX 那一側做；工具這邊的 `entries_csv` 與 `to_csv` 不用改。

其餘（誰開埠、怎麼登入、閒置設定、AI 能不能開服務）在 SYNOX ADR 0023。

## 給實作者的步驟與驗收（SYNOX 計畫的工作包 7）

前提：SYNOX 的 `feat/tool-apps` 已經合了工作包 0、1、2、3、6。測試時讓 `scripts/_synox.py` 找到那個工作樹（`SYNOX_HOME`），只 import、不改它。
這台開發機**不准開任何網路埠**：不啟動 SYNOX、不起伺服器、不開瀏覽器；只跑 pytest（一次一份）。

1. 重構 `pack.py`（2.5）。先不加任何新功能；跑 `tests/test_money.py`，22 個不改就過。單獨一個 commit。
2. 加 `/money_edit`（2.4）與它的測試。
3. 加 `app_read`／`app_action`／`app_export` 的登記（2.2、2.3、2.7，包在 `hasattr` 裡）、`to_csv` 與 `pack.json`（2.6）。
4. 寫 `app/`（2.1）。字都放在 `money.js` 開頭的 `TEXT`；連線只用 `SynoxApp.*`。
5. `tests/test_money_app.py`（第 3 節 1–12）與 `tests/test_apps_common.py`（第 13 項）。
6. README、SKILL、CHANGELOG、`build.py` 的 `"app"`。

驗收：第 3 節 13 項全過；`tests/` 全部照舊過；`scripts/public_check.py` 乾淨；`python scripts/build.py --check` 沒有問題；
在分支上 commit，不合併、不推、不發版（`VERSION` 不動）。真的在瀏覽器裡點的驗收在 SYNOX 計畫第 9 節，由維護者帶到別台電腦做。
