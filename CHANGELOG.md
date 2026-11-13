# Changelog

工具庫本身的版本在 `VERSION`；每個工具的版本在自己的 `pack.json`（下面每行標出來）。格式照 Keep a Changelog。

## [Unreleased]

### Added
- [ADR-0003](docs/adr/0003-personal-and-server-mode.md)（2026-11-13，提議中）：個人版與伺服器（多人）版——九個工具的審查結果、
  工具的「多人契約」、`pack.json`／`install`／面板的入口標準、接外面服務的工具在伺服器版維持只給擁有者的政策、測試要求與實作待辦。
  只有文件，沒有改任何工具（待辦裡最優先的是 google-calendar 的密鑰會進 `[ROUTE]` log）。README 加上指向它的說明。

### Changed
- **宣告補齊**（ADR-0003 T1，C4、C5；不改行為，SYNOX 讀到的值跟原本的預設值一樣）：`calendar`、`todo`、`bullet` 0.1.1 → **0.1.2**
  明寫 `"surfaces": ["dm"]`、`"data": "user"`；`notes` 0.1.1 → **0.1.2**、`money` 0.2.0 → **0.2.1** 明寫 `"capabilities": []`。
- **`todoist` 0.1.1 → 0.1.2**（ADR-0003 T1、C3）：`auth.fields` 改成物件（`{name, ask, secret}`，跟 notion 一樣），
  `python -m synox connect todoist` 從此能用（存在 `key="auth"`）；程式先認 connect 存的、再認 `/todoist_token` 存的（舊的 token 照樣能用），
  `/todoist_token` 存新的時清掉 connect 那份（最後設的算數），`/todoist_logout` 兩份都清。
- `scripts/build.py`：`access`、`surfaces`、`data`、`storage`、`capabilities` 沒明寫就不打包（ADR-0003 入口標準）；
  `tests/test_gates.py` 加 `test_declares_scope`，`ALLOWED_CAPS` 不再把「沒寫」當期待值。

- 測試（ADR-0003 T2；不改工具）：`tests/test_isolation.py` 改用真的 SYNOX，本機五個工具（多了 notes、money）跑伺服器版
  兩個成員＋擁有者（S1 讀的指令、S2 落地檔、S3 面板）與個人版（S5）；新的 `tests/test_access.py` 九個工具的權限（S4：
  個人版陌生人、伺服器版訪客、成員對 `access: owner`、`policy.decide(via=AI)`）；notion、apple-reminders、google-calendar
  各加一條 token 每人一份（S7）；calendar 加匯出檔不留東西（S8）。`tests/conftest.py` 的假 pack 伺服器版 `path()`／`path_of(None)`
  跟真的一樣丟 `PackPathError`（P7）。

### Fixed
- **`google-calendar` 0.1.1 → 0.1.2**（ADR-0003 C1、P5）：`/gcal_setup`、`/gcal_code`、`/gcal_token` 打的 client secret、授權碼、
  refresh token 原本整串印在 `[ROUTE]` log（兩種模式都中）；`install` 改成對這三個叫 `kit.redact_in_logs`。
  `/gcal_status` 改成 `ai=False`（跟另外三個外部工具一樣，SKILL 改成請使用者自己打）。測試加一條 S6（log 裡沒有那些值）。

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
