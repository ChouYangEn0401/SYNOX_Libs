# 🎬 影片筆記（video-notes）

把影片網址交給使用者電腦上**已經裝好的** Video Notes：下載 → 語音辨識成逐字稿 → AI 依影片類型整理成筆記（Markdown，可以貼進 Notion）。
這個工具只是橋：真正做事的是 Video Notes，它的 AI 服務、金鑰、輸出位置都照它自己的設定。

## 什麼時候用哪個指令

| 使用者想要 | 指令 |
|---|---|
| 「幫我把這支影片整理成筆記」「這幾支 YouTube 做重點」 | `v2n.add`：`urls`（一支或好幾支，空白隔開），選填 `profile`、`with`、`preset`、`focus`、`outputs`、`redo` |
| 「做好了沒」「進度」 | `v2n.status`（`n`＝工作編號；不給＝最近的工作） |
| 「給我那份筆記」「筆記內容」 | `v2n.get`：`n`＝工作編號（不給＝最新做好的），或 `item`＝筆記庫編號（`v2n.list` 列的），`slug` 選填 |
| 「我整理過哪些影片」 | `v2n.list`（`keyword` 選填） |
| 「把這幾支合併成一份」 | `v2n.combine`：`items`＝筆記庫編號（至少兩個，逗號隔開），選填 `title`、`preset`、`profile` |
| 「停掉」「不要做了」 | `v2n.cancel`（`n` 選填） |
| 「Video Notes 能不能用」「為什麼一直失敗」 | `v2n.check` |

## 規則

- **做筆記是背景工作**：`v2n.add`、`v2n.combine` 只回「排進去了（工作 #N）」，做完 SYNOX 會直接通知使用者。不要一直叫 `v2n.status` 等它；
  告訴使用者工作編號、做完會通知，要看進度再叫一次就好。一個長影片可能要十幾分鐘以上。
- `profile`（整理方式）：`auto`（自動判斷，預設）、`knowledge` 知識學習、`news` 新聞快訊、`podcast` 訪談、`quick` 娛樂快看、
  `finance` 財經產業、`general` 通用；使用者也可能有自訂的。`with`（加料，可多個）：`timeline`、`actions`、`quotes`、`glossary`、
  `brief`、`detailed`、`english`。`preset`（常用組合）：`news-roundup`、`course`、`movie-recap`、`daily-news`、`stock-show`、`study`。
  不確定就都不給（auto）。`focus`：使用者特別想知道的主題（一句話）。
- 處理過的影片預設沿用已經有的檔（不重新下載、不重新轉錄）；使用者明說要重做才給 `redo`（`download`、`transcribe`、`notes` 或 `all`）。
- **合併會用到 AI 額度**：規模很大時 Video Notes 會拒絕並要求確認；把那句話轉告使用者，**使用者同意之後**才帶 `yes=是` 再叫一次，不要自己加。
- 每支影片都會用掉使用者的 AI 額度（Claude 訂閱或 API）；使用者一次丟很多支時先確認他真的要全部做。
- 回覆裡「還沒設定 Video Notes 在哪裡」：**不要**替使用者設定（`v2n.setup` 只有本人能打）；請他自己打 `/v2n_setup program=<路徑>`。
- 錯誤訊息裡有「怎麼辦」就照實轉告（例如升級 yt-dlp、重新登入 Claude Code）；不要自己猜指令。
- 筆記全文可能很長：使用者只要重點時，拿到之後自己摘要，不要整份貼回去。
