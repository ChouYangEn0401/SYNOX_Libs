# Changelog

工具庫本身的版本在 `VERSION`；每個工具的版本在自己的 `pack.json`（下面每行標出來）。格式照 Keep a Changelog。

## [Unreleased]

### Added
- **`guitar` 0.2.0-alpha.1 → 0.3.0-alpha.1（和弦圖；仍是 alpha、尚未實機驗證；要 SYNOX SDK 0.2.5 以上，`requires.sdk` `>=0.2.5,<0.3`、`requires.synox` `>=0.2.1`）**：
  **先給圖**：`/chord` 預設回和弦圖（圖片，經 `kit.send_image`）＋名稱＋一行按法；每根手指、組成音、提醒改成按「📖 說明」、最後加 `說明`、或 `/chord_info`。
  一個和弦可以用 `‹ 上一個`／`下一個 ›` 在同一則訊息裡翻其他按法（`kit.edit_image`；Telegram 改送新的一張）；好幾個（`D/F# Bb F Gm Cm`）排成同一張圖、每個有「換按法」。
  經 AI 呼叫的一律不出按鈕（預設按法的圖＋文字）。圖是自己畫的 SVG＋PNG（不靠外部套件，同一個和弦每次一模一樣；SVG 過得了 SYNOX 的白名單）。
  新指令：`/chord_info`、`/chord_alt`、`/chord_easy`（最好按的按法）、`/chord_tones`（組成音與音程、每條弦的音）、`/chord_all`（一個根音的常用和弦）、
  `/chord_basic`（入門 8 個）、`/chord_change`（換和弦每根手指怎麼動）、`/chord_strum`（節奏刷法）、`/chord_prog`（調的常見進行＋圖）。
  分散和弦 TAB 放等寬區塊（純文字時包 ```），說明一行一個記號，弦標 `e(1)`…`E(6)`。

### Fixed
- **`guitar` 0.3.0-alpha.1**：`/chord_arp F major 7` 以前回 F 大三和弦——拼出來的名稱（`F major 7`、`Fmajor7`、`FM7`、`Fmaj 7`、`Δ7`、minor／min、dominant、
  diminished、augmented、suspended、全形字、`♯`／`♭`／`＃`）都認得了；看不懂的會說哪個字看不懂、列出相近的名字，不再默默改成別的和弦。
  資料對照「吉他和弦全書」：補簡易 Bm `x x 0 4 3 2`、一指 G `x x 0 0 0 3`（都標「低音 D」）；簡易 B／Bb／Cm 的提醒寫明最低音不是根音；
  A 型 add9／madd9（要跨 5 格）拿掉；A 型 dim7 改成常見的 `x 3 4 2 4 x`（2-3-1-4）、m7b5 1-3-2-4；自動排的手指照格數給（差 3 格用小指），
  滑到第 0 格的手型不再把橫按的手指放在空弦上，還是要撐開 4 格的會標「要撐開」。
- **`money` 0.3.0 → 0.3.1**（SYNOX 獨立安全審查第 1 輪）：金額欄位改用 SYNOX 的 `kit.amount`（大於 0、最多兩位小數、不超過十億；預算與上限另外收 0＝拿掉）；`nan`、`inf` 這類不是一般數字的值一律擋下（以前會讓記一筆出錯、讓預算存成 NaN 之後每筆都壞）。
- **`calendar` 0.1.2**（ADR-0006 T5、P1）：`/cal_ics` 的檔名改成每次不同（`export-<時間>-<亂數>.ics`），同一個人同時匯出兩次
  不會互相蓋、互相刪；匯出前先清掉這個人資料夾裡留下的舊匯出檔（舊版的 `export.ics`、10 分鐘以前的）。
- **`google-calendar` 0.1.1 → 0.1.2**（ADR-0006 C1、P5）：`/gcal_setup`、`/gcal_code`、`/gcal_token` 打的 client secret、授權碼、
  refresh token 原本整串印在 `[ROUTE]` log（兩種模式都中）；`install` 改成對這三個叫 `kit.redact_in_logs`。
  `/gcal_status` 改成 `ai=False`（跟另外三個外部工具一樣，SKILL 改成請使用者自己打）。測試加一條 S6（log 裡沒有那些值）。

### Added
- **`money` 0.4.0-alpha.1 → 0.5.0-alpha.1**（**alpha，尚未實機驗證**；[ADR-0008](docs/adr/0008-money-statements-and-review-writer.md)）：補上原本那套記帳程式還沒搬的功能。
  **對帳單匯入** `/money_import_statement`（成交明細、期貨選擇權日報表、已實現損益；網頁存的 html 或 CSV）：照寫的數字記、全部核對、重匯不會多；
  格式全部寫在「格式描述檔」（JSON），程式只是通用的讀取器＋三個 adapter，內建幾份範例描述檔（不帶任何券商名），使用者可以在自己的 `formats/` 放自己的；
  `/money_formats` 列出能用的與壞掉的。**寫檢討的 AI** 改成注入的提供者介面：`/money_review_run` 請接上的 AI 寫，
  SYNOX 現在沒有給工具用的 AI 入口，所以預設回 `llm_unavailable`、什麼都不寫（AI 助理照舊用 `report` → `review_save`）。
  不搬：兩個只適合一份私人試算表版面、又要第三方 xlsx 套件的匯入（通用的 CSV 匯入本來就有）。`requires.pip` 仍是空的。
- **`guitar` 0.1.0-alpha.1 → 0.2.0-alpha.1（小節譜；仍是 alpha、尚未實機驗證）**：新指令 `/chord_chart`，把和弦進行排成一格一小節的譜（每行 4 小節，可改）：認得 `| C | G D/F# | Am | F |`、`C G Am F`（一個和弦一小節）、`C / / / G / / /`（照拍子）、`Am . . G`／`Am:3`（拍數）、`[主歌]`／`前奏：`、`%`、`N.C.`、`(x2)`、`|: :|`、Key／Capo／4/4／♩ 資訊行、和弦在上歌詞在下（歌詞照欄位分到小節）、`[C]` 夾在歌詞裡；也收 AI 看譜的照片填的小節譜 JSON（格式見 README，含照片上和弦位置 `image.marks`）。第一行可以先寫移法（`+2`、`G→A`），不貼＝用上次的譜。最後一份小節譜存在保管箱（每人一份），`GET /data` 多一個 `chart`。面板多一個「小節譜」分頁（格子、和弦圖、移調、Capo、每行幾小節、複製、列印、選照片把和弦標在照片上；照片只在頁面記憶體裡）。另有獨立樣板 `docs/guitar/chart_template.html`（不連網，換掉 `chart-data` 那段 JSON 就是一份新譜；`scripts/guitar_template.py` 從 panel.html 同步）。
  聊天指令會把換行併成空白（整份譜變一行），所以移法和譜在同一行也認得（`到 Eb {…}`、`G 到 A …`）；一行的譜改成「像和弦的字都移」並在回覆說明，要保留排版用面板。
- **`guitar` 0.1.0-alpha.1（新工具，`stage` alpha；**尚未實機驗證**，試過之後才發 0.1.0）**：把「吉他和弦全書」做成工具。`/chord`（查按法，一次可以好幾個）、`/chord_tp`（貼一份譜直接移調：`+2`、`-3`、`G→A`、`到 A`；和弦行與 `[C]` 夾在歌詞裡都認得，移調後和弦盡量留在原本那一欄；升降記號照新調寫；不貼譜＝用上次那份）、`/chord_capo`（夾幾格、用哪組手型最好彈）、`/chord_arp`（分散和弦 TAB）、`/chord_key`（一個調的和弦與常見進行）。網頁面板 `panel.html`（即時移調、capo 選擇、和弦圖、查和弦、分散和弦），資料走 `GET /api/packs/guitar/data`。只存最後一次貼的譜（保管箱、每人一份）；不連網、不碰檔案。`tests/test_guitar.py` 逐弦檢查每個按法的組成音；`catalog.json` 多一個建議群組「音樂」。
- **`google-sheets` 0.1.0-alpha.1（新工具，`stage` alpha；**尚未實機驗證**，試過之後才發 0.1.0）**：Google 試算表**只加列**（[ADR-0005](docs/adr/0005-google-sheets-append-only.md)）。`/gsheet_new`、`/gsheet_list`、`/gsheet_use`、`/gsheet_read`、`/gsheet`（預覽）、`/gsheet_void`（加一列作廢標記）、`/gsheet_ok`（本人打確認碼才寫，AI 不行）；裝置碼登入、權限只有 `drive.file`、`values.append` 用 RAW＋INSERT_ROWS、每列有 id 不會重複寫。只對假的 Google 測過（`tests/test_gsheet.py`），還沒在真帳號上試。共用的 Google 登入程式放 `packs/_shared/googleauth.py`（`scripts/sync_shared.py` 也管它）；`catalog.json` 的「接外面的服務」多這一個。
- **`money` 0.3.1 → 0.4.0-alpha.1**（**alpha，尚未實機驗證**：只在自動測試裡跑過，還沒在真的 SYNOX／Telegram 上試過；試過之後才發 0.4.0。記帳整個重做，[ADR-0004](docs/adr/0004-money-remake.md)；`stage` 仍是 alpha）：從一個本機使用多年的記帳程式通用化而來，
  去掉所有個人資料與寫死的設定。六個分頁（總覽、明細、規劃、設定、檢討、撲滿）、固定項目、每一格預算與滾存、信用卡與分期、負債、借貸、
  餘額核對、水電帳單、收據附檔、每週與每月檢討（由 SYNOX 的 AI 讀 `report` 後照手冊寫、`review_save` 存回）、第一次使用的三步引導、CSV 匯出匯入。
  撲滿（投資、倉、跟基準比）只在開了投資的帳本顯示；不含券商對帳單匯入；目前只支援整數幣別。
  幣別、小數位數、時區、起算日、類別與帳戶都可自訂。工具改成多個檔（`python.package`，要有支援多檔工具的 SYNOX），
  帳本是每人一個 SQLite 檔（**沒有加密**，README 照實寫；日後加密只動 `bridge/session.py`）。
  舊指令照樣能打；從 0.3.x 升級時第一次打開會把舊資料轉進新帳本（一批寫入、可撤銷，原本那份另存不刪）。升級報告會出現「執行方式改了」紅字。
- **`diary` 0.1.0-alpha.1（新工具，`stage` alpha；**尚未實機驗證**，試過之後才發 0.1.0）**：維護者自己的每日日記程式 1:1 搬成 SYNOX 工具：題組（原本 21 個＋新設計 20 個，
  其中 12 個內建但預設不出現）、分類與挑題組的規則、寫入的合併規則（AI 整理一律標明、附原文）、歷史版本、垃圾桶、整本備份、
  匯出匯入（格式名稱 `synox.diary.export`，其他跟原本的「匯出全部」一樣；原本程式匯出的舊檔照樣匯得進來）、四種版型的 HTML 與日記本卡片 SVG。資料在保管箱、伺服器版每人一份。
  `tests/test_diary_parity.py` 拿原本程式錄下的輸出逐字比對（`scripts/dump_diary_parity.py` 產生 `tests/fixtures/diary_parity.json`）。
  `tests/test_gates.py` 多一條 diary 的規矩（可以用的標準函式庫、只在三個地方開檔）；`catalog.json` 多一個建議群組「日記」。
- **入口頁改成產生的**：`scripts/build_site.py`（`build.py` 全部打包時也跑；`--check`、`--list-republish [--since <git 版本>]`）從每個工具的
  `pack.json`（＋選填的 `site` 區塊、`docs/site-tools.json`）產生 `docs/site/index.html`，加一個 `packs/<名字>/` 就多一張卡片、不用改 HTML；
  沒有手寫說明頁的工具產生一頁簡單的（標「說明頁還沒寫」），手寫的 9 頁不動。樣板分開給設計改：`scripts/site_index_template.html`、
  `scripts/site_tool_template.html`（版面沒變；衣櫃按鈕改連線上衣櫃頁）。線上頁網址登記在 `docs/artifacts.json`。
  `index.json` 每個工具多 `line`（卡片上的一句話）。測試 `tests/test_build_site.py`（假的新工具會出現在入口頁與 index.json、連結對）。
- 文件：`docs/README.md`（索引）、`docs/CONTRIBUTING-TOOLS.md`（新增工具從頭到尾）、`docs/ARTIFACTS.md`（線上頁）；`docs/RELEASING.md` 最上面加流程圖與檢查清單。
- **`money` 0.2.0 → 0.3.0**（[ADR-0003](docs/adr/0003-tool-apps-and-money-app.md)；`stage` 仍是 alpha）：附完整的網頁畫面「記帳本」（SYNOX 的工具服務：
  總覽、帳目、類別、預算；畫面只走 SYNOX 的共用元件）；新指令 `/money_edit`（改一筆）；匯出 `entries_csv`（個人版、下載前提醒沒有加密；
  多人共用的 SYNOX 沒有匯出；加密匯出還沒做）。`pack.py` 拆成模組層的核心函式（指令與畫面同一份邏輯），舊指令的回覆一個字都沒變。
  `requires.sdk` → `>=0.2.3,<0.3`（要有工具服務的 SYNOX）。從 0.2.0 升級時 SYNOX 的升級報告會出現紅字「新加了完整的網頁畫面」、每個動作、「畫面多了匯出」——這是預期的。
  資料格式不變（`schema` 2），退回 0.2.0 讀得懂。
  畫面宣告 `"remote": true`：可以從手機用（SYNOX 的手機入口，ADR 0026；要擁有者另外打開；手機上不能刪、不能匯出）。
- `scripts/build.py`：`index.json` 每筆多 `"app": true/false`（目錄頁標「有完整畫面」）。
- 測試：`tests/test_money_app.py`（同一件事兩條路結果一樣、欄位訊息一樣、讀、匯出、伺服器版隔離）、`tests/test_apps_common.py`
  （每個有畫面的官方工具：畫面程式不自己連線、入口先載 SYNOX 的共用元件、說明不印畫面的網址）。
- **SYNOX 衣櫃**（`wardrobe`）：品牌提案的 26 個系列（A–Z）做成 26 套外觀 `costume-<字母>-<名字>` 1.0.0（kind=`theme`）：
  淺色／深色 token、吉祥物 4 種狀態、開場動畫、字標、App 圖示，只有 SVG＋CSS、不帶程式、不附字型。
  `scripts/build_costumes.py`（extract／check／pack／page）、`tests/test_costumes.py`（不帶程式的規則、7 個 slot、token、大小、雜湊）、
  公開頁 `docs/site/wardrobe.html`（從 `docs/site/index.html` 與 README 連過去）。
  `scripts/build.py` 沒指定工具名時一起打包、列進 `index.json`（`kind: "theme"`、`category: "衣櫃／外觀"`、`collections.wardrobe`）。
- **下載連結永遠是最新版**（[docs/RELEASING.md](docs/RELEASING.md)）：`scripts/links.py` 是網址的唯一來源（版本只看 `pack.json`／`VERSION`；
  `LINK_MODE` = `tag`（現在，v0.2.0 是 pre-release）或 `latest`（標成 Latest 之後））。`scripts/build.py` 全部打包時 dist/ 多了
  不帶版本的別名 `<name>.synoxpack`、`tools-<VERSION>.zip`／`tools.zip`、`SHA256SUMS`，打完自動改寫 README 與 `docs/site/*.html` 的連結。
  衣櫃頁改連發佈頁（不再連 repo 分支裡的 `docs/site/packs/`，那份拿掉了）。`tests/test_links.py` 擋過期連結。
- [ADR-0006](docs/adr/0006-personal-and-server-mode.md)（2026-11-13，提議中）：個人版與伺服器（多人）版——九個工具的審查結果、
  工具的「多人契約」、`pack.json`／`install`／面板的入口標準、接外面服務的工具在伺服器版維持只給擁有者的政策、測試要求與實作待辦。
  只有文件，沒有改任何工具（待辦裡最優先的是 google-calendar 的密鑰會進 `[ROUTE]` log）。README 加上指向它的說明。

### Changed
- **多人契約也套到 `money` 0.4.0 與 `diary`**（ADR-0006 合進來時補的）：M3、M4 改成看工具裡每個 `.py`（money 是多檔工具），個別准的模組層級呼叫列在 `tests/test_gates.py` 的 `M4_EXTRA_CALLS`，每條寫理由、用不到了測試會擋；`diary` 的四個字元表改成 `frozenset`、設定的預設值改由 `pref_defaults()` 每次回新的（原本 `dict(PREF_DEFAULTS)` 會讓大家共用同一個 `palette` list）；`money` 拿掉 `if hasattr(pack, "web_route")`；`tests/test_access.py` 每打一個指令先清掉陌生人當天的配額（money、diary 的指令超過 30 個），只准本人打的指令（`ai=False`）算看得到；`tests/test_isolation.py` 的 money 照 0.4.0 的帳本位置檢查（沒有加密，不檢查密文）。
- **宣告補齊**（ADR-0006 T1，C4、C5；不改行為，SYNOX 讀到的值跟原本的預設值一樣）：`calendar`、`todo`、`bullet` 0.1.1 → **0.1.2**
  明寫 `"surfaces": ["dm"]`、`"data": "user"`；`notes` 0.1.1 → **0.1.2** 明寫 `"capabilities": []`（`money` 0.4.0 本來就寫了 `["filesystem:own"]`）。
- **`todoist` 0.1.1 → 0.1.2**（ADR-0006 T1、C3）：`auth.fields` 改成物件（`{name, ask, secret}`，跟 notion 一樣），
  `python -m synox connect todoist` 從此能用（存在 `key="auth"`）；程式先認 connect 存的、再認 `/todoist_token` 存的（舊的 token 照樣能用），
  `/todoist_token` 存新的時清掉 connect 那份（最後設的算數），`/todoist_logout` 兩份都清。
- `scripts/build.py`：`access`、`surfaces`、`data`、`storage`、`capabilities` 沒明寫就不打包（ADR-0006 入口標準）；
  `tests/test_gates.py` 加 `test_declares_scope`，`ALLOWED_CAPS` 不再把「沒寫」當期待值。
- 測試（ADR-0006 T2；不改工具）：`tests/test_isolation.py` 改用真的 SYNOX，本機五個工具（多了 notes、money）跑伺服器版
  兩個成員＋擁有者（S1 讀的指令、S2 落地檔、S3 面板）與個人版（S5）；新的 `tests/test_access.py` 九個工具的權限（S4：
  個人版陌生人、伺服器版訪客、成員對 `access: owner`、`policy.decide(via=AI)`）；notion、apple-reminders、google-calendar
  各加一條 token 每人一份（S7）；calendar 加匯出檔不留東西（S8）。`tests/conftest.py` 的假 pack 伺服器版 `path()`／`path_of(None)`
  跟真的一樣丟 `PackPathError`（P7）。
- `tests/test_gates.py` 加多人契約的靜態檢查（ADR-0006 T3；不改工具）：M3 `chat_id` 不准當 `store.of`／`path_of`／`secret` 的參數或 dict 的鍵；
  M4 模組層級不准有空容器、會被改的表、共用物件、`global`、拿常數表淺拷貝（`X = None` 的測試掛勾暫列 todoist `HTTP`、
  google-calendar `transport`，T5 拿掉）；M5 `panel.html` 不准 `innerHTML`、`localStorage`、外部網址，`fetch` 只打自己宣告的
  `/api/packs/<名字>/<route>`。每個檢查都有一條「故意寫錯一定抓得到」的測試。
- 小修（ADR-0006 T5；不改資料格式。這一輪還沒發佈，T1／T4 已經升過版的工具不再另外升）：
  `todoist`、`google-calendar` 模組層級的 `HTTP`／`transport` 改成 `def new_session()`（跟 notion、apple-reminders 一樣；
  測試改成換函式，P6），`tests/test_gates.py` 的暫時白名單拿掉；`calendar`、`todo`、`bullet`、`money` 拿掉
  `if hasattr(pack, "web_route")`（`requires.synox >=0.2` 一定有，C9）。
- 文件（ADR-0006 T6）：本機五個工具的 README 寫明伺服器版每人一份、伺服器的管理者解得開、改模式不會自己搬舊資料；
  外部四個的 README 寫明伺服器版只有擁有者用得到（ADR-0006 第 4 節）。ADR-0006 的 T1～T6 標成已完成。
  `notion`、`apple-reminders` 只改了 README（會打包進 .synoxpack），版本 0.1.1 → **0.1.2**。

## [0.2.0] - 2026-11-13

### Added
- **`money` 0.1.1 → 0.2.0**（2026-11-13，[ADR-0002](docs/adr/0002-money-general-version.md)；`stage` 仍是 alpha、`requires.sdk` 不變）：
  資料第 2 版（`schema: 2`，每筆有編號、種類、日期；0.1.x 的資料讀的時候自己轉）；收入 `/money_in` 與轉帳 `/money_transfer`
  （存錢、投資、還款、繳卡費不算「這個月花了」、不吃預算）；自己的類別 `/money_cats`、`/money_cat_add`、`/money_cat_rm`；
  選配範本 `/money_template`（基本、帳單、存錢與投資、捐贈、收入；預設不載、載兩次不重複）；沒給或打的不是類別名時用關鍵字猜類別；
  `/money_undo`、`/money_rm`；`/money_list` 篩選；`/money_cap` 單一類別每月上限；付款方式標籤 `account=`、補記 `date=`。
  退回 0.1.1 資料不會壞，但收入與轉帳會被當成花費。
- 工具庫成立（2026-11-12，ADR-0001）：從 SYNOX 搬來 `money`、`notes`、`remind`，從 SYNOXPacks 搬來 `calendar`、`todo`、`bullet`
  （來源對照 [docs/sources.md](docs/sources.md)）。
- 六個工具都宣告 `requires.synox: ">=0.2"`、`requires.sdk: ">=0.2,<0.3"`，版本 0.1.0 → **0.1.1**。
- `scripts/build.py`：打包成 `dist/<name>-<version>.synoxpack`＋`dist/index.json`（雜湊、SDK 範圍、建議群組）；`catalog.json` 建議群組。
- 測試：六個工具的第 1、2 關、SDK 範圍、打包、空的 SYNOX 一個一個裝。
- 從 SYNOX 主程式的 `connectors/` 搬來 `notion`、`todoist`、`google-calendar`、`apple-reminders`（2026-11-12，維護者決定），
  連同測試；宣告 `requires.synox`／`requires.sdk`，版本 0.1.0 → **0.1.1**，`publisher` 改成 `SYNOX_Libs`。
  `catalog.json` 新建議組合 `connect`（接外面的服務）。SYNOX 主程式從此一個工具都不帶。
- **穩定度 `stage`**：9 個工具都標 `"stage": "alpha"`（維護者決定：所有工具先標為測試版本）；`scripts/build.py` 要求每個工具明寫、
  `index.json` 帶 `stage`；README「穩定度」寫各級的意思與升級條件。（同一輪還沒發佈，所以 `version` 不再另外升。）
- `scripts/public_check.py`：公開版檢查（本機路徑、使用者名稱、email、token、私人資料檔、測試用的真實 id），測試每次都跑。
  掃到並修掉 3 處本機路徑（ADR-0001、`docs/mcp-audit-2026-11.md`）。

### Changed
- `scripts/sync_shared.py`：`zhdate` 也同步到 `google-calendar`、`todoist`。
- README：寫明工具庫是「工具的統一管理處」、SYNOX 主程式本身沒有內建工具；要含 `stage` 的 SYNOX（`feat/connectors-to-libs` 之後）。

### Removed
- `remind` 下架（2026-11-12，維護者決定）：提醒回到 SYNOX 核心當系統指令（「⏰ 提醒（系統）」，`/remind_*` 不變、資料不用搬）。
  檔案搬到 [legacy/remind/](legacy/README.md)（含原本的測試）；`catalog.json` 的 `starter` 改成 `notes`、`todo`。

### Fixed
- `calendar` 0.1.1：`/cal_ics` 的暫存檔改寫在使用者自己的資料夾（原本寫在全部人共用的 `data/packs/calendar/`）。
