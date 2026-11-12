# SYNOX_Libs：SYNOX 的工具庫

SYNOX（主程式）出廠是空的；要什麼工具，從這裡挑 `.synoxpack` 自己裝。設計見 [docs/adr/0001](docs/adr/0001-tool-library-and-sdk-dependency.md)。

| 工具 | 指令（例） | 做什麼 | 建議群組 |
|---|---|---|---|
| [📒 notes](packs/notes/README.md) | `/note 買牛奶`、`/note_list`、`/note_rm 1` | 隨手記一筆、找、刪 | 入門 |
| [⏰ remind](packs/remind/README.md) | `/remind_add 08:00 吃藥`、`/remind_list` | 每天幾點提醒、或某一天提醒一次 | 入門 |
| [✅ todo](packs/todo/README.md) | `/todo !! 繳電費 明天 #家事`、`/todo_list` | 待辦：重要度、專案、到期 | 入門、規劃 |
| [💰 money](packs/money/README.md) | `/money 120 午餐`、`/money_month`、`/money_budget 8000` | 記一筆、看這個月、設預算 | 記帳 |
| [📅 calendar](packs/calendar/README.md) | `/cal 明天 14:00 看牙醫`、`/cal_week`、`/cal_ics` | 行事曆、匯出 .ics | 規劃 |
| [• bullet](packs/bullet/README.md) | `/bj • 買牛奶`、`/bj_today`、`/bj_migrate` | 子彈筆記 | 規劃 |

六個都是：資料加密存在你自己的電腦（保管箱）、不連網、不開別的程式；伺服器版每個人一份。

## 給使用者：怎麼裝

1. 拿到 `dist/` 資料夾（一堆 `<名字>-<版本>.synoxpack` ＋ `index.json` 目錄）。`index.json` 的 `bundles` 是建議組合（入門／記帳／規劃），只是建議。
2. 挑一個裝，三條路都跑 SYNOX 同一條安全檢查（宣告、靜態掃描，看完報告按確認，雜湊鎖起來）：
   - SYNOX 網頁介面 →「設定 → 裝功能」→ 上傳；
   - 點兩下 `.synoxpack`（安裝精靈勾過「點兩下安裝」）；
   - 終端機：`<SYNOX>\.venv\Scripts\python.exe -m synox install <檔>.synoxpack`。
3. 裝好後 `/help` 就多一頁。不要了在網頁設定頁移除（舊的搬到 `packs/_removed/`，不刪；資料也不動）。

從舊版 SYNOX 更新上來、原本有內建記帳／筆記／提醒的人：資料還在，裝同名工具（`money`、`notes`、`remind`）就回來。

## 給開發者

```
set SYNOX_HOME=<SYNOX 資料夾>             沒設就找並排的 ..\SYNOX
<SYNOX>\.venv\Scripts\python.exe -m pytest -q                測試（不連網、不開埠、不連 Telegram、不叫 AI）
<SYNOX>\.venv\Scripts\python.exe scripts\build.py            全部打包 → dist\*.synoxpack ＋ dist\index.json
<SYNOX>\.venv\Scripts\python.exe scripts\build.py --check    只檢查
<SYNOX>\.venv\Scripts\python.exe scripts\sync_shared.py      改了 packs\_shared\zhdate.py 之後跑（--check 只檢查）
```

（主控台是 cp950：先 `set PYTHONIOENCODING=utf-8` 與 `set PYTHONUTF8=1`。）

- **每個工具一個資料夾** `packs/<name>/`：`pack.json`、`SKILL.md`（給 AI）、`README.md`（給人）、程式、（選用）`panel.html`。格式見 SYNOX 的 `docs/pack-format.md`。
- **一定要宣告 SDK 版本**：`"requires": {"synox": ">=0.2", "sdk": ">=0.2,<0.3"}`。SDK 的正本在 SYNOX（`synox_sdk.SDK_VERSION`）；
  打包時範圍不含拿來打包的那份 SYNOX 的 SDK 就不打包。kind=cli／mcp 的工具用 `synox-sdk init --from tool.py` 會自動放一份 `synox_sdk.py`
  並寫好範圍（或 `pip install <SYNOX>/sdk`）；附的 `synox_sdk.py` 版本也要在範圍內。
- **改了工具就升 `version`**（semver，要比裝著的大，SYNOX 才會當成升級），並在 [CHANGELOG.md](CHANGELOG.md) 記一行。
- 工具之間不能互相 import（SYNOX 的規則）；共用的程式放 `packs/_shared/`，用 `scripts/sync_shared.py` 原樣複製進每個用到的 `pack.py`。
- `remind` 的程式本體在 SYNOX 的 `synox.schedule`（不是 SDK 的一部分），所以它靠 `requires.synox` 釘住 SYNOX 版本。
- 測試兩層：`make_bot`（假 store，快）與 `synox_bot`（真的 SYNOX）；`tests/test_build.py` 走完整條「打包 → 空的 SYNOX 安裝」。

## 目錄

| 路徑 | 是什麼 |
|---|---|
| `packs/` | 每個工具一個資料夾；`_shared/` 是共用程式的正本（不是工具） |
| `scripts/build.py` | 打包＋寫 `index.json` |
| `scripts/_synox.py` | 找 SYNOX（`SYNOX_HOME` 或 `../SYNOX`） |
| `catalog.json` | 建議群組 |
| `docs/adr/` | 設計決定 |
| `docs/sources.md` | 每個檔從哪裡搬來 |
| `VERSION`、`CHANGELOG.md` | 工具庫本身的版本（每個工具的版本在自己的 `pack.json`） |

授權：AGPL-3.0-or-later（[LICENSE](LICENSE)，跟 SYNOX 核心一樣；這些工具原本就是 SYNOX 核心的一部分）。
