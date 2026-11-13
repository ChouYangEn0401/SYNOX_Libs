# SYNOX_Libs：SYNOX 的工具庫（工具的統一管理處）

**所有 SYNOX 工具都在這裡統一管理**：每個工具的正本、測試、打包、版本與穩定度都只在這個 repo。
**SYNOX 主程式本身沒有內建任何工具**（2026-11-12 起；連接 Notion／Todoist／Google 日曆／Apple 的也搬來了），
出廠是空的，要什麼工具從這裡挑 `.synoxpack` 自己裝。設計見 [docs/adr/0001](docs/adr/0001-tool-library-and-sdk-dependency.md)。

| 工具 | 指令（例） | 做什麼 | 穩定度 | 建議群組 |
|---|---|---|---|---|
| [📒 notes](packs/notes/README.md) | `/note 買牛奶`、`/note_list`、`/note_rm 1` | 隨手記一筆、找、刪 | 🧪 alpha | 入門 |
| [✅ todo](packs/todo/README.md) | `/todo !! 繳電費 明天 #家事`、`/todo_list` | 待辦：重要度、專案、到期 | 🧪 alpha | 入門、規劃 |
| [💰 money](packs/money/README.md) | `/money 120 午餐`、`/money_in 32000 薪資`、`/money_month` | 支出／收入／轉帳、自己的類別與範本、預算與上限 | 🧪 alpha | 記帳 |
| [📅 calendar](packs/calendar/README.md) | `/cal 明天 14:00 看牙醫`、`/cal_week`、`/cal_ics` | 行事曆、匯出 .ics | 🧪 alpha | 規劃 |
| [• bullet](packs/bullet/README.md) | `/bj • 買牛奶`、`/bj_today`、`/bj_migrate` | 子彈筆記 | 🧪 alpha | 規劃 |
| [🗂 notion](packs/notion/README.md) | `/notion 會議`、`/notion_read`、`/notion_row` | 用你自己的 integration token 搜尋、讀、加一列、改欄位 | 🧪 alpha | 接外面的服務 |
| [✅ todoist](packs/todoist/README.md) | `/todoist !! 繳電費 明天`、`/todoist_list`、`/todoist_done 3` | Todoist 的待辦：看、加、完成、改期 | 🧪 alpha | 接外面的服務 |
| [📅 google-calendar](packs/google-calendar/README.md) | `/gcal 明天 14:00 看牙醫`、`/gcal_today`、`/gcal_setup` | 直接看、加、刪你的 Google 日曆 | 🧪 alpha | 接外面的服務 |
| [🍎 apple-reminders](packs/apple-reminders/README.md) | `/applerem`、`/applerem_add`、`/applerem_status` | CalDAV 提醒事項（iCloud 升級過的 Apple 不開放，見說明） | 🧪 alpha | 接外面的服務 |

- 前五個：資料加密存在你自己的電腦（保管箱）、不連網、不開別的程式；伺服器版每個人一份。
- 後四個（2026-11-12 從 SYNOX 主程式的 `connectors/` 搬來）：只連自己那個服務的官方 API（只用 `requests`）、不開埠、
  token／帳號加密存在保管箱、只給擁有者（`access: owner`）；都只對假傳輸測過，還沒對真的服務長時間用過。
- 全部先標 **🧪 alpha**（維護者決定），意思與升級條件見下面「穩定度」。

**提醒（`/remind_add 08:00 吃藥`）不在這裡**：它是 SYNOX 的系統指令（「⏰ 提醒（系統）」），不用裝。
2026-11-12 以前這裡有一個 `remind` 工具，已下架到 [legacy/](legacy/README.md)。`apple-reminders`（Apple 提醒事項）是另一個東西：接你 iCloud 的待辦。

## 給使用者：怎麼裝

工具庫不能單獨用，要先有 SYNOX（主程式）。整條流程（SYNOX 的 `docs/adr/0003`）：

1. **裝 SYNOX**（二選一）：
   - 安裝程式 `SYNOX-Setup-<版本>.exe`：已經附好工具（`<SYNOX>\tools\`），直接跳到第 3 步；
   - 或 `git clone <SYNOX 的 repo 網址>`，雙擊 `Setup.bat`（找／裝 Python、建 `.venv`、裝套件、開設定精靈）。
2. **拿到工具**（git clone 的人才要）：下載發佈好的 `tools` 資料夾（一堆 `<名字>-<版本>.synoxpack`＋目錄 `index.json`），
   放進 SYNOX 資料夾、叫 `tools`（`<SYNOX>\tools\index.json`）。SYNOX 沒設定時就找這個位置（相對於 SYNOX 資料夾，不是絕對路徑）；
   放別處就在 init 時給位置，或寫進 `config.json` 的 `packs.library`。也可以自己打包：見下面「給開發者」的 `scripts\build.py`。
   （不要直接指到這個 repo 的原始碼資料夾：SYNOX 會說「這是工具庫的原始碼，不是打包好的」。）
3. **挑要裝的**：`<SYNOX>\.venv\Scripts\python.exe -m synox init` 會列出工具庫、問你要裝哪些（名字或建議組合的代號，
   不會自動全裝）；之後要再裝：`python -m synox tools list`、`python -m synox tools install <名字>`。
   每條路都跑 SYNOX 同一條安全檢查（宣告、靜態掃描、看完報告按確認、雜湊鎖起來），而且先對 `index.json` 的 sha256。
   其他裝法照舊：SYNOX 網頁介面「設定 → 裝功能」上傳、點兩下 `.synoxpack`（安裝精靈勾過「點兩下安裝」）、
   `python -m synox install <檔>.synoxpack`。
4. 裝好後 `/help` 就多一頁。不要了在網頁設定頁移除（舊的搬到 `packs/_removed/`，不刪；資料也不動）。

從舊版 SYNOX 更新上來、原本有內建記帳／筆記的人：資料還在，裝同名工具（`money`、`notes`）就回來；提醒不用裝（系統指令，資料一直都在）。
原本用內建 Notion／Todoist／Google 日曆／Apple 提醒事項的人：裝同名工具（`notion`、`todoist`、`google-calendar`、`apple-reminders`），
存好的 token／帳號照用，不用重設。

## 穩定度（`stage`）

每個工具的 `pack.json` 都寫 `"stage"`（這裡規定一定要寫，`scripts/build.py` 會擋；SYNOX 那邊沒寫的當 alpha）。
SYNOX 在 `/help` 那一頁的標題、網頁工具頁、安裝報告、`doctor` 都會顯示「符號＋字」的標籤；`index.json` 每個工具也帶 `stage`。

| stage | 標籤 | 意思（給使用者） | 升到下一級的條件（給維護者） |
|---|---|---|---|
| `test` | 🔧 test（測試中） | 只給開發者試：隨時會改、可能壞掉，不要拿來放重要的資料 | 第 1、2 關與這裡的測試都過；README 寫清楚怎麼設定、資料存哪；維護者自己把主要指令跑過一輪 → `alpha` |
| `alpha` | 🧪 alpha（早期版） | 能用，但還沒在真的服務上長時間用過；指令與資料格式之後可能會改 | 有人在**真的環境**連續用兩週以上（接外面服務的：用真帳號、SYNOX 的 `scripts/smoke_real.py` 全綠）；沒有弄丟資料的 bug；指令名定下來；資料裡有 `schema` 版本號 → `beta` |
| `beta` | 🚧 beta（公測版） | 功能齊了、有人實際用過一陣子；可能還有小問題，資料格式不會隨便改 | 實際用一個月以上、不只一個人（或伺服器版）用過；沒有開著的嚴重問題；資料格式改了會自己轉（升級路徑測過）；擁有者同意 → `stable` |
| `stable` | ⭐ stable（穩定版） | 長時間實際使用過、有測試；不相容的改動會升主版號並寫清楚怎麼搬 | — |

- 換 stage 也是改了工具：**一起升 `version`**、在 [CHANGELOG.md](CHANGELOG.md) 寫一行（為什麼可以升）。
- 往下調也可以：發現會弄丟資料或安全問題，先調回 `alpha`／`test`、升 `version`，修好再往上。
- `stage` 是作者的自我標示，**不是安全保證**；安全檢查照舊是 SYNOX 的五關。

## 給開發者

```
set SYNOX_HOME=<SYNOX 資料夾>             建議明寫（見下面「找 SYNOX 的順序」）
<SYNOX>\.venv\Scripts\python.exe -m pytest -q                測試（不連網、不開埠、不連 Telegram、不叫 AI）
<SYNOX>\.venv\Scripts\python.exe scripts\build.py            全部打包 → dist\*.synoxpack ＋ dist\index.json
<SYNOX>\.venv\Scripts\python.exe scripts\build.py --check    只檢查
<SYNOX>\.venv\Scripts\python.exe scripts\sync_shared.py      改了 packs\_shared\zhdate.py 之後跑（--check 只檢查）
<SYNOX>\.venv\Scripts\python.exe scripts\public_check.py     公開版檢查（--history 另外看 git 歷史與 commit 作者信箱）
```

（主控台是 cp950：先 `set PYTHONIOENCODING=utf-8` 與 `set PYTHONUTF8=1`。）

**找 SYNOX 的順序**（`scripts/_synox.py`，測試在 `tests/test_find_synox.py`）：
1. 環境變數 `SYNOX_HOME`＝SYNOX 資料夾（裡面有 `src\synox`）；設了卻不對就直接報錯，不往下猜。
2. 跑腳本的 Python 自己 import 得到 `synox`（用 `<SYNOX>\.venv\Scripts\python.exe` 跑，`pip install -e` 裝的那份）就用它。
3. 後備：跟這個 repo 並排的 `..\SYNOX`（只是維護者的方便，不要依賴）。
都找不到時會列出試過的位置和怎麼設。

**要哪一版的 SYNOX**：打包與測試用到 SYNOX 的 `requires.sdk` 檢查（`synox.packs.manifest.sdk_problem`、`SDK_VERSION` 0.2.0）
與 `stage` 檢查（`synox.packs.manifest.STAGES`），也就是 SYNOX 分支 `feat/connectors-to-libs`（2026-11-12）合併之後的版本；
更舊的 SYNOX：沒有 `requires.sdk` 的會在 import 時失敗，沒有 `stage` 的打包時說「SYNOX 太舊、不認得 stage」。
（工具的 `pack.json` 多寫 `stage`，舊 SYNOX 裝的時候會忽略它，不會壞。）

| 依賴 | 版本 | 為什麼 |
|---|---|---|
| SYNOX（主程式） | 含 `requires.sdk` 與 `stage` 的版本（`feat/connectors-to-libs` 之後；SYNOX 本體版號仍是 0.2.0） | 打包用它的 `zip_pack`（安裝時的第 1、2 關）、測試用它的 `build()` |
| SYNOX SDK | `>=0.2,<0.3` | 每個工具的 `requires.sdk` |

- **每個工具一個資料夾** `packs/<name>/`：`pack.json`、`SKILL.md`（給 AI）、`README.md`（給人）、程式、（選用）`panel.html`。格式見 SYNOX 的 `docs/pack-format.md`。
- **一定要宣告 SDK 版本**：`"requires": {"synox": ">=0.2", "sdk": ">=0.2,<0.3"}`。SDK 的正本在 SYNOX（`synox_sdk.SDK_VERSION`）；
  打包時範圍不含拿來打包的那份 SYNOX 的 SDK 就不打包。kind=cli／mcp 的工具用 `synox-sdk init --from tool.py` 會自動放一份 `synox_sdk.py`
  並寫好範圍（或 `pip install <SYNOX>/sdk`）；附的 `synox_sdk.py` 版本也要在範圍內。
- **一定要寫 `stage`**（`test`／`alpha`／`beta`／`stable`，見上面「穩定度」）；新工具從 `test` 或 `alpha` 開始。
- **改了工具就升 `version`**（semver，要比裝著的大，SYNOX 才會當成升級），並在 [CHANGELOG.md](CHANGELOG.md) 記一行。
- 工具之間不能互相 import（SYNOX 的規則）；共用的程式放 `packs/_shared/`，用 `scripts/sync_shared.py` 原樣複製進每個用到的 `pack.py`。
- 工具不要取名 `remind`、不要用 `remind` 群組：那是 SYNOX 系統指令的名字（裝了也載不進去）。要「到時間提醒」，在回覆裡附一行 `/remind_add …` 讓使用者自己決定（`calendar` 的做法）。
- 測試兩層：`make_bot`（假 store，快）與 `synox_bot`／`host_bot`（真的 SYNOX；`host_bot` 跟 SYNOX 自己測試的 make_bot 同一個介面，
  從 SYNOX 搬來的 connector 測試用它）；`tests/test_build.py` 走完整條「打包 → 空的 SYNOX 安裝」。接外面服務的工具一律用假的 HTTP 測。

## 公開版（給別人用）之前

擁有者的打算：這個工具庫之後做成 **public 版本**，去掉擁有者的隱私項目，提供給別人。所以從現在起就保持乾淨：

- `scripts/public_check.py`（只用標準函式庫、不連網、不執行 repo 裡的程式）掃 git 追蹤的＋沒被忽略的新檔：
  本機絕對路徑（`C:\Users\<名字>…`）、使用者名稱（這台的帳號名、git 的 user.name／user.email、
  `public_check.local.txt`——git 忽略、一行一個擁有者自己的名字或暱稱）、email、token／金鑰、私人資料檔
  （`.env`、`config.json`、`*.enc`、`data/`…）、測試用的真實 id。有發現＝退出碼 1，報告只寫位置與種類、不印內容。
- `tests/test_public_check.py` 每次跑測試都會檢查「目前的檔案是乾淨的」，所以不乾淨的東西進不來。
- `--history` 另外看 git 歷史與 commit 作者信箱：那些修不了（要改寫歷史或換信箱），由擁有者決定。
- 範例與假資料一律用佔位（`me@example.com`、`<使用者>`、`0123…`）；真的需要留的範例行加 `public-check: allow` 並寫為什麼。

## 目錄

| 路徑 | 是什麼 |
|---|---|
| `packs/` | 每個工具一個資料夾；`_shared/` 是共用程式的正本（不是工具） |
| `legacy/` | 下架的工具（不打包、不測試；每項寫原本做什麼、為什麼下架、怎麼救回） |
| `scripts/build.py` | 打包＋寫 `index.json`（每個工具要寫 `requires.sdk`、`stage`） |
| `scripts/public_check.py` | 公開版檢查（隱私、token、私人資料檔） |
| `scripts/sync_shared.py` | `packs/_shared/` 的共用程式複製進用到的工具（calendar、todo、google-calendar、todoist） |
| `scripts/_synox.py` | 找 SYNOX（`SYNOX_HOME` → 跑腳本的 Python 裝的那份 → 後備 `../SYNOX`） |
| `catalog.json` | 建議群組 |
| `docs/adr/` | 設計決定 |
| `docs/sources.md` | 每個檔從哪裡搬來 |
| `VERSION`、`CHANGELOG.md` | 工具庫本身的版本（每個工具的版本在自己的 `pack.json`） |

授權：AGPL-3.0-or-later（[LICENSE](LICENSE)，跟 SYNOX 核心一樣；這些工具原本就是 SYNOX 核心的一部分）。
