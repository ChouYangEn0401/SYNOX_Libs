# ⏱ Claude 重置計時（claude-resetter）

> 🔧 **test（測試中）**，`0.1.0-alpha.1`（尚未實機驗證：只對假的 ClaudeTimerResetter 測過）。

在你開工**之前**，先打一次最便宜的探針（約 **$0.00025**），把 Claude 5 小時額度視窗的重置時鐘**提早啟動**；
也能隨時看額度用了多少、幾點重置（不花錢）。這個工具只是一座橋：真正做事的是你電腦上**另外裝好的
[ClaudeTimerResetter](https://github.com/ChouYangEn0401/ClaudeTimerResetter)**（MIT 授權）。**沒有裝它就不能用**（只會告訴你怎麼設定）。

## 為什麼要提早啟動重置時鐘

Claude 的額度是 5 小時一個視窗，而這 5 小時的倒數是從「**第一次呼叫**」那一刻才開始跑的，不是整點對齊。
重度工作常常 2、3 小時就把額度用完，然後只能乾等視窗重置，偏偏卡在最需要它的時候。

做法反過來：在預期會重度使用**之前**，先送出一次極便宜的呼叫，倒數就提早開始；等你用到額度見底時，
下一輪重置剛好接上，工作節奏不會被打斷。（觸發時間要抓提前量：是「預估額度差不多要用完的時候」，不是「開始工作的時候」。）

## 為什麼這麼便宜

| 項目 | 設定 |
|---|---|
| 模型 | 目前最便宜的一檔 Haiku |
| 工具、thinking | 全關：純文字進出（開工具是 agent 模式，貴 15 到 45 倍） |
| 對話 | 不保存、不重送歷史 |
| 提示 | 只回一個字 `k`（費用幾乎全在輸出） |
| 預算上限 | 單次 $0.02，超過就中止 |

實測一次約 $0.000254、4 秒左右。細節在 ClaudeTimerResetter 的 `docs/design.md`。

## 為什麼是「該刷新才刷新」

重置時鐘一旦在跑，再打一次**不會**讓它提早結束，只是白花錢。所以 `refresh` 先花 0 元查一次額度，再決定：

| 5 小時視窗的狀態 | 做什麼 |
|---|---|
| 還沒開始計時 | **打**：打下去重置時鐘才開始跑（這正是目的） |
| 再 15 分鐘內就要重置 | 預設**這次先不打**，告訴你等重置後再叫一次；加 `wait=是` 就等它重置完再打（最多約 15 分鐘） |
| 已經在跑而且還早 | **不打**（不花錢） |
| 查不到額度 | **打**：寧可多花 $0.00025，也不漏掉一次該做的刷新 |

所以重複叫、排程每天叫，都不會多花錢；這跟 ClaudeTimerResetter 自己的排程走同一條判斷。

## 指令

| 指令 | 做什麼 |
|---|---|
| `/resetter_usage` | 額度用了多少、5 小時與 7 天視窗幾點重置。**不花錢、唯讀**；人、AI、排程都可以叫 |
| `/resetter_refresh [wait=是]` | 該刷新就刷新、不該就不打；回一句白話（有沒有真的打、為什麼、下次重置）。有打約 $0.00025；人、AI、排程都可以叫。`wait=是` 只有你本人或排程能用（AI 呼叫時會卡 15 分鐘，所以不開放） |
| `/resetter_refresh force=是` | 不管狀態強制打一次（會花錢）。**只有你本人**能下；AI 與排程帶 `force` 都會被擋 |
| `/resetter_check` | 檢查：找得到 resetter 嗎、`.venv` 在嗎、讀得到額度嗎（代表 `claude` 登入過）。不花錢。探針本身（`claude` 命令列能不能呼叫）要真的刷新一次才知道 |
| `/resetter_setup [home=…] [python=…]` | 告訴我 ClaudeTimerResetter 的資料夾（**只有你本人**能打，AI 不能叫）；不帶參數＝看現在的設定 |

這個工具**不自己排程**，定時用 SYNOX 內建的 `/schedule_add`（見下面範例）。

## 前置條件

1. 電腦上有官方 **Claude Code 命令列**，而且**登入過**（命令列打 `claude` 要能跑）。
2. **clone ClaudeTimerResetter 並建好環境**（只有 `ClaudeTimerResetter.exe` 視窗版的**不能用**，因為它沒有命令列入口）：

   ```bat
   git clone https://github.com/ChouYangEn0401/ClaudeTimerResetter.git
   cd ClaudeTimerResetter
   scripts\setup.bat
   ```

   跑完資料夾裡會有 `.venv`（它自己的 Python，裡面裝了呼叫 Claude 用的套件）。

## 安裝與設定

1. 裝這個工具（`.synoxpack`；SYNOX 的 `python -m synox install <檔>`、網頁「功能與安裝」都可以），過 SYNOX 的安裝檢查：
   它只宣告一項能力，**開別的程式**（`subprocess`），不連網、不讀寫任何檔案。
2. 你本人打：`/resetter_setup home=<ClaudeTimerResetter 的資料夾>`。
   會自動用資料夾底下 `.venv` 的 Python；想用別的 Python 再加 `python=<python 的完整路徑>`。
3. `/resetter_check` 看能不能讀到額度；`/resetter_usage` 看現在的額度。

設定（資料夾、Python 路徑）加密存在 SYNOX 的保管箱，工具不寫任何檔。

## 應用範例

- **早上上班前自動刷新**：`/schedule_add 08:40 /resetter_refresh`。每天 08:40 到點替你執行；視窗已經在跑就不打、不花錢。
  想看幾點改成別的、排好幾個時間點，就多排幾條（時間照你的工作節奏抓提前量，例如預計 11 點開始重度使用就排 08:40 到 09:00 之間）。
  排程不能帶 `force=是`（到點會被擋）。
- **開工前看一下額度**：`/resetter_usage`，會寫「5 小時視窗：已用 37%，13:40 重置（剩 2 小時 05 分）」；
  若寫「還沒開始計時」，現在 `/resetter_refresh` 才有意義。
- **請 AI 幫忙**：對 SYNOX 的 AI 說「我今天預計重度使用，先幫我刷新」。AI 會叫 `/resetter_refresh`（不加 `force`），
  再把結果照實轉述給你；想強制打，只有你本人自己打 `/resetter_refresh force=是`。

## 風險與隱私

- **最多花約 $0.00025 一次**（有打才花；沒打的不花）。這個工具不能花更多：探針的模型、提示、預算上限都寫死在 ClaudeTimerResetter 裡。
- **不改任何檔案**：這個工具自己不讀寫檔。SYNOX 的 JobRunner 會把子程序的輸出（額度百分比、重置時間）寫進 SYNOX 自己的工作紀錄資料夾，不含任何金鑰。
- **不碰你的 Claude 帳號金鑰**：工具本身不讀 `~/.claude`、不讀 token、不連網。看額度與打探針都是 ClaudeTimerResetter 在你本機做的
  （看額度是它讀本機登入憑證、打 Claude Code 自己 `/usage` 用的那支端點；探針經官方 `claude` 命令列）。
  交給它的子程序環境會拿掉名字像密鑰的環境變數（`*_TOKEN`、`*_API_KEY`…）。
- 看額度用的那支端點**不是公開文件化的 API**，欄位可能改；壞了這個工具只會說「讀不到」。
- 只給擁有者（`access: owner`），只能私訊。設定決定了 SYNOX 會執行哪個 Python，所以只有你本人能改。

## 授權與來源

- 這個工具：跟 SYNOX_Libs 一樣，AGPL-3.0（見 repo 的 `LICENSE`）。
- 它呼叫的 [ClaudeTimerResetter](https://github.com/ChouYangEn0401/ClaudeTimerResetter)：MIT 授權；本工具不附它的程式碼，只呼叫你自己裝好的那份。
- 設計說明：ClaudeTimerResetter 的 `docs/resetter.md`（排程規則、判斷表、結束碼）與 `docs/design.md`（為什麼這麼便宜）。

## 現在的狀態

`0.1.0-alpha.1`，只對假的 resetter（暫存資料夾裡的假 `usage.py`、`ping.py`）測過：叫法、參數、結果轉述、權限（force／setup 只有本人、
AI 與排程能叫 usage／refresh）、逾時與失敗的白話訊息。**還沒在真的 SYNOX＋真的 ClaudeTimerResetter＋真的登入上試過。**
