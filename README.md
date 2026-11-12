# SYNOX_Libs：SYNOX 的工具庫

SYNOX（主程式）出廠是空的；要什麼工具，從這裡挑 `.synoxpack` 自己裝。設計見 [docs/adr/0001](docs/adr/0001-tool-library-and-sdk-dependency.md)。

| 工具 | 指令（例） | 做什麼 | 建議群組 |
|---|---|---|---|
| [📒 notes](packs/notes/README.md) | `/note 買牛奶`、`/note_list`、`/note_rm 1` | 隨手記一筆、找、刪 | 入門 |
| [✅ todo](packs/todo/README.md) | `/todo !! 繳電費 明天 #家事`、`/todo_list` | 待辦：重要度、專案、到期 | 入門、規劃 |
| [💰 money](packs/money/README.md) | `/money 120 午餐`、`/money_month`、`/money_budget 8000` | 記一筆、看這個月、設預算 | 記帳 |
| [📅 calendar](packs/calendar/README.md) | `/cal 明天 14:00 看牙醫`、`/cal_week`、`/cal_ics` | 行事曆、匯出 .ics | 規劃 |
| [• bullet](packs/bullet/README.md) | `/bj • 買牛奶`、`/bj_today`、`/bj_migrate` | 子彈筆記 | 規劃 |

五個都是：資料加密存在你自己的電腦（保管箱）、不連網、不開別的程式；伺服器版每個人一份。

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

## 給開發者

```
set SYNOX_HOME=<SYNOX 資料夾>             建議明寫（見下面「找 SYNOX 的順序」）
<SYNOX>\.venv\Scripts\python.exe -m pytest -q                測試（不連網、不開埠、不連 Telegram、不叫 AI）
<SYNOX>\.venv\Scripts\python.exe scripts\build.py            全部打包 → dist\*.synoxpack ＋ dist\index.json
<SYNOX>\.venv\Scripts\python.exe scripts\build.py --check    只檢查
<SYNOX>\.venv\Scripts\python.exe scripts\sync_shared.py      改了 packs\_shared\zhdate.py 之後跑（--check 只檢查）
```

（主控台是 cp950：先 `set PYTHONIOENCODING=utf-8` 與 `set PYTHONUTF8=1`。）

**找 SYNOX 的順序**（`scripts/_synox.py`，測試在 `tests/test_find_synox.py`）：
1. 環境變數 `SYNOX_HOME`＝SYNOX 資料夾（裡面有 `src\synox`）；設了卻不對就直接報錯，不往下猜。
2. 跑腳本的 Python 自己 import 得到 `synox`（用 `<SYNOX>\.venv\Scripts\python.exe` 跑，`pip install -e` 裝的那份）就用它。
3. 後備：跟這個 repo 並排的 `..\SYNOX`（只是維護者的方便，不要依賴）。
都找不到時會列出試過的位置和怎麼設。

**要哪一版的 SYNOX**：打包與測試用到 SYNOX 的 `requires.sdk` 檢查（`synox.packs.manifest.sdk_problem`、`SDK_VERSION` 0.2.0），
也就是 SYNOX 分支 `feat/libs-extraction`（2026-11-12）合併之後的版本；更舊的 SYNOX 會在 import 時失敗。

| 依賴 | 版本 | 為什麼 |
|---|---|---|
| SYNOX（主程式） | 含 `requires.sdk` 的版本（`feat/libs-extraction` 之後；SYNOX 本體版號仍是 0.2.0） | 打包用它的 `zip_pack`（安裝時的第 1、2 關）、測試用它的 `build()` |
| SYNOX SDK | `>=0.2,<0.3` | 每個工具的 `requires.sdk` |

- **每個工具一個資料夾** `packs/<name>/`：`pack.json`、`SKILL.md`（給 AI）、`README.md`（給人）、程式、（選用）`panel.html`。格式見 SYNOX 的 `docs/pack-format.md`。
- **一定要宣告 SDK 版本**：`"requires": {"synox": ">=0.2", "sdk": ">=0.2,<0.3"}`。SDK 的正本在 SYNOX（`synox_sdk.SDK_VERSION`）；
  打包時範圍不含拿來打包的那份 SYNOX 的 SDK 就不打包。kind=cli／mcp 的工具用 `synox-sdk init --from tool.py` 會自動放一份 `synox_sdk.py`
  並寫好範圍（或 `pip install <SYNOX>/sdk`）；附的 `synox_sdk.py` 版本也要在範圍內。
- **改了工具就升 `version`**（semver，要比裝著的大，SYNOX 才會當成升級），並在 [CHANGELOG.md](CHANGELOG.md) 記一行。
- 工具之間不能互相 import（SYNOX 的規則）；共用的程式放 `packs/_shared/`，用 `scripts/sync_shared.py` 原樣複製進每個用到的 `pack.py`。
- 工具不要取名 `remind`、不要用 `remind` 群組：那是 SYNOX 系統指令的名字（裝了也載不進去）。要「到時間提醒」，在回覆裡附一行 `/remind_add …` 讓使用者自己決定（`calendar` 的做法）。
- 測試兩層：`make_bot`（假 store，快）與 `synox_bot`（真的 SYNOX）；`tests/test_build.py` 走完整條「打包 → 空的 SYNOX 安裝」。

## 目錄

| 路徑 | 是什麼 |
|---|---|
| `packs/` | 每個工具一個資料夾；`_shared/` 是共用程式的正本（不是工具） |
| `legacy/` | 下架的工具（不打包、不測試；每項寫原本做什麼、為什麼下架、怎麼救回） |
| `scripts/build.py` | 打包＋寫 `index.json` |
| `scripts/_synox.py` | 找 SYNOX（`SYNOX_HOME` → 跑腳本的 Python 裝的那份 → 後備 `../SYNOX`） |
| `catalog.json` | 建議群組 |
| `docs/adr/` | 設計決定 |
| `docs/sources.md` | 每個檔從哪裡搬來 |
| `VERSION`、`CHANGELOG.md` | 工具庫本身的版本（每個工具的版本在自己的 `pack.json`） |

授權：AGPL-3.0-or-later（[LICENSE](LICENSE)，跟 SYNOX 核心一樣；這些工具原本就是 SYNOX 核心的一部分）。
