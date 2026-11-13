# ✅ Todoist

讓 SYNOX（Telegram、網頁、AI）直接看、加、完成你 Todoist 裡的待辦。用你自己的 **API token**，不用另外申請什麼、不用開任何網路埠。

> 這是 **SYNOX 工具庫（SYNOX_Libs）** 的工具（🧪 alpha：只對假傳輸測過，還沒對真的 Todoist 長時間用過）。
> 先把 `todoist-<版本>.synoxpack` 裝進 SYNOX（裝法見工具庫的 [README](../../README.md)「給使用者：怎麼裝」），再照下面設定。

## 設定（一次就好，約 1 分鐘）

1. 用電腦打開 Todoist 網頁版 <https://app.todoist.com> 並登入。
2. 點右上角（或左上角）你的**頭像 → 設定 → 整合（Integrations）→ 開發者（Developer）**。
3. 在「API token」那一格按**複製**（一串 40 個英數字）。
4. 回到跟 SYNOX 的對話（Telegram 或網頁），打：

   ```
   /todoist_token 貼上那串字
   ```

5. 看到「✅ 接上 Todoist 了」就好了。**接著把你剛剛貼 token 的那則訊息刪掉**（Telegram 長按 → 刪除）。
6. 試試看：`/todoist_list`。

不想把 token 貼進聊天：在跑 SYNOX 的那台電腦打 `python -m synox connect todoist`（輸入時不顯示、不進聊天紀錄，0.1.2 起）。
兩個地方都設過的話，用**最後設的那一個**（`/todoist_token` 會清掉 connect 存的那份）；`/todoist_logout` 兩份都清。

> token 等於你 Todoist 帳號的鑰匙：不要傳給別人、不要貼給 AI。
> SYNOX 把它**加密**存在這台電腦的保管箱（`data/todoist.json.enc`；用 connect 設的在 `data/todoist.auth.json.enc`），log 裡也會遮掉。
> 不想用了：打 `/todoist_logout`（刪掉這台電腦存的 token）；要讓 token 完全失效，到同一頁按「重新產生（Regenerate）」。

## 指令

| 指令 | 做什麼 |
|---|---|
| `/todoist <內容>` | 加一項。例：`/todoist !! 繳電費 明天 #家事 @電話`（`!` 重要、`!!` 很重要；`#專案`，沒這個專案就當標籤；`@標籤`） |
| `/todoist_list` | 全部還沒做完的；`/todoist_list today`（今天到期）、`overdue`（逾期）、`#專案`、`@標籤` |
| `/todoist_done 3` | 打勾完成 `[3]`（重複的待辦會排到下一次） |
| `/todoist_due 3 下週一` | 改期；`/todoist_due 3 無` 拿掉到期 |
| `/todoist_rm 3` | 從 Todoist 刪掉（⚠️ 救不回來；AI 要做會先送確認卡給你按） |
| `/todoist_projects`、`/todoist_labels` | 看有哪些專案、標籤 |
| `/todoist_token`、`/todoist_logout` | 設定／拔掉 token（只有你自己打，AI 不能用） |

`[3]` 這種編號是 SYNOX 給的短號：第一次看到那個任務就配一個，之後不會變。到期可以寫「明天、後天、週五、下週一、11/20、15號、下午3點、2026-11-20 15:00」；
SYNOX 看不懂的寫法（例如 `every monday`）原樣交給 Todoist 解析。

AI：跟 AI 說「Todoist 今天有什麼要做」「加一個明天繳電費」「第 3 個做完了」就會用這組指令。

## 跟本機「待辦（todo）」的關係

指令寫法、欄位名稱（`text`、`due`、`priority`、`filter`、`n`）跟工具庫的本機待辦 [`todo`](../todo/README.md) 一樣，換用時不用重學。
差別：`todo` 存在這台電腦（不連網）；這個存在 Todoist（手機 App、別的裝置都看得到）。

## 安全與隱私（說實話版）

- **伺服器版只有擁有者用得到**：裝得上，但這個工具是 `access: owner`——成員、訪客打了跟不存在一樣，AI 也拿不到；token 存在擁有者自己的 `data/users/<id>/`。為什麼還不開給成員（密鑰還走聊天、管理者解得開每個人的保管箱、還沒對真的服務用過）見 [ADR-0003](../../docs/adr/0003-personal-and-server-mode.md) 第 4 節。個人版改成伺服器版時要重新設定一次。
- **會連到的只有** `https://api.todoist.com/api/v1`（Todoist 官方 API v1；舊的 REST v2 已退役）。不開埠、不執行別人的程式，只用 `requests`。
- token 存保管箱（密文）；伺服器版每個人一份、用自己的金鑰。log 的 `/todoist_token …` 會變成 `***`。
- token 是透過聊天傳進來的：**Telegram 的訊息不是端對端加密**，所以設定完請刪掉那則訊息。網頁介面的對話紀錄是密文存在這台電腦。
- 個人 API token 能讀寫**整個 Todoist 帳號**（Todoist 沒有更小的權限）。SYNOX 只做上面這幾件事；刪除一律要你按確認（AI 叫的時候）。
- 有 AI 時，AI 看得到它叫的指令回的那幾行（例如任務標題）。
- `pack.json` 宣告 `capabilities: ["network"]`，通過 SYNOX 安裝五關的第 1、2 關（宣告、靜態掃描）。

## 給工程師

- 程式：`pack.py`（`Client` 包 API v1：`GET tasks`、`GET tasks/filter?query=`、`POST tasks`、`POST tasks/{id}`、`POST tasks/{id}/close`、
  `DELETE tasks/{id}`、`GET projects`、`GET labels`；分頁照 `next_cursor`，最多 10 頁×200）。
- 重要度對照：SYNOX 0／1／2 → Todoist `priority` 1／3／4（Todoist 的 4＝App 上的 P1）。
- 到期：看得懂的中文日期自己換成 `due_date`／`due_datetime`（沒有時區＝跟著 Todoist 帳號的時區）；看不懂的送 `due_string`。
- 測試：工具庫的 `tests/test_todoist.py`，全部用假的 HTTP session（`pack.HTTP`），不打真的 Todoist。
- 日期解析是工具庫 `packs/_shared/zhdate.py` 的原樣複製（改正本、再跑 `scripts/sync_shared.py`）。
- 對真的 Todoist 試：SYNOX 的 `scripts/smoke_real.py --only todoist`。
- 呼叫是同步的（逾時 15 秒），Todoist 慢的時候那一則會等。
