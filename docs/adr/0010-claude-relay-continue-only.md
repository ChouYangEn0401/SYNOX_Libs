# ADR-0010：對話接力（claude-relay）——第一版只送固定的 continue

- 狀態：**已採用（低風險層）**（2026-11-23；`packs/claude-relay` 0.1.0-alpha.1，`stage: test`、`publish: false`，尚未實機驗證）。
  高風險層（任意文字、開新對話）**沒有做**，只在第 6 節留設計草案。
- 範圍：新的私人工具 `packs/claude-relay`；共用的 `packs/_shared/resetterbridge.py`（與 `claude-resetter` 共用）。不改 SYNOX、不改 ClaudeTimerResetter。
- 標籤：**已查證**＝讀過程式或跑過測試確認的；**推測**＝沒有在真的機器上驗過、照文件與程式推的。

## 1. 背景

Claude Code 的對話因為額度（session limit）停住時，會告訴你大概幾點重置。ClaudeTimerResetter 的 resumer
（它的 `docs/resumer.md`、`docs/cli.md`）做的就是「到點把那個對話接著送出去」：`python -m claude_timer.cli resume <對話> --prompt … [--at 時間]`。
它管「跑起來能碰到什麼」（內容釘死、被佔用只等不搶、接手前存快照、`bypassPermissions` 不沿用），**不管誰能下令**——那是呼叫端的事。

這個工具就是那個呼叫端：讓 SYNOX 的使用者在手機上授權、排時間，或讓 SYNOX 的排程到點叫它。
同一位擁有者的 Telegram 專案做過更完整的版本（背景派工：白名單、R1–R4 風險分級、兩組驗證碼、凍結、稽核）；這裡參考它的概念，不搬它的程式。

## 2. 決定：分兩層，第一版只做低風險層

| 層 | 送什麼 | 風險 | 這一版 |
|---|---|---|---|
| **低風險層** | 固定的 `continue`，權限照對話原本的（`inherit`） | 對話照它停住前的計畫接著做；沒有新的指示可以被塞進去 | **做** |
| 高風險層 | `resume` 帶任意文字、`new` 開新對話 | 任意文字＝prompt injection 的入口：誰能把字送進一個有工具權限的對話，誰就拿到那些權限 | **不做**（第 6 節） |

擁有者在猶豫之後選了保守版：先只做低風險層，高風險層等有更多保護再說。**不要偷偷加。**

## 3. 威脅模型

| 威脅 | 怎麼擋（結構，不是檢查文字） | 標籤 |
|---|---|---|
| 有人（含 AI 被注入）讓對話做新的事 | 送出去的字是常數 `PROMPT = "continue"`；整個工具沒有任何指令有自由文字的參數會送給 resetter | 已查證（測試鎖住 `relay_argv` 的輸出） |
| 改權限模式、搶佔、分支接續、開新對話、收起改動 | resetter 的 argv **只由 `relay_argv()` 組**：只有 sessions／show／check／resume／jobs／job／cancel 七種，resume 那一種的旗標寫死；`module_argv` 只在 `call()` 裡被叫一次。測試用 AST 斷言這件事，並斷言整份 pack.py 的字串常數裡沒有 `--take-over`、`--fork`、`--allowed-tools`、`bypassPermissions`、`release`、`new`… | 已查證 |
| 參數被當成旗標 | 對話 id 只收小寫 UUID（`fullmatch`），工作編號只收 8 個小寫 hex，都放在 `--` 後面；其他值一律 `--旗標=值` 一段；時間只收三種嚴格格式，送給 resetter 前換成絕對的 `YYYY-MM-DD HH:MM` | 已查證（`--` 在 Python 3.11／3.12／3.13 的 argparse 子指令都照預期，試過 resetter 的 `_parser()`） |
| AI 被注入後自己去續跑 | `continue`、`arm` 的 `callers` 不含 `ai`（AI 的工具清單裡看不到）、`ai=False` | 已查證 |
| 手機遠端入口被拿去用 | `callers` 不含 `remote` | 已查證 |
| 有人拿到你的 Telegram 就授權對話 | `arm` 要 `stepup`（手機驗證器、只有本人）；沒設定驗證器的人根本不能用 | 已查證 |
| 授權之後被一直續跑、花太多 | 授權有期限（1–48 小時）與次數（1–10）；全域每 24 小時最多 10 次；每次帶 `--max-budget-usd`；連續 2 次失敗自動凍結 | 已查證 |
| 出事時停不下來 | `freeze`：本人或 AI 都能叫、不用碼，取消這個工具排的、還沒跑完的工作；`thaw` 要本人＋驗證器 | 已查證 |
| 事後查不到 | 稽核：時間、動作、來源、對話前 8 碼、結果；最近 200 筆；不記驗證碼、不記對話內容 | 已查證 |
| `continue` 本身的風險 | **擋不住、只能收斂**：對話會帶著原本的工具權限接著做停住前在做的事。靠「本人＋驗證器授權」、期限、次數、預算、凍結收斂 | 誠實寫在 README |
| 用 `at=` 排的工作在 SYNOX 外面跑，而且可能遲到 | resetter 在 Windows 工作排程器註冊一次性的工作，SYNOX 關著也會送；被佔用時補試最多 1 小時；工作設了 `StartWhenAvailable`、`fire` 不檢查遲到，**電腦關機錯過的會在下次開機才送**，可能在授權過期之後。relay 只能在**下一個 `/relay_…` 指令進來時掃一次**（sweep）：授權過期、撤回或換掉、還沒送的就問過 resetter 後取消；`disarm` 對授權已經被清掉的對話也照樣取消。開機前沒有任何指令進來就擋不住 | 已查證（讀 resetter 的 `scheduler.py` 的 XML 與 `cli.fire`）；擋不住的那段是推測，要實機看 |
| 送的那幾秒裡被凍結或撤回 | 記下工作的那一刻（同一把鎖）再看一次凍結與授權（同一個 auth id、沒過期），不成立就當場取消、退次數 | 已查證（並行測試） |

## 4. 每個指令的 callers／stepup

| 指令 | callers | stepup | effect | 理由 |
|---|---|---|---|---|
| `sessions`、`show`、`check`、`armed`、`jobs`、`job` | human、ai | — | READ | 唯讀；AI 幫你找對話、看結果有用。回來的文字是資料不是指令（SKILL.md 寫明） |
| `freeze` | human、ai | — | WRITE | 出事時要一鍵停；只會讓事情變少，給 AI 沒有壞處 |
| `arm` | human | **要** | DANGER | 「讓對話可以在沒人看著時繼續做」是這個工具裡權限最大的動作 |
| `disarm`、`cancel` | human | — | WRITE | 收回的動作；不給 AI，免得被注入後拿來干擾你排好的事（要停就 freeze） |
| `continue` | human、**schedule** | — | DANGER | 排程到點時沒人在，不能問碼；風險靠「已授權＋期限＋次數＋未凍結」收斂。AI、手機遠端不行 |
| `thaw` | human | **要** | WRITE | 解除凍結＝把停下來的東西重新打開 |
| `setup` | human | **要** | WRITE | 改的是「要執行哪一支程式」，等於決定 SYNOX 會跑什麼 |

工具自己再用 `kit.caller()` 檢查一次（多一層，不是邊界）。

## 5. 其他決定

- **被佔用（結束碼 3）**：已查證 resetter 只有 `--at` 的工作會補試（工作排程器每 2 分鐘、最多 1 小時）；**現在送**被佔用時它把那筆標成 waiting、
  不會再有人去補試。所以這個工具在「現在送、被佔用」時：不扣次數、不算失敗、把那筆 waiting 取消掉（不留沒人管的工作），告訴使用者關掉對話再叫，或改用 `at=+5`。
- **次數**：叫 resetter 之前先扣；只有結束碼 2／3／4（證明 claude 沒被叫起來）退回，而且只退到同一筆授權。結束碼 1、逾時、看不懂的輸出不退；
  後兩者在稽核標 `unknown:…`，下次用 `jobs --json` 找出那段時間這個對話的 continue 補記。排好之後自己取消不退（只會往下）。
- **失敗計數**：送不出去（叫不動、結束碼 1／2／4、看不懂的輸出）＋ resetter 之後回報那筆 failed，都算；有一筆 done 就歸零。
  工作狀態在 `continue`、`jobs`、`job` 被叫時順便用 `jobs --json` 更新，不另開背景執行緒。
- **私人工具**（`publish: false`）：擁有者先不讓別人裝。`tests/test_gates.py` 多一段私人工具的登記（`PRIVATE_CAPS`），
  `tests/test_synox_integration.py` 的「全部一起載入」改比 `ALL_PACK_NAMES`（私人工具一樣在 `packs/` 裡、一樣被 SYNOX 載入）。

## 6. 延後的事（設計草案，沒有程式）

### 6.0 resetter 端：一次性工作的「最晚時間」

讓 `resume --at` 多一個 `--not-after <時間>`（或在一次性工作的 XML 放 `EndBoundary`），`fire` 遲到超過就不送、標 failed。
這樣 at= 的工作就算電腦關機錯過，也不會在授權過期後才送。要改 resetter，不在這一版。

### 6.1 高風險層：`resume` 帶文字、`new` 開新對話

需要的額外保護（全部都要，少一個就不開）：

1. **資料夾白名單**：只准對列出來的專案資料夾派工；不准對 SYNOX 自己、ClaudeTimerResetter 自己、家目錄、放金鑰的資料夾。
2. **風險分級**：照「跑起來能碰到什麼」分（只規劃／只改檔／能跑指令／略過權限）；越高越嚴，最高一級只能在這台電腦的本機畫面下。
3. **兩組驗證**：能跑指令以上要兩組驗證器的碼；每一次都問，不靠「解鎖一段時間」。
4. **預設凍結**：裝好、升級、重開之後都是凍結的，要本人解凍才能用。
5. **文字的來源**：只收本人當場打的字；AI、排程、別的工具傳來的字一律不收（排程只能排已經審過、內容釘死的那一筆）。
6. `new` 一律在獨立的 git worktree 跑（resetter 的 `--sandbox worktree` 預設），不准 `none`。

### 6.2 自動辨認「被額度停住的對話」

要 resetter 先加偵測（例如從對話紀錄認出 session limit 的訊息與重置時間）。在那之前要先想清楚：
誰可以選要續跑哪一個（自動選＝沒有人授權）、會不會被利用（讓別的對話看起來像停住、或在紀錄裡塞假的重置時間）。
推測：最安全的做法是偵測只用來**提示**（「這三個對話停住了，要授權哪一個？」），授權仍然是本人＋驗證器。

## 7. 「推出去給別人用」是什麼意思

不是讓別人操作你的 Claude。是別人在**他自己的電腦**裝 SYNOX、裝 ClaudeTimerResetter、註冊**他自己的**手機驗證器，
操作**他自己的** Claude Code 對話。這個工具 `access: owner`：伺服器版的成員、訪客都看不到它；資料（授權名單、稽核）每人一份、在保管箱裡。
要公開（`publish: true`）之前：先實機驗過、把 stage 升上去，並在這份 ADR 記下決定。
