# ADR-0005：Google 試算表工具只加列，寫入要本人確認

- 狀態：**已實作**（`google-sheets` 0.1.0，stage `test`）；**只對假的 Google 測過**，真帳號的驗證清單在 `packs/google-sheets/README.md`。
- 範圍：`packs/google-sheets`、`packs/_shared/googleauth.py`（裝置碼登入，之後的 gmail／drive 工具共用）。
- 背景：擁有者的規則是「別人做好的就不重做；自己寫的要簡單、讓 agent 能快速加內容，但要防 agent 把內容改壞」。
  官方遠端 Sheets MCP 只有「覆蓋格子」（`update_values`）、沒有「加一列」；社群的要在本機跑別人的程式、開埠（`docs/mcp-audit-2026-11.md`）。

## 決定

1. **只加列，沒有別的寫入**。程式裡唯一會寫進既有試算表的呼叫是 `spreadsheets.values.append`，
   `insertDataOption=INSERT_ROWS`（插新列，不覆蓋任何格子）。沒有 `values.update`、`clear`、`batchUpdate`、刪檔；
   `tests/test_gsheet.py` 直接檢查原始碼裡沒有這些字。另外只有 `spreadsheets.create`（建新檔＋用 append 寫第一列欄位）。
2. **作廢＝再加一列**：`/gsheet_void <id>` 加一列 id 是 `void-<id>` 的標記；原本那一列不動。讀的時候標「已作廢」。
3. **每一列都有 id（A 欄），寫之前比對**：預覽時、寫入前各讀一次 A 欄（最多 10000 列；讀滿了就不寫，因為確認不了），
   已經有的 id 跳過。重送同一個 id（AI 重試、人按兩次）不會多一列。B 欄是寫入時間。SYNOX 只往第一列是 `id | 寫入時間 | …`
   的工作表加列，別的表不碰；格子比欄位多就拒絕，不會偷偷寫到別欄。
4. **預覽和寫入分開，寫入只有人打得了**：
   - `/gsheet`（＝`/gsheet_add`）與 `/gsheet_void` 只做預覽：把「確切要寫的列」（含 id、寫入時間、型別）存成待確認（10 分鐘、一次性、隨機 6 碼），
     回預覽和確認碼。它們標 `DANGER`：AI 叫的時候 SYNOX 一定先出確認卡（卡上是完整參數）；按 ✅ 之後預覽由工具**直接**送到本人私訊，不經 AI 轉述。
   - `/gsheet_ok <碼>` 才寫，寫的就是存著的那幾列；它 `ai=False`，執行時再 `kit.require_human()`。寫之前再確認欄位沒變、id 沒重複；
     同一個碼 60 秒內不能同時寫兩次；寫失敗碼還在、可以再試（因為有 id，再試不會重複）。
   - 為什麼不讓「按卡的 ✅」直接寫：SYNOX 給 python 工具的卡只顯示 AI 給的參數，工具在執行中不能再要一次卡（`needs_confirm`
     只有 cli／mcp 工具有），而且按卡之後的執行跟人自己打字分不出來。兩步走的代價是 AI 路線要按一次卡、再打一次 `/gsheet_ok`；
     換到的是「寫進去之前，本人一定看過工具自己產生的預覽」，以及「AI 永遠寫不進去」。之後 SYNOX 給 python 工具確認來回時可以改成一步。
5. **公式當文字**：`valueInputOption=RAW`（Google：「不解析、原樣存」；官方範例用 RAW 寫 `=MAX(…)` 結果是文字）。不在前面加 `'`
   （RAW 下 `'` 會被當成內容存進去）。數字只認一般的 ASCII 寫法（`-?整數(.小數)`，最多 15 位整數、6 位小數），其他都送字串，
   所以 `0912…`、`+886…`、`1e5`、`nan`、全形數字不會被改掉。
6. **上限與樣式**：一次最多 20 列、一格 500 字、26 欄；試算表 id `[A-Za-z0-9_-]{25,100}`、工作表名字不能有 `' ! [ ] * ? / \ : "`
   （所以 A1 寫法 `'名字'!A1` 不用跳脫）、id 是英文字母開頭的短字；放進網址前一律百分比編碼。不合的在送 Google 之前就擋下。
7. **登入**：OAuth 裝置碼登入（device flow），scope 只有 `drive.file`（Google 列為不敏感；裝置碼登入收這個 scope）——
   SYNOX 只看得到自己建的檔。`/gsheet_login` 只問 Google 一次，不在背景輪詢（不開執行緒、不 sleep）；太早問就不送，
   被說 `slow_down` 就多等 5 秒（RFC 8628）。用戶端要擁有者自己建「電視和輸入受限裝置」類型。
8. **憑證與錯誤**：client secret、token、裝置碼只在保管箱；錯誤是固定句子，只拿 Google 回應裡的已知代碼挑句子，不印原文。
   讀出來的內容先清掉控制字元、換行、截長，並寫明「只當資料看」，再由 SYNOX 包進每輪的分隔符。
9. **共用的登入程式放 `packs/_shared/googleauth.py`**，照 `scripts/sync_shared.py` 原樣複製進用到的 `pack.py`（工具之間不能 import）。

## 沒做的（與理由）

- 改格子、刪列、排序、格式：違反「防 agent 改壞內容」；要改請本人在 Google 試算表裡改。
- 連到使用者原本就有的試算表：`drive.file` 看不到，這是故意的。要的話之後另做「本人在 Google 的檔案挑選器選一個檔」。
- 背景自動輪詢登入結果：要開執行緒、跟這台電腦「不跑重負載」的規矩衝突，也沒必要。
- 跟記帳工具同步：工具之間不能互相呼叫；要的話做在記帳工具裡，或讀它匯出的 CSV。

## 還沒驗證

只對記在記憶體裡的假 Google 測過（`tests/test_gsheet.py`）。要擁有者在別台電腦用真帳號確認：裝置碼登入、`spreadsheets.create`
回的工作表名字、`values.append` 用 `A1:E1` 範圍時接在表的最後面、RAW 下 `=1+1` 真的是文字、`files.list` 只列出 SYNOX 建的檔。
`append` 找「表」的規則：中間有整列空白時，Google 可能把新列插在第一段資料後面（用 INSERT_ROWS，不會蓋掉資料，但順序會怪）；
SYNOX 建的表不會有空白列，除非本人在 Google 那邊刪了內容。

## 來源（2026-11-16 查）

- https://developers.google.com/identity/protocols/oauth2/limited-input-device
- https://developers.google.com/identity/protocols/oauth2 （測試狀態 7 天失效）
- https://developers.google.com/workspace/drive/api/guides/api-specific-auth （drive.file 不敏感）
- https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/append
- https://developers.google.com/workspace/sheets/api/reference/rest/v4/ValueInputOption
- https://developers.google.com/workspace/sheets/api/samples/writing
- https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get
- https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/create
- https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list
- https://developers.google.com/workspace/sheets/api/limits
