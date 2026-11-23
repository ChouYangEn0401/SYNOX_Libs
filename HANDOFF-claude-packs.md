# 交接單：claude-resetter／claude-relay（本機打包與實機驗證）

> **用完就刪**：這份只放在 `handoff/claude-packs-build` 這個分支，是一個獨立的 commit，不影響開發分支。
> 刪法在最後一節。

## 1. 現在做到哪

| repo | 分支 | 內容 | 狀態 |
|---|---|---|---|
| SYNOX_Libs | `claude/elegant-pascal-yf0z7j` | 共用橋接層 `packs/_shared/resetterbridge.py`；工具 **`claude-resetter`**（公開）；工具 **`claude-relay`**（`publish: false`） | 都已 `--no-ff` 合進這個分支並推送；全部測試過（見第 5 節）；**沒有**合進 `main`、沒打包、沒簽章、沒改 `VERSION` |
| ClaudeTimerResetter | `claude/elegant-pascal-yf0z7j` | MIT `LICENSE`、重整後的 README（三個功能）、`docs/integration.md`、`SECURITY.md`、`tests/test_json_contract.py` | 已 `--no-ff` 合進並推送；沒動程式（`claude_timer/` 零改動）、沒升版號 |
| SYNOX、ClawBot | — | — | 唯讀，沒動 |

兩個工具版本都是 `0.1.0-alpha.1`、`stage: test`：**沒有在真的 Windows＋真的 resetter＋真的 claude 上跑過**，所有測試用假的 resetter。

## 2. 兩個工具各是什麼

| 工具 | 群組 | 做什麼 | 風險 |
|---|---|---|---|
| `claude-resetter` | `/resetter_*` | `usage`（看額度，不花錢）、`refresh`（該刷新才打一次探針，約 $0.00025；預設不等待）、`check`、`setup` | 低。能力只宣告 `subprocess`；工具自己不讀 `~/.claude`、不連網 |
| `claude-relay` | `/relay_*` | 對**事先授權**的對話自動傳固定的 `continue`：`arm`（本人＋手機驗證器）、`continue`（本人／排程，可帶 `at=`）、`freeze`（本人／AI）、`thaw`／`setup`（驗證器）、唯讀的 `sessions`／`show`／`check`／`jobs`／`job`／`armed` | 中。**第一版刻意沒有**：任意文字的 resume、開新對話（task_opener）、搶佔、自動辨認被額度停住的對話 |

設計理由、威脅模型、延後的事：`docs/adr/0010-claude-relay-continue-only.md`；各工具 `packs/<名>/README.md`。
resetter 專案的三個功能（resetter／resumer／task_opener）定義：ClaudeTimerResetter 的 `README.md`；給接入方的合約：`docs/integration.md`。

## 3. 本機打包與安裝（Windows，PowerShell）

前提：SYNOX 的 clone 與它的 `.venv`（`SYNOX_Libs` 的 `AGENTS.md` 第一段）。

```powershell
git fetch origin
git switch claude/elegant-pascal-yf0z7j          # 或這個交接分支（它就是開發分支多一個交接 commit）
$env:SYNOX_HOME = "<SYNOX 的 clone>"
$env:SYNOX_SRC = "$env:SYNOX_HOME\src"; $env:PYTHONUTF8 = "1"
$PY = "$env:SYNOX_HOME\.venv\Scripts\python.exe"
& $PY -m pytest -q                               # 預期見第 5 節
& $PY scripts\sync_shared.py --check
& $PY scripts\build.py --check
```

打包（`build.py <名字>` 只打指名的；**自己裝用**，不是發版流程）：

```powershell
& $PY scripts\build.py claude-resetter           # → dist\claude-resetter-0.1.0-alpha.1.synoxpack
& $PY scripts\build.py claude-relay              # → dist\private\claude-relay-0.1.0-alpha.1.synoxpack（私人工具預設寫到 dist\private\）
```

安裝：照 SYNOX 的 `docs/install-packs.md`（點兩下、拖進網頁、或 `python -m synox install <檔>`）。
安裝報告會列出能力：resetter 只有 `subprocess`；relay 是 `subprocess`＋`filesystem:own`。看過再按確認。

兩個工具都要在 SYNOX 裡先設定 resetter 在哪：

- `/resetter_setup home=<ClaudeTimerResetter 的 clone 資料夾>`（資料夾裡要有 `claude_timer\`、並跑過一次 `scripts\setup.bat` 建 `.venv`；**只有 exe 版的不能用**）
- `/relay_setup home=<同一個資料夾> [budget=2]`（要先有手機驗證器：`python -m synox stepup setup`）

## 4. 實機驗證清單（都還沒做過）

用一個**拋棄式**的資料夾與對話做，不要拿重要專案。`relay` 會真的叫 `claude -p`（每筆有 `--max-budget-usd` 上限，預設 2 美元）。

**claude-resetter**
- [ ] `/resetter_check`、`/resetter_usage`：讀得到額度與重置時間
- [ ] `/resetter_refresh`：視窗已開時回「不用打」；視窗沒開時真的打一次（約 $0.00025）
- [ ] `/schedule_add <時間> /resetter_refresh` 到點有跑；`force=是` 只有本人能下（排程帶 force 會到點才被擋）
- [ ] 連續兩次 `refresh` 不會重複打

**claude-relay**
- [ ] 沒設驗證器時 `arm` 被擋、訊息看得懂
- [ ] `/relay_sessions`：標出哪些已授權；程式開的／暫存的對話 `arm` 被拒
- [ ] `/relay_arm <對話>`：打驗證碼後授權；回覆有「權限可能退回 acceptEdits」的警告
- [ ] `/relay_continue <對話>`：對話關著時送出、`/relay_job` 看得到結果；對話被 VS Code 開著時回「正被開著」、**不扣次數**、不搶
- [ ] `/relay_continue <對話> at=+3`：Windows 工作排程器有一個 `ClaudeResumer-<id>` 的一次性工作，到點送出
- [ ] `/schedule_add <時間> /relay_continue <對話>` 到點有送（AI 自己叫 `/relay_continue`、`/relay_arm` 應被擋）
- [ ] `/relay_freeze` 之後 `continue` 被拒，且已排好的工作被取消（到工作排程器確認）；`/relay_thaw` 要驗證碼
- [ ] `/relay_disarm` 之後同上
- [ ] 把授權時間設很短（`hours=1`）再放過期：已排好的 `at=` 工作，在你下一次打任何 `/relay_…` 時被掃掉

## 5. 測試現況（給你比對）

SYNOX_Libs 全部測試：**2225 passed、12 skipped、1 failed**。

- 唯一的失敗 `tests/test_public_check.py::test_this_repo_is_clean`：**在開工前就有**。這台雲端容器的帳號叫 `user`，會撞到文件裡的 user 字樣（約 150 項→389 項都是同一類「使用者名稱」）。我確認過沒有新增信箱、本機路徑、token 這類項目。在你自己的電腦上跑應該只會報你的帳號名稱，請看有沒有**其他種類**。
- `tests/test_video_notes.py` 有 1～2 項**會隨機失敗**（在沒動過的分支上連跑 3 次也看到），跟這次的改動無關；這次最後一輪剛好全過。
- 對現有測試的改動（請你過目，專案規矩是不為了變綠改期望）：
  - `tests/test_synox_integration.py`：兩條「全部工具一起載入」的檢查，從比 `PACK_NAMES` 改成比 `ALL_PACK_NAMES`（連私人工具也要載入、群組也不能撞名）。**是變嚴、不是放寬**；不改的話只要有任何私人工具就必然失敗。
  - `tests/test_gates.py`：新增 `PRIVATE_CAPS` 與三個測試（私人工具也要登記、過 SYNOX 第 1、2 關、只經 `app.jobs` 開程式）；`LOCAL_PROGRAM` 的「會開檔的函式名單」從 video-notes 寫死改成每個工具一份（video-notes 的期望值沒變）。
  - `tests/test_access.py`、`tests/test_build.py`：各登記 claude-resetter；`catalog.json` 新增 bundle `claude-quota`（`test_build` 規定每個工具都要在某個 bundle）。
- 新增：`tests/test_resetterbridge.py`（7）、`tests/test_claude_resetter.py`（27）、`tests/test_claude_relay.py`（68，含用 resetter 真的 `argparse` 解析 argv 的一支；找不到 resetter 會 skip，可用環境變數 `CLAUDE_TIMER_RESETTER_HOME` 指定）。

claude-relay 另外做過一次**獨立的資安審查**（審查者只拿到程式碼與「宣稱的保證」），提了 6 項，全部已修並有測試：凍結與送出中的競態、授權過期後撤不掉的排程、`arm` 收了程式開的對話、AI 拿得到對話內容、次數退還太寬、凍結原因帶到錯誤原文。

## 6. 已知限制、需要你決定的事

1. **`at=` 不保證準時**：resetter 的一次性工作設了 `StartWhenAvailable` 且 `fire` 不檢查遲到。電腦關機錯過的話，開機後才送；若那段時間你沒打任何 `/relay_…`，它可能在授權過期後才送。relay 只能在「下一次有指令進來」時掃掉。真正的解法在 resetter：加 `--not-after`／任務 XML 的 `EndBoundary`（**這次沒改 resetter 程式**）。
2. **權限模式從外面看不出來**：resetter 的 `inherit` 讀不到互動紀錄時退回 `acceptEdits`（會放行檔案編輯），`bypassPermissions` 一律降成 `auto`。relay 只能在每次授權時警告。
3. **continue 不是無害**：對話帶著原本的工具權限繼續做事。只授權你信任的對話。
4. **被佔用時不補試**：現在送（不帶 `at=`）遇到對話被開著，resetter 不會自己補試（補試只存在於帶 `--at` 的工作）。relay 回「請關掉對話再叫，或改用 `at=+5`」，且不扣次數。
5. **失敗偵測是惰性的**：沒有背景執行緒，失敗只在 `continue`／`jobs`／`job` 被叫時才發現，所以自動凍結可能晚一點。
6. **公開可見**：`publish: false` 只讓它**不進打包目錄與公開頁**，**不會讓程式碼變機密**。`claude-relay` 的原始碼在這個公開 repo 的開發分支裡看得到。若不想公開原始碼：不要把它合進 `main`、改放在不推送的 `local/…` 分支。
7. **resetter 專案裡我只回報、沒改的程式側不一致**：`cli.py` 檔頭 docstring 還寫 resume 預設要乾淨工作目錄（v1.5 起不是）；`--notify` 的 help 只提 Notifier_TB（實際先打 ClawBot）；`require_clean` 已是殘留欄位；`--permission-mode` 沒有 argparse `choices`；`ping.py` 與 `requirements.txt`、README 的 wheel 網址指向某個個人 GitHub 帳號；CLI 只能在 Windows 完整運作（`schtasks`、`taskkill`）。
8. **`SECURITY.md`** 寫「用 GitHub 的 private vulnerability reporting」：要先在 resetter repo 的設定裡開啟，否則文件裡有寫的退路是開 issue 但不貼細節。
9. **`task_opener` 沒有獨立入口**，只是 resumer 命令列的 `new` 子指令；之後要由橋單獨管它，需要在 resetter 加旗標或子指令。
10. **高風險層（帶文字的 resume、`new`）與「自動辨認被額度停住的對話」都沒做**，設計草案與要先想清楚的安全問題在 ADR-0010。
11. 兩個 SYNOX 工具的檢查都建議宣告 `data_schema`，其他工具也都沒宣告，這次沒加。

## 7. 發版（你的事）

我沒碰：合併進 `main`、`VERSION`、`dist/`、簽章（`sign_dist.py`）、公開頁。照 `docs/HANDOFF.md` 的流程由你決定。

## 8. 用完怎麼清掉

```bash
git push origin --delete handoff/claude-packs-build
git branch -D handoff/claude-packs-build         # 本機
```

這份檔案只存在這個分支，刪掉分支就沒了；開發分支 `claude/elegant-pascal-yf0z7j` 不受影響。
