# Changelog

工具庫本身的版本在 `VERSION`；每個工具的版本在自己的 `pack.json`（下面每行標出來）。格式照 Keep a Changelog。

## [Unreleased]

### Fixed
- **`money` 0.3.0 → 0.3.1**（SYNOX 獨立安全審查第 1 輪）：金額欄位改用 SYNOX 的 `kit.amount`（大於 0、最多兩位小數、不超過十億；預算與上限另外收 0＝拿掉）；`nan`、`inf` 這類不是一般數字的值一律擋下（以前會讓記一筆出錯、讓預算存成 NaN 之後每筆都壞）。

### Added
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
