# 來源對照：這裡的每個檔從哪裡來

2026-11-12 建立工具庫時，從兩個地方搬進來（決定見 [adr/0001](adr/0001-tool-library-and-sdk-dependency.md)）；同一天又從 SYNOX 搬來 4 個 connector。
**兩個來源都只讀**：SYNOXPacks 沒改、沒刪；SYNOX 那邊把三個內建工具與 4 個 connector 下架，原始碼留在它的 git 歷史，說明在 SYNOX 的 `legacy/`。

| 這裡 | 來源 | 來源版本 | 搬過來之後改了什麼 |
|---|---|---|---|
| `packs/money/` | SYNOX `packs/money/` | SYNOX 0.2.0，commit `ec01b67`（2026-11-12） | `pack.json`：`version` 0.1.0 → 0.1.1、加 `requires.synox`／`requires.sdk` |
| `packs/notes/` | SYNOX `packs/notes/` | 同上 | 同上 |
| `legacy/remind/`（原本 `packs/remind/`） | SYNOX `packs/remind/` | 同上 | 同上；程式本體仍在 SYNOX 的 `synox.schedule`（`requires.synox` 釘住）。2026-11-12 下架，提醒回到 SYNOX 核心（[../legacy/README.md](../legacy/README.md)） |
| `packs/calendar/`、`packs/todo/`、`packs/bullet/` | SYNOXPacks `packs/…` | SYNOXPacks commit `18bc2dc`（2026-11-09） | `pack.json`：`version` 0.1.0 → 0.1.1、加 `requires.synox`／`requires.sdk`；calendar 的 `/cal_ics` 暫存檔改寫在使用者自己的資料夾（`pack.path_of`，SYNOX 第 2 關的資料歸屬規則） |
| `packs/_shared/zhdate.py`、`scripts/sync_shared.py` | SYNOXPacks | 同上 | 沒改 |
| `docs/mcp-audit-2026-11.md` | SYNOXPacks `docs/` | 同上 | 沒改 |
| `tests/test_calendar.py`、`test_todo.py`、`test_bullet.py`、`test_zhdate.py`、`test_isolation.py`、`test_gates.py`、`test_mcp_examples.py`、`test_synox_integration.py`、`conftest.py` | SYNOXPacks `tests/` | 同上 | 改成六個工具一起測；`conftest.py` 改用 `scripts/_synox.py` 找 SYNOX |
| `tests/test_money.py`、`test_notes.py`、`test_remind.py`（後者 2026-11-12 搬到 `legacy/remind/`） | SYNOX `tests/`（跟這三個工具有關的測試） | SYNOX commit `ec01b67` | 從 SYNOX 的測試裡挑出來，改成用這裡的 `packs/` |
| `LICENSE` | SYNOX `LICENSE`（AGPL-3.0-or-later） | 同上 | 沒改 |
| `packs/notion/`、`packs/todoist/`、`packs/google-calendar/` | SYNOX `connectors/<同名>/` | SYNOX dev commit `65e61f2`（2026-11-12） | `pack.json`：`version` 0.1.0 → 0.1.1、加 `requires.synox`／`requires.sdk`、`publisher` → `SYNOX_Libs`；README／pack.py 裡指到 `connectors/…`、SYNOXPacks 的字改指這裡；todoist 的 `zhdate` 開頭註解跟正本同步（`scripts/sync_shared.py` 現在也管這兩個） |
| `packs/apple-reminders/` | SYNOX `connectors/apple-reminders-caldav/` | 同上 | 同上；資料夾名改成跟 pack 名字一樣（`apple-reminders`，pack 名字沒變，保管箱的資料照用） |
| `tests/test_notion.py`、`test_todoist.py`、`test_gcal.py`、`test_apple_reminders.py` | SYNOX `tests/` | 同上 | 測試本體沒改；資料夾位置改成 `packs/<name>`、檔內 `make_bot` 指向 `conftest.host_bot`；假帳號 `me@icloud.com` → `me@example.com` |

之後全部 9 個工具的 `pack.json` 都加了 `"stage": "alpha"`（README「穩定度」）。

## 以後誰是正本

- 這裡每個工具的正本**都在這裡**；SYNOX 主程式本身不帶工具（SYNOX 的 `legacy/builtin-packs.md`、`legacy/connectors.md`）。SYNOXPacks 停在 `18bc2dc`，不再更新（要不要封存由擁有者決定）。
- SDK 的正本在 SYNOX（`src/synox/sdk/author.py`，單檔版 `sdk/synox_sdk.py`）。這裡不放副本。
