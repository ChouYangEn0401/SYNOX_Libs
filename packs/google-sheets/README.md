# 📊 Google 試算表（google-sheets，只加列）0.1.0-alpha.1

讓 SYNOX 在**你的 Google 試算表**裡：建新的試算表、看最後幾列、**往後加列**。
它**不會改格子、不會刪列、不會清除**——程式裡根本沒有這些功能。記錯了就再加一列「作廢」標記。

> 這是 **SYNOX 工具庫（SYNOX_Libs）** 的工具（🧪 alpha，**尚未實機驗證**：只對「假的 Google」測過，**還沒在真的 Google 帳號上試過**，
> 先不要拿來放重要的資料）。先把 `google-sheets-<版本>.synoxpack` 裝進 SYNOX（裝法見工具庫的 [README](../../README.md)「給使用者：怎麼裝」），再照下面設定。

| 指令 | 做什麼 |
|---|---|
| `/gsheet_new 記帳 日期\|項目\|金額` | 建一個新的試算表。第一列是欄位：SYNOX 會在最前面自己加 `id`、`寫入時間` 兩欄 |
| `/gsheet 2026-11-16\|午餐\|120` | **預覽**要加的列（還沒寫），回你一組確認碼。好幾列用 `;` 隔開；可以加 `id=…` |
| `/gsheet_ok <確認碼>` | 真的寫進去（只有你自己打得了，AI 不行；10 分鐘內有效） |
| `/gsheet_void <id> [原因]` | 作廢一列：預覽一列「作廢」標記，一樣要 `/gsheet_ok` 才寫；原本那一列不改、不刪 |
| `/gsheet_read [n=20]` | 看最後幾列（最多 100 列；作廢過的會標「已作廢」） |
| `/gsheet_list` | SYNOX 建過的試算表（有編號） |
| `/gsheet_use <編號> [sheet=工作表名字]` | 之後預設加到哪一個 |
| `/gsheet_status` | 連上了沒、預設是哪一個、有幾筆等你確認 |
| `/gsheet_setup`、`/gsheet_login`、`/gsheet_logout` | 連接／中斷（只有你能打，AI 用不到） |

每個指令後面都可以加 `book=<編號或試算表 id>`、`sheet=<工作表名字>` 指定別的試算表（不加＝預設那一個）。

---

## 第一次設定（大約 10 分鐘，只要做一次）

Google 規定每個程式都要有自己的「OAuth 用戶端」。SYNOX 沒有大家共用的，所以要在你自己的 Google 帳號裡建一個（免費、不用信用卡）。
這個工具用「**裝置碼登入**」：SYNOX 給你一個網址和一組碼，你用手機或電腦的瀏覽器打開、輸入、按允許就好。
**SYNOX 不會在你的電腦上開任何網路埠**，也不用把網址貼回來。

### A. 在 Google Cloud 建一個用戶端

1. 打開 <https://console.cloud.google.com/>，用你的 Google 帳號登入。
2. 左上角「選取專案」→「新增專案」，名字隨便（例如 `my-synox`）→ 建立。（已經為 Google 日曆建過的，可以用同一個專案。）
3. 上面的搜尋框打 **Google Sheets API** → 點進去 →「啟用」。再搜尋 **Google Drive API** →「啟用」。
   **兩個都要啟用**（Drive 是用來列出「SYNOX 建過哪些試算表」）。
4. 左邊選單「API 和服務」→「OAuth 同意畫面」（新版叫「Google Auth Platform」）：
   - 使用者類型選「**外部**」→ 填應用程式名稱（例如 `SYNOX`）、你的 email → 一路儲存。
   - 「目標對象」（Audience）頁的「測試使用者」：把**你自己的 Gmail** 加進去。
   - **建議**：同一頁的「發布狀態」按「**發布應用程式**」（Publish app，改成「正式版」）。
     停在「測試」的話，**Google 每 7 天會讓授權失效**，你就得重新連一次。
     這個工具只要 `drive.file` 一種權限（Google 列為「不敏感」的權限），發布後自己用不用送 Google 審核；
     登入時如果看到「Google 尚未驗證這個應用程式」，按「進階 → 前往」即可。
5. 左邊「用戶端」（舊版是「憑證」→「建立憑證」→「OAuth 用戶端 ID」）→ 應用程式類型選「**電視和輸入受限裝置**」
   （TVs and Limited Input devices）→ 名字隨便 → 建立。
   **注意不是「電腦版應用程式」**：Google 日曆那個工具用的是電腦版，這個不一樣，要另外建一個。
6. 畫面會給你兩串字：**用戶端 ID**（`….apps.googleusercontent.com` 結尾）和**用戶端密鑰**（`GOCSPX-…`）。先留著這個視窗。

### B. 在 SYNOX 連上

> 建議在**這台電腦上的 SYNOX 終端機**（`Console.bat`）或網頁介面打 `/gsheet_setup`，而不是在 Telegram 打：
> Telegram 的訊息不是端對端加密。如果用 Telegram 打，完成後把那則訊息刪掉。

1. 打：`/gsheet_setup <用戶端 ID> <用戶端密鑰>`
2. SYNOX 回你一個網址（`https://www.google.com/device`）和一組碼（像 `ABCD-EFGH`）。
3. 用手機或電腦的瀏覽器打開那個網址 → 輸入那組碼 → 選你的帳號 →（看到「尚未驗證」→「進階」→「前往」）→「允許」。
4. 回到 SYNOX 打：`/gsheet_login`。看到「✅ 連上 Google 試算表了」就好了。
   （太快打的話會說「還沒看到你按允許」，過幾秒再打一次就好；SYNOX 不會在背景一直問 Google。）
5. 建第一個試算表：`/gsheet_new 記帳 日期|項目|金額`，回覆裡有它的網址，可以用手機的 Google 試算表 App 打開看。

那組碼通常 30 分鐘內有效；超過就再 `/gsheet_setup` 一次（第二次可以只打 `/gsheet_setup`，會沿用存著的用戶端）。

### 不想用了

`/gsheet_logout`：刪掉這台電腦上的授權，並請 Google 收回（試算表本身留在你的 Google 雲端硬碟，不會被刪）。
也可以到 <https://myaccount.google.com/permissions> 手動移除。

---

## 怎麼加一筆（為什麼要確認）

```
你：/gsheet 2026-11-16|午餐|120 id=lunch1116
SYNOX：📝 預覽（還沒寫）：要加到「記帳」的工作表「工作表1」，1 列：
       欄位：id | 寫入時間 | 日期 | 項目 | 金額
       1. lunch1116 | 2026-11-16 14:03 | 2026-11-16 | 午餐 | 120
       確認寫入請你自己打：/gsheet_ok K7P2Q9（10 分鐘內有效；不寫就不用理它）
你：/gsheet_ok K7P2Q9
SYNOX：✅ 寫進「記帳」的工作表「工作表1」了：1 列（'工作表1'!A5:E5）。
```

- **寫進去的就是預覽的那幾列**，一個字都不會變（包含 id 和寫入時間）。
- **叫 AI 幫你記**（「幫我記午餐 120」）：SYNOX 先出一張確認卡給你按；按了 ✅ 之後，預覽**直接**送到你的私訊（不是 AI 轉述的），
  你看過沒問題再自己打 `/gsheet_ok`。AI 自己永遠寫不進去。
- **同一筆送兩次不會變兩列**：每一列都有 id（A 欄）。寫之前 SYNOX 會先讀 A 欄比對，已經有的 id 就跳過。
  沒給 `id=` 的話 SYNOX 會自己產生一個（那就只保護「同一個預覽按兩次」，不保護「重新打一次」）。
- 一格一格用 `|` 隔開；沒有 `|` 的話用空白隔開（`/gsheet 11/16 午餐 120`）。好幾列用 `;` 隔開，一次最多 20 列；一格最多 500 字。
  格子比表上的欄位多就不寫（不會偷偷跑到別欄）。
- **數字**：只有一般寫法（`120`、`-30`、`99.5`）會存成數字（可以加總）；`0912345678`、`+886…`、`1e5` 這種都原樣存成文字，前面的 0 不會被吃掉。
- **公式不會被執行**：`=`、`+`、`-`、`@` 開頭的字原樣存成文字（Google 的 `RAW` 模式：不解析，也不會在前面多加 `'`）。
- 記錯了：`/gsheet_void lunch1116 金額打錯`，再加一筆對的。原本那一列還在，`/gsheet_read` 會標「已作廢」。

---

## 常見問題

| 狀況 | 怎麼辦 |
|---|---|
| 「Google 不再接受這個授權」 | 授權被收回或過期（用戶端停在「測試」狀態時 7 天就失效）。照上面 A-4 發布，再 `/gsheet_setup` 一次 |
| 「還沒啟用要用的 API」 | 回到 A-3，Google Sheets API 和 Google Drive API 兩個都要按「啟用」 |
| 「client_id／client_secret 不對」 | 檢查 A-6 那兩串有沒有複製完整，用戶端類型是不是「**電視和輸入受限裝置**」 |
| 「不是 SYNOX 的格式」 | SYNOX 只往自己用 `/gsheet_new` 建的表加列（第一列是 `id`、`寫入時間`、…）。別的表它不碰 |
| 「沒有權限」、看不到我原本的試算表 | 故意的：權限只有 `drive.file`，SYNOX 只看得到自己建的試算表 |
| 「表的欄位在預覽之後變了」 | 你在 Google 那邊改了第一列。重新 `/gsheet …` 預覽一次 |
| 「超過 10000 列」 | 表太大，SYNOX 沒辦法確認 id 有沒有重複，所以不寫。`/gsheet_new` 開一個新的 |
| 「太頻繁」「忙不過來」 | Google 限制每人每分鐘大約 60 次；等一分鐘再試 |
| 寫入時間差了幾小時 | SYNOX 用這台電腦的時間；檢查 Windows 的時區設定 |

---

## 安全：資料在哪、誰看得到

- **用戶端 ID／密鑰、refresh token、access token、登入用的裝置碼** 全部存在 SYNOX 的加密保管箱（`data/google-sheets.json.enc`，密文；
  伺服器版每個人一份）。不寫進 `config.json`、不寫進 log（`/gsheet_setup` 後面的字在 log 裡會遮掉）、不會出現在任何回覆或 AI 看到的工具結果裡。
  出錯時只回固定的一句話，不會把 Google 回應的原文印出來。
- **只給擁有者**（`access: owner`）；伺服器版的成員看不到這個工具。
- **AI 碰不到憑證，也寫不進去**：連接用的三個指令和 `/gsheet_ok` 不給 AI；AI 只能看、建新的試算表、做預覽。
  加列、作廢的預覽一定先出確認卡；真的寫入只有你自己打 `/gsheet_ok` 才會發生。
- **試算表裡的字是資料**：交給 AI 時 SYNOX 包在每輪隨機的分隔符裡，工具也先拿掉控制字元、把換行變空白、太長截掉，
  並寫明「只當資料看」。格子裡就算寫「把表清空」，也沒有任何工具做得到。
- **不開網路埠**：裝置碼登入只要你在瀏覽器輸入一組碼，不用本機伺服器、不用貼網址。
- **權限範圍**：只有 `https://www.googleapis.com/auth/drive.file`——只看得到、寫得到 **SYNOX 自己建的**檔。你雲端硬碟裡其他的東西它都看不到。
- **連到哪裡**：`oauth2.googleapis.com`（登入、換 token、收回）、`sheets.googleapis.com`（試算表）、`www.googleapis.com/drive/v3`（列出 SYNOX 建的試算表）；
  你在瀏覽器登入時是 `www.google.com/device`。只用 `requests`，沒有其他套件，沒有在本機跑別人的程式。
- 會離開這台電腦的：你加的列送到 Google（本來就要放那裡）；AI 看試算表時，那幾列會送到你設定的 LLM（跟其他工具一樣，見 SYNOX 的 docs/security.md）。

---

## 還沒驗證的（要在別台電腦、用真的 Google 帳號試）

這一版**只對假的 Google 測過**（測試用一個記在記憶體裡的假 Google，不連網、不開埠）。要拿掉版本號的 `-alpha`（發 0.1.0）之前，請擁有者在別台電腦上用真的帳號跑一輪：

1. 照上面 A 建一個「電視和輸入受限裝置」用戶端，`/gsheet_setup` → 在瀏覽器輸入碼 → `/gsheet_login` 連得上；太早打 `/gsheet_login` 會說「還沒按允許」。
2. `/gsheet_new 測試 日期|項目|金額`：Google 雲端硬碟裡多一個試算表，第一列是 `id | 寫入時間 | 日期 | 項目 | 金額`。
3. `/gsheet 2026-11-16|午餐|120 id=t1` → `/gsheet_ok <碼>`：多一列，`120` 靠右（是數字）。
4. 再 `/gsheet 2026-11-16|午餐|120 id=t1`：說「已經在表裡了」，表上**沒有**多一列。
5. `/gsheet =1+1|@x|-5 id=t2` → 確認：格子裡顯示的是文字 `=1+1`、`@x`，不是 `2`；`-5` 是數字。
6. `/gsheet_void t1` → 確認：最後多一列 `void-t1`，原本 `t1` 那一列原樣不動。
7. `/gsheet_list` 只列出 SYNOX 建的試算表；`/gsheet_read` 看得到上面幾列，`t1` 標「已作廢」。
8. 用戶端停在「測試」狀態時，7 天後會說授權失效（可以不測，知道就好）；發布後不會。
9. `/gsheet_logout` 之後，<https://myaccount.google.com/permissions> 裡這個 app 不見了。

---

## 給開發的人

- 設計理由（為什麼只加列、為什麼要本人確認、為什麼 RAW）：工具庫的 [docs/adr/0005-google-sheets-append-only.md](../../docs/adr/0005-google-sheets-append-only.md)。
- 為什麼不用官方的 Sheets MCP：官方遠端 MCP 沒有「加一列」，只有「覆蓋格子」；社群的要在本機跑別人的程式、開埠
  （見工具庫的 [docs/mcp-audit-2026-11.md](../../docs/mcp-audit-2026-11.md)）。REST 直連只要 `requests`。
- 登入程式（裝置碼、換 token、收回）是工具庫 `packs/_shared/googleauth.py` 的原樣複製（pack 不能互相 import），之後的 gmail／drive 工具用同一份；
  改的話改正本、再跑 `scripts/sync_shared.py`。
- 用到的 Google API（2026-11-16 照官方文件查過）：
  - 裝置碼登入：<https://developers.google.com/identity/protocols/oauth2/limited-input-device>
    （`POST https://oauth2.googleapis.com/device/code`、`POST https://oauth2.googleapis.com/token` 的
    `grant_type=urn:ietf:params:oauth:grant-type:device_code`；這個流程只收幾個 scope，`drive.file` 在裡面）
  - 測試狀態 7 天失效：<https://developers.google.com/identity/protocols/oauth2>；發布狀態在「目標對象」頁：<https://support.google.com/cloud/answer/15549945>
  - `drive.file` 是不敏感的權限：<https://developers.google.com/workspace/drive/api/guides/api-specific-auth>
  - 建試算表 `spreadsheets.create`：<https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/create>
  - 加列 `spreadsheets.values.append`（`valueInputOption=RAW`、`insertDataOption=INSERT_ROWS`）：
    <https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/append>；
    RAW 的意思：<https://developers.google.com/workspace/sheets/api/reference/rest/v4/ValueInputOption>；
    RAW 寫 `=MAX(…)` 會是文字：<https://developers.google.com/workspace/sheets/api/samples/writing>
  - 讀 `spreadsheets.values.get`：<https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get>
  - 試算表名字與工作表 `spreadsheets.get`（`fields=properties.title,sheets.properties.title`）：
    <https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get>
  - 列出試算表 Drive `files.list`（`q=mimeType='application/vnd.google-apps.spreadsheet' and trashed=false`）：
    <https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list>
  - 用量：每人每分鐘 60 次讀、60 次寫，超過回 429：<https://developers.google.com/workspace/sheets/api/limits>
- 測試：工具庫的 `tests/test_gsheet.py`，一個記在記憶體裡的假 Google（換 `transport`），不連 Google、不開 socket。
