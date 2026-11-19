# 🎯 habits（里程碑與習慣）

養成習慣、每天打卡，和有期限的里程碑旅程。**錯過只是記錄，不是失敗**：旅程裡沒做的那天插一格休息，後面的步驟自己順延；習慣可以暫停、可以「這期夠了」。

（移植自較早的一套個人工具：引擎與畫面的行為照舊，網頁畫面重寫成 SYNOX 工具頁。）

## 習慣

| 指令 | 做什麼 |
|---|---|
| `/habit <名字>` | 加一個習慣，預設撐 30 天、晚上九點提醒。進階：`repeat=daily／weekly／monthly／days／quota`、`days=135`、`every=14`、`times=3 period=week`（一期幾次）、`minutes=90`（一期花多久）、`at=20:30`、`why=…`、`end=+60d`、`kind=reminder`（純提醒） |
| `/habit_list [all／reminder／ended]` | 進行中的習慣 |
| `/habit_today` | 今天還有什麼沒做 |
| `/habit_check <名字> [done／cancel／none／rep／unrep] [day]` | 打卡、跳過、清掉、加碼＋1、退一次 |
| `/habit_log <名字> <分鐘> [day]` | 時間配額記時間（可分幾次；負的＝扣回來） |
| `/habit_pause <名字> [until=日期]`、`/habit_enough <名字>`、`/habit_pin <名字>` | 暫停（不算失敗）、這一期夠了、釘到最上面（`off=yes` 取消） |
| `/habit_rm <名字>` | 刪掉（承諾期內要 `force=yes`；危險動作，AI 要做會先送確認卡） |

## 旅程

| 指令 | 做什麼 |
|---|---|
| `/habit_ms_new <名字> steps=畫角色;寫關卡;上架` | 開一個旅程（`goal=`、`start=`、`cadence=daily／weekly`） |
| `/habit_ms_list`、`/habit_ms_show <旅程>` | 清單、每一步 |
| `/habit_ms_done <旅程> <第幾步> [undo=yes]` | 完成一步 |
| `/habit_ms_miss <旅程> [kind=rest／extra] [label=] [sticker=]` | 今天沒做：插一格休息（或臨時的事），後面順延 |
| `/habit_ms_note <旅程> <第幾步> [mood=] [note=]` | 心情與筆記 |
| `/habit_ms_rm <旅程>` | 刪掉旅程（危險） |

## 畫面

工具頁按「打開」：習慣（打卡矩陣、每條的打卡／加碼／記時間／暫停／夠了／釘選／編輯器）與旅程（清單、每一格、心情筆記、插休息、補一步）。小面板顯示今天還沒做的與進行中的旅程。

## 資料與界線

- 兩份文件（習慣與提醒、旅程），存在每個人自己的保管箱（`storage: vault`，加密）。
- 不連網、不開檔、不開別的程式（`capabilities: []`）；沒有裝任何其他工具也完整可用。
- 不內建推播：「該提醒」的判斷（每條的 `should_notify`）在資料裡，由 SYNOX 的提醒機制使用。
- **跟其他工具的連動（沒做）**：SYNOX 還沒有讓工具呼叫別的工具的機制。[`engine/links.py`](engine/links.py) 留了一個空的掛鉤 `set_habit_blocks`（讓行事曆之類提供「留給這條習慣的時段」）；沒掛＝什麼都不會出錯。
- 沒搬的：時間配額的底線／目標「跟著某個行事曆行程每一期排了多久來算」（原本的 basis；這裡只用自己設的分鐘數，舊資料裡的這個設定會被忽略、不會出錯）、番茄鐘、依「方向」分類旅程、旅程步驟的截圖、月曆總覽、煙火動畫（這些依賴原本那套程式的其他部分）。
