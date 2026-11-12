# legacy/：下架的工具

這裡的東西**不再打包、不再測試**（`scripts/build.py` 只看 `packs/`，pytest 只跑 `tests/`），留著是為了查歷史與救回。
每一項寫三件事：原本做什麼、為什麼下架、怎麼救回。

## remind（⏰ 提醒）— 2026-11-12 下架，回到 SYNOX 核心

| | |
|---|---|
| 檔案 | `remind/`（`pack.json` 0.1.1、`pack.py`、`SKILL.md`、`README.md`）＋ `remind/test_remind.py`（原本的 `tests/test_remind.py`） |
| 原本做什麼 | 一個「開關」：裝了它，SYNOX 才把 `/remind_add`、`/remind_list`、`/remind_rm` 掛到 `remind` 群組。真正的程式（存提醒、到點送出）一直在 SYNOX 的 `synox.schedule`，所以它靠 `requires.synox` 綁住 SYNOX 的版本，不算 SDK 的一部分 |
| 為什麼下架 | 維護者決定（提醒是系統功能；避免同一個名字兩個身分）：提醒本來就是系統功能（排程器在核心），做成可裝可卸的工具反而讓「remind」這個字有兩個身分。SYNOX 從 `feat/core-remind-soul` 起把 `/remind_*` 當成**系統指令**（跟 `/help`、`/schedule_*` 同一層，網頁工具頁歸在「系統」群組，標題「⏰ 提醒（系統）」），不用裝 |
| 資料 | 沒有搬也不用搬：提醒一直存在 SYNOX 的 `data/schedule.json.enc`（伺服器版 `data/users/<id>/schedule.json.enc`），跟這個工具裝不裝無關 |
| 已經裝了舊 remind 工具的人 | SYNOX 新版載入時看到它，會標「不需要了（提醒已經是系統指令）」、不載入；到網頁設定頁移除即可（移除只是搬到 `packs/_removed/`，資料不動） |
| 怎麼救回 | 只有在 SYNOX 退回 `feat/core-remind-soul` 之前的版本時才需要：`git mv legacy/remind packs/remind`、`git mv packs/remind/test_remind.py tests/test_remind.py`，`catalog.json` 的 `starter` 加回 `"remind"`，`tests/test_build.py` 的 `SMOKE`、`tests/test_gates.py` 的 `ALLOWED_CAPS` 加回 `remind`，再 `scripts/build.py` |

跟 Apple 提醒事項無關：`apple-reminders`（Apple 提醒事項，經 CalDAV 讀寫你 iCloud 的待辦）是另一個「接外面服務」的工具，不受影響。
