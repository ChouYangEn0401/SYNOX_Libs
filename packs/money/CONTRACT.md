# 記帳工具 0.4.0 的契約（給一起開發的人看）

這份寫的是**大家同時會用到、一次定死的介面**：名字、欄位、值怎麼寫、錯誤怎麼轉、隔離規則、檔案歸誰。
照這份寫，每個人只改自己的檔，不用碰共用的檔。要改契約，在回報裡寫清楚「要改什麼、為什麼」，由主 session 決定。

`tests/money/test_contract.py` 會檢查：這份的名字清單跟 `pack.json` 一樣、每個名字都有掛函式、每個動作都有同名同危險程度的指令、
`app/js/routes.js` 的每一列都指到宣告過的名字、`app/` 裡沒有自己連線與輪詢。

## 1. 目錄與檔案歸屬

```
packs/money/
  pack.json        宣告（契約檔）
  pack.py          很薄：install() 把 bridge 的表掛到 SYNOX 上
  CONTRACT.md      這一份
  engine/          邏輯層（W2 搬；schema.sql 是契約檔）
  bridge/          SYNOX 的指令與畫面 ↔ engine：一個領域一個檔
    table.py       指令表的格式、錯誤怎麼轉、回覆怎麼排；金額與日期的共用解讀（第 5 節）
    session.py     「這個人的帳本」的唯一入口（契約檔）
  app/             完整的記帳畫面
    index.html、style.css（契約檔）、css/<分頁>.css
    js/api.js（轉接器，契約檔）、js/routes.js（對照表，契約檔）、js/main.js（契約檔）、js/dom.js、forms.js、info.js
    js/views/*.js  每個分頁一個檔
  panel.html       工具頁的小面板（W6）
  SKILL.md、skills/*.md   給 AI 的手冊（W7）
```

| 誰 | 會動的檔（`packs/money/` 底下，除非另寫） |
|---|---|
| W0 | `pack.json`、`pack.py`、`CONTRACT.md`、`bridge/__init__.py`、`bridge/session.py`、`bridge/table.py`、8 個領域檔的空殼、`app/index.html`、`app/style.css`、`app/css/*.css`（空檔）、`app/js/{api,routes,main,dom,forms,info}.js`、`app/js/views/*.js`（空殼）、`tests/money/__init__.py`、`tests/money/conftest.py`、`tests/money/test_contract.py` |
| W2 | `engine/**`、`tests/money/engine/**`；契約檔裡只准改 `bridge/session.py` 的 `_connect`／`_close`（接上引擎） |
| W3 | `bridge/ledger.py`、`app/js/views/book.js`、`app/css/book.css`、`tests/money/test_ledger.py`、`test_app_book.py` |
| W4 | `bridge/home.py`、`app/js/views/home.js`、`app/css/home.css`、`tests/money/test_home.py`、`test_app_home.py` |
| W5 | `bridge/plan.py`、`bridge/setup.py`、`app/js/views/{plan,setup,onboard}.js`、`app/css/{plan,setup}.css`、`tests/money/test_{plan,setup,onboard}.py` |
| W6 | `bridge/compat.py`、`bridge/legacy.py`（新檔）、`panel.html`、`tests/money/test_{compat,legacy}.py`；刪 `app/money.js`、`app/money.css`、`tests/test_money.py`、`tests/test_money_app.py` |
| W7 | `bridge/review.py`、`bridge/flow.py`、`app/js/views/reviews.js`、`app/css/review.css`、`SKILL.md`、`skills/*.md`、`tests/money/test_{review,flow,skill}.py` |
| W8 | `bridge/piggy.py`、`app/js/views/{piggy,reconcile}.js`、`app/css/piggy.css`、`tests/money/test_piggy.py` |
| W9 | `tests/money/test_{isolation,deid,decimals}.py`、`tests/money/js/*.mjs`、`tests/test_gates.py`、`README.md`（工具的）、新的 ADR；整合收尾時改了 `table.py`、`session.py`、`pack.py`、`dom.js`、`main.js`、`forms.js` 與各領域檔的金額／日期呼叫處 |

契約檔（W0／W2 以外不改）：`pack.json`、`app/js/api.js`、`app/js/routes.js`、`app/js/main.js`、`app/style.css`、
`engine/schema.sql`、`bridge/session.py`。`bridge/table.py`、`pack.py` 也當契約檔看待。

## 2. 指令表（`bridge/table.py`）

每個領域檔（`ledger`、`home`、`plan`、`setup`、`flow`、`review`、`piggy`、`compat`）匯出：

```python
DOMAIN     = "ledger"
COMMANDS   = (Cmd(name, summary, effect, fields, run, ai=False|{"scenario": "…"}, positional=None,
                  app=False, default=False, describe=None), ...)
READS      = frozen({"meta": fn(book, params) -> dict, ...})
EXPORTS    = frozen({"book": fn(book, params) -> kit.Download})
WEB_ROUTES = frozen({"summary": fn(book) -> dict})          # 只有 compat
```

- `run(book, values) -> Result(data: dict, text: str, warnings: list)`：**同一個 run** 同時給指令（回 `text`）與畫面動作（回 `data`）。
  `values` 是 SYNOX 檢查過的「欄位名 → 字串」。
- 指令：回 `text`＋每句提醒一行（`⚠️ …`）；`text` 空的就回「✅ 好了」。
- 畫面動作：回 `data`（一定是 dict）；有提醒而 `data` 沒有 `warnings` 鍵時補上 `data["warnings"]`。
- `app=True`：同時是畫面的同名動作（名字要寫在 `pack.json` 的 `web.app.actions`，危險程度一樣）。
- `positional`：打字時照位置填幾個欄位；`None`＝必填欄位的個數。
- `default=True`：`/money 120 午餐` 走它（全工具只有 `add`）。
- `describe(book, values) -> str`：危險動作確認框的那一句（選填）。
- 表一律唯讀（tuple、`frozen()`）：模組層級不准有會變的 dict／list。
- 還沒做的用 `todo(name)`／`todo_read(name)`／`todo_export(name)`：丟 `kit.AppError("這個功能還在做…")`。
- 欄位最多 20 個（SYNOX 的上限）。畫面送來的欄位名**一定要**在 `fields` 裡，不然 SYNOX 直接擋下。
- 只能**加**欄位、加檢查、改說明；`routes.js` 會送的欄位名（下面第 3 節）**不准改名或拿掉**。
- 給 AI 的指令（`ai` 不是 False）控制在 15 個左右，每個寫一句使用情境（120 字內、一行、不放網址與指令樣子的字）。
  SYNOX 現在只有 cli 工具的 scenario 會進 AI 的說明；python 工具先存在表裡，`ai` 只當開關。

## 3. 名字清單

### 讀（`web.app.reads`）

| 名字 | 誰 | 參數 |
|---|---|---|
| `meta` | W3 | — 回 `write_seq`、`today`、`current`（本期）、`default_account`、`inbox`、`piggy`（true／false：要不要顯示撲滿分頁；小數位數不是 0 的帳本一律 false）、`currency`（`code`、`symbol`、`decimals`：畫面照它換算金額） |
| `ref` | W3 | — |
| `guess` | W3 | `desc`、`time`、`date` |
| `items` | W3 | `category` |
| `passbook` | W3 | `period` |
| `txn` | W3 | `id` |
| `inbox` | W3 | — |
| `batches` | W3 | `limit` |
| `home` | W4 | `period` |
| `year` | W4 | `year` |
| `allowance` | W4 | `period` |
| `conflicts` | W4 | — |
| `moves` | W4 | `period` |
| `setup` | W5 | `period` |
| `features` | W5 | — |
| `book_info` | W5 | — |
| `reviews` | W7 | `period`、`kind` |
| `review` | W7 | `id` |
| `piggy` | W8 | `period`、`level` |
| `piggy_trades` | W8 | `level`、`book`、`symbol`、`offset`、`limit` |
| `piggy_positions` | W8 | `level`、`book` |
| `piggy_book` | W8 | `id` |
| `piggy_compare` | W8 | `level`、`quotes`、`offset`、`limit`、`hidden`、`book` |
| `books` | W8 | `level`、`archived` |
| `quotes` | W8 | `symbols` |
| `quotes_needed` | W8 | — |
| `reconcile` | W8 | — |

### 動作（`web.app.actions`，每個都是同名的指令）

| 名字 | 誰 | 危險 | 給 AI | 只收 | 欄位 |
|---|---|---|---|---|---|
| `add` | W3 | WRITE | ✓ | — | amount、category、desc、account、date、time、note、kind、to、purpose、refund、for_period、merchant、nature、counterparty、direction |
| `in` | W3 | WRITE | ✓ | — | 同 add |
| `transfer` | W3 | WRITE | ✓ | — | 同 add |
| `iou` | W3 | WRITE | — | — | 同 add |
| `edit` | W3 | WRITE | ✓ | — | id、amount、desc、category、account、to、date、time、merchant、note、for_period、nature、counterparty、advance |
| `rm` | W3 | DANGER | — | — | id |
| `undo` | W3 | WRITE | ✓ | — | （無） |
| `revert` | W3 | DANGER | — | — | id |
| `note` | W3 | WRITE | — | — | date、text、replace |
| `reclassify` | W3 | WRITE | — | — | （無） |
| `dedup` | W3 | WRITE | — | — | id、action、expect_a、expect_b（沒給 id＝重新掃一次） |
| `bucket_skip` | W4 | WRITE | — | human、remote | id、period、undo |
| `bucket_setaside` | W4 | WRITE | — | human、remote | id、period、undo、amount、to、from_account、category |
| `bucket_fold` | W4 | WRITE | — | human、remote | id、into、add_to_parent、to、period |
| `move` | W4 | WRITE | — | human、remote | from_bucket、to_bucket、amount、note、period、undo（給 undo＝拿掉那一筆挪用） |
| `fixed_post` | W4 | WRITE | — | — | period、upcoming |
| `envelope_do` | W4 | WRITE | — | — | id、amount、date、category |
| `plan` | W5 | WRITE | — | human、remote | period、income_expected、buckets、group_budgets |
| `plan_names` | W5 | WRITE | — | human、remote | must、flex、living |
| `bucket` | W5 | WRITE | — | human、remote | id、label、locked、active、categories、add_categories、rollover、rollover_from、flex |
| `fixed` | W5 | WRITE | — | human、remote | id、name、kind、amount、day_of_month、category、account、to、purpose、cadence、note、active、bucket |
| `group` | W5 | WRITE | — | human、remote | id、name、bucket、icon、active |
| `cat_add` | W5 | WRITE | — | — | name、group、keywords、kind、id、default_budget、cap_basis、icon、active、bucket |
| `account` | W5 | WRITE | — | human、remote | id、name、kind、aliases、floor_amount、target_amount、card_closing_day、card_due_day、pay_from、asset_class、bucket、active |
| `setting` | W5 | WRITE | — | human、remote | key、value |
| `feature_on` | W5 | WRITE | — | human、remote | name |
| `init` | W5 | WRITE | — | human、remote | currency、start_day、seed、features |
| `book_new` | W5 | DANGER | — | human | confirm |
| `review_apply` | W7 | WRITE | — | — | id、period、force |
| `review_reply` | W7 | WRITE | — | — | id、text |
| `review_save` | W7 | WRITE | ✓ | — | kind、key、content、grade、title、summary、good、watch、strategy、suggestion（content 是整份 JSON；也可以一欄一欄給） |
| `trade_add` | W8 | WRITE | — | — | date、action、level、account、book、time、symbol、name、shares、price、gross、fee、tax、net、note、day_trade、stop_price、instrument、multiplier |
| `trade_rm` | W8 | WRITE | — | — | id |
| `pbook` | W8 | WRITE | — | human、remote | id、level、account、name、kind、start、end、start_date、end_date、capital、max_loss、per_trade_loss、tithe_rate、done、note |
| `pbook_close` | W8 | WRITE | — | human、remote | id、do（close／reopen／archive／unarchive） |
| `piggy_hide` | W8 | WRITE | — | human、remote | layer、key、hidden |
| `piggy_set` | W8 | WRITE | — | human、remote | bench_rate、bench_low、tithe_rate、month_max_loss、bench_name（基準叫什麼；- 清掉） |
| `count` | W8 | WRITE | — | — | account、symbol、shares、as_of、note |
| `price_push` | W8 | WRITE | — | — | prices、at、source |

「只收」＝`pack.json` 的 `callers`（SYNOX 在執行時檢查）：畫面專用的設定類指令只收你本人（human）與手機入口（remote）；
危險的只收 human（手機入口本來就不能做危險動作）。

**新增與修改共用一個動作**（`bucket`、`fixed`、`group`、`cat_add`、`account`、`pbook`、`dedup`、`move`）：SYNOX 一個畫面最多 40 個動作，
設計報告列的 50 個放不下。規則一律是「沒給 `id`＝新增；給 `id`＝改那一個」（`move` 是給 `undo`＝拿掉；`dedup` 沒給 `id`＝重新掃）。
`cat_add` 沿用 0.3.1 的名字（舊版本來就是「新增，或改已有的」）。

### 匯出（`web.app.exports`）

| 名字 | 誰 | 內容 |
|---|---|---|
| `book` | W5 | 整本帳，**只能是 CSV**（SYNOX 的匯出只收 CSV）：欄位 `table`、`data`，每一列是「表名＋那一列的 JSON」（`kit.csv_bytes`）。個人版下載前 SYNOX 會提醒沒有加密；伺服器版 SYNOX 不給匯出 |
| `entries_csv` | W6 | 照篩選的帳目 CSV（一定用 `kit.csv_bytes`） |

### 只有指令（不是畫面動作）

| 名字 | 誰 | 危險 | 給 AI | 欄位（* 必填） |
|---|---|---|---|---|
| `list` | W3 | READ | ✓ | n、period、date、category、group、account、q、keyword、month、kind |
| `show` | W3 | READ | ✓ | id* |
| `summary` | W4 | READ | ✓ | period |
| `check` | W4 | READ | ✓ | period |
| `envelope` | W4 | READ | — | period |
| `card` | W7 | READ | — | card、statement |
| `card_statement` | W7 | WRITE | — | card、closing_date、total、due_date、period_start、min_payment、lines～lines5（「日期 說明 金額」用分號分開）、content（整份 JSON）、source_ref |
| `card_match` | W7 | WRITE | — | line*、txn（選填：create／ignore 這類不用指定帳）、action |
| `card_pay` | W7 | WRITE | — | id*、from_account、amount、date |
| `debt` | W7 | WRITE | — | do（list／all／show／add／pay／edit）、id、name、kind、periods、principal、per、start、account、lender、nature、paid_before、no、from_account、date、note、active |
| `lend` | W7 | READ | — | （無） |
| `balance` | W7 | WRITE | — | account、balance、as_of、note、adjust（補差額：核對紀錄的編號） |
| `bill` | W7 | WRITE | — | do（list／trend／add／pay）、id、type、amount、usage、period_start、period_end、due_date、paid、account、date、note |
| `attach` | W7 | WRITE | — | id*、file* |
| `report` | W7 | READ | ✓ | kind（week／month）、key、period、full（連明細與小日記）、review（給寫檢討用的那一包） |
| `review_due` | W7 | **WRITE** | — | peek（yes＝只看不標送出）。會把該送的標成送出（寫 notifications 表），所以是 WRITE |
| `ingest` | W7 | WRITE | ✓ | records*（JSON 字串）、source_ref、dry_run |
| `month` | W6 | READ | ✓ | month |
| `budget` | W6 | WRITE | — | amount* |
| `cap` | W6 | WRITE | — | category*、amount* |
| `cats` | W6 | READ | — | name |
| `cat_rm` | W6 | WRITE | — | name* |
| `template` | W6 | WRITE | — | name |
| `import_csv` | W6 | WRITE | — | file*、dry_run |

這一節的指令不在 `routes.js` 裡：欄位由負責的人自己決定（名字、危險程度要改就在回報寫）。

0.3.1 的舊指令名全部還在：`add`（預設）、`in`、`transfer`、`list`、`undo`、`rm`、`edit`、`month`、`budget`、`cap`、`cats`、`cat_add`、
`cat_rm`、`template`，以及小面板的 `GET /summary`。舊的畫面讀（`month`、`entries`、`categories`）與舊動作（`budget`、`cap`、`cat_rm`、
`template`）只有舊畫面在用，0.4.0 不再是畫面的讀與動作（指令還在）。

## 4. 命名規則

- 指令、讀、動作、匯出的名字：小寫英數字與 `_`，字母開頭；指令是 `/money_<名字>`（`add` 是 `/money`）。
- 欄位名：小寫英數字與 `_`；跟原本畫面送的 body 鍵一樣（`for_period`、`from_bucket`…），路徑裡的編號一律叫 `id`
  （`DELETE /api/moves/<編號>` 例外：送成 `move` 的 `undo`）。
- 檔名：`bridge/<領域>.py`、`app/js/views/<分頁>.js`、`app/css/<分頁>.css`、`tests/money/test_<領域>.py`。

## 5. 值怎麼寫（畫面與指令一樣）

- **一律是字串**（SYNOX 的規定：每欄 2000 字內、最多 20 欄、整包 64 KB）。
- **清掉用「-」**（跟 0.3.1 一樣）：轉接器把 PATCH 送的 `null` 與空字串都轉成 `-`；其他方法的 `null`／空字串＝沒給。
- **是／否**：`yes`／`no`（bridge 也要收 `true`／`false`、`1`／`0`、`是`／`否`）。
- **清單**：逗號分開（`咖啡,拿鐵`；bridge 也要收 `、`、`，`）。
- **物件**：JSON 字串（例：`plan` 的 `buckets` 是 `{"fixed": "12000"}` 這種；只給要改的格子）。
- **金額**：帳本裡一律是**最小單位**的整數（新台幣就是元）；小數位數是帳本設定 `currency.decimals`（0～3）。
  - **畫面送來的**（完整畫面的讀、動作、匯出；手機入口也是）：已經是最小單位的整數字串（小數位數 2 時 99.50 送 `"9950"`）；
    有小數點就擋（「畫面送來的金額要是最小單位的整數」）。
  - **人打的字**（聊天指令、SYNOX 網頁主控台打字、AI、排程）：照小數位數換算（`99.5` → 9950；可以有千分位、「元」、幣別符號）；
    小數比帳本的位數多就擋。
  - 分辨靠 `book.screen`（第 8 節），**不看** caller 的種類：網頁主控台打字的 caller 也是 web，但那是人打的字。
  - 全工具只用 `bridge/table.py` 的一套：`decimals(conn)`、`typed(book)`、`to_minor(book, 字, label, field=, zero=, negative=)`、
    `amount_of(book, values, 名字, …)`、`show(conn, 最小單位, symbol=, trim=)`。領域檔不准自己再寫一份換算。
  - 回給人看的字照小數位數印（`show`；引擎的 `report.text`、週報、檢討摘要用 `report.money(n, places)`）。小數位數 0 時跟以前完全一樣。
  - 畫面那一邊：`app/js/dom.js` 的 `num(最小單位)` 顯示、`toMinor(字)` 換成最小單位、`major(最小單位)` 放回輸入框、
    `count(n)` 給筆數天數股數（不換算）；`setDecimals(d)` 由 `main.js` 照 `meta.currency.decimals` 設（設定頁與第一次使用改完也會設）。
  - 撲滿（W8）的價金與損益還只會算整數元：小數位數不是 0 的帳本，`meta.piggy` 是 false，撲滿的讀與動作回「撲滿目前只支援整數幣別」
    （對明細卡 `reconcile` 不受影響）。
- **日期**：`YYYY-MM-DD`；也收「今天、昨天、前天、1/15、01-15、2030/1/15」（`table.parse_day`；只有月日的算今年）。時間 `HH:MM`。
  記帳主線（`ledger`）另外擋明天以後與民國年。
- 讀的參數：照原本 `api()` 的規則，`undefined`、`null`、空字串、`false` 都不送；`true` 送 `"true"`。

## 6. 回傳 data 的慣例

- 寫入的動作一律帶 `batch_id`（沒改到東西是 `null`，畫面就不給「復原」）與 `write_seq`（寫入序號）。
- 有提醒放 `warnings`（清單）。
- 讀回的形狀照原本的 `/api/…`（畫面的 views 原樣搬，不改畫面邏輯；跑起來有錯就改 bridge 回的形狀）。
- `meta` 多一個 `piggy`：`false`＝沒有證券帳戶、也沒開投資選配，畫面不顯示撲滿分頁。
- `home` 回 `{"onboarding": true}`＝要跑第一次使用的引導，總覽叫 `views/onboard.js` 的 `start(app)`；`features` 讀的 `onboarding` 是同一個值。
  判斷只有一個（`bridge/setup.py` 的 `needs_onboarding`）：**沒有 `setup.done`，而且這本帳還沒有資料**才引導。
  舊版資料轉進來過（設定 `legacy.v03`）或已經記過帳（例如先在聊天打 `/money 120` 才開畫面）都算有資料：不引導、也不再問起始類別（`can_seed` false）。只有別的寫入（例如跳過一格）不算開始用。

## 7. 錯誤怎麼轉

- 不行的時候丟 `kit.AppError(一句給人看的話)`：指令回「❌ 那句話」，畫面跳那句話。
- 引擎的錯（`engine/errors.py` 的 `LedgerError`：`code`、`message`、`hint`、`suggestions`）由 `table.as_app_error` 轉成
  `kit.AppError`：訊息＝`message`＋「　是不是：a、b」＋「　下一步」；狀態碼 invalid 400、not_found 404、conflict 409、其他 409。
  畫面原本的 `errorText` 顯示的就是同一串字。
- 其他例外不接：交給 SYNOX（訊息不會出現在回應、log、稽核；指令那邊算工具出錯）。
- 引擎的錯誤訊息**不准有路徑**（隔離規則 9）。
- 轉接器（`api.js`）把共用元件的錯包回原本的 `ApiError`（`message`、`code`、`fields`、`status`）；
  使用者在確認框按取消＝`code: "cancelled"`。

## 8. 隔離九條（硬規則）

| # | 規則 |
|---|---|
| 1 | 帳本位置**只**從 `pack.path_of(<這個人的編號>, "book")` 來（個人版 `data/money/files/book/`，伺服器版 `data/users/<id>/money/files/book/`） |
| 2 | 引擎的「資料夾在哪」每次呼叫明確設定（`contextvars`），沒設定就丟錯；不讀環境變數、不退回專案的 `data/` |
| 3 | 只有 `bridge/session.py` 的 `open_book(pack, user)` 可以開資料庫；每次呼叫開、用完關；連線不存在模組或 pack 物件上 |
| 4 | `engine/` 與 `bridge/` 不准有模組層級的可變狀態（快取、dict、list、`lru_cache`）、不准寫 `os.environ` |
| 5 | 畫面與指令送來的參數裡**沒有**任何「使用者」「路徑」「資料夾」欄位；使用者只從 SYNOX 的登入身分來 |
| 6 | 匯出、匯入、附檔、備份、封存全部在這個人的資料夾裡；檔名經 `book.path(...)`（＝`pack.path_of`，擋 `..`、絕對路徑） |
| 7 | 每個人一把寫入鎖（照編號；`session.Locks`，掛在 pack 物件上），SQLite 設忙碌等待 |
| 8 | 改動紀錄的「誰」寫 `synox:<來源>`（`book.actor`），不寫名字 |
| 9 | 引擎以外的例外訊息不外流（SYNOX 本來就這樣）；引擎的錯誤訊息不含路徑 |

`pack.store` 只有 `bridge/legacy.py`（W6）可以用，而且一定是 `pack.store.of(uid)`（讀 0.3.1 的舊資料）；任何地方都不准用 `pack.path()`。

### `open_book` 給的東西

```python
with session.open_book(pack, user) as book:                 # 指令（人打字、AI、排程）
with session.open_book(pack, user, screen=True) as book:    # 完整畫面的讀、動作、匯出、確認框（pack.py 畫面那一層）
    book.conn      # 資料庫連線
    book.actor     # "synox:telegram"、"synox:web"、"synox:ai"…（只寫進改動紀錄，不拿來判斷是不是畫面）
    book.screen    # 這次是不是畫面送來的（金額已經是最小單位）
    book.today     # 帳本的今天（date）：帳本設了 tz_offset＝UTC 現在＋時差（session.utc_now），沒設＝這台電腦的今天
                   #   （session.now）；測試換掉這兩個
    book.user_id   # SYNOX 給的整數編號
    book.path(*parts)   # 這個人自己的檔案資料夾裡的路徑（import_csv、attach 讀檔用）
    book.fresh     # 這次是不是剛建好的空帳本
```

開好之後、交給 run 之前，`open_book` 會叫 `compat.on_open(pack, uid, book)`（在寫入鎖裡）：
W6 在這裡判斷「剛建好、而且這個人有 0.3.1 的舊資料」→ 轉進來（`legacy.py`）。

## 9. 儲存層

- 帳本是這個人資料夾裡的**一個 SQLite 檔，沒有加密**（0.4.0 第一版）。備份、封存、匯出也都是沒加密的檔，畫面與說明照實寫，
  不准再寫「加密」。
- 日後要改成放進 SYNOX 保管箱（每次用時在記憶體解開）：**只改 `bridge/session.py` 的 `_connect`／`_close`**——
  其他檔都只拿 `book.conn`，不知道檔案在哪、有沒有加密。
- `pack.json` 的 `storage` 還是 `vault`：這個欄位只管 `pack.store`（SYNOX 給工具存一份 JSON 的地方），**不管帳本**。
  0.3.1 的舊資料就是加密存在 `pack.store` 裡，W6 要讀它轉進新帳本，所以這裡不能改成 `plain`（改了就讀不到舊資料）。
  帳本本身有沒有加密以上一點為準。

## 10. 畫面

- `index.html`：第一個 `<script>` 是 `/_synox/kit.js`；沒有內嵌程式、沒有外部連結；連 `/_synox/kit.css`、`style.css` 與 6 個 `css/<分頁>.css`。
- `main.js`：等 `SynoxApp.ready` 才開始；**不輪詢**，回到這個分頁（`visibilitychange`）時讀一次 `meta`，寫入序號變了才重畫；
  `app.hideTab(名字)`／`app.showTab(名字)`；`meta.piggy === false` 時不顯示撲滿。可能重複的帳不再一打開就問（放在總覽「待解決」）。
- `api.js`：同樣的 `api(path, {method, body, params})` 與 `ApiError`、`errorText`；裡面只叫 `SynoxApp.read`／`act`（`quiet`，
  錯誤由畫面自己顯示）／`download`。多一個 `download(name, params)`：原本 `<a href="/api/book/export">` 那種下載要改成叫它。
  上傳（帳本匯入）這一版做不到，回一句白話。
- `routes.js`：原本的每一條 `/api/…` → 讀、動作或匯出（格式寫在檔頭）。
- 分頁之間互相 import 的名字（空殼已經留好，搬進來時名字與參數不要變）：
  `book.js` 的 `recordForm`、`payKey`、`KINDS`；`plan.js` 的 `postRecurring`；`piggy.js` 的 `pendingList`、`titheDone`、`titheRate`；
  `reconcile.js` 的 `reconcileCard`（空殼回 `null`，呼叫的地方讀不到就不顯示那張卡）；`onboard.js` 的 `start(app)`。
- SYNOX 畫面的 CSP 不准 `style="…"` 屬性：樣式一律走 class，或 `h(tag, {style: {…}})`（物件，走 `el.style`）。
- 不准 `fetch(`、`setInterval(`、`http` 網址、內嵌程式、`onclick=` 屬性。唯一的例外是 `dom.js` 的 SVG 命名空間字串
  （`createElementNS` 要用，不會連線；SYNOX 的畫面掃描也放行它）。工具庫的 `tests/test_apps_common.py` 另外擋 `innerHTML`、
  `sessionStorage`、`localStorage`、`location.href`、`cookie`…；`"/api/"` 對記帳放行（只是 routes.js 的鍵）。
- 去識別化檢查（`tests/money/test_deid.py`）明確白名單的誤報：`style.css` 的 `.env`（「錢怎麼分的每一格」的 class，不是 .env 檔）、上面那個 SVG 命名空間。
- 捐贈：英文欄位名、函式名沿用 `tithe_rate`、`tithe_amount`、`titheDone`…（契約不改名）；畫面與給人看的字一律寫「捐贈」。
- 危險動作（`rm`、`revert`、`book_new`）SYNOX 會先跳確認框；畫面原本自己的確認可以留。手機入口做不了危險動作與匯出
  （所以手機上「復原」按了會說不行）。

## 11. 給 AI

- `pack.json` 的 `agent` 區塊（SYNOX 現在會忽略、嚴格安裝也不拒）：索引一行、觸發字、手冊主題檔
  `skills/{record,categories,transfer,iou,debt,budget,review,ingest}.md`（W7 寫）。
- 給 AI 的指令（13 個）：`add`、`in`、`transfer`、`edit`、`undo`、`list`、`show`、`summary`、`check`、`report`、`review_save`、`ingest`、`month`。

## 12. 跟 SYNOX 本體的對齊

- `pack.json` 的 `python` 寫 `{"module": "pack", "package": true}`：`pack.py` 以套件身分載入（SYNOX 的多檔工具，`synox/packs/pkgload.py`）。
  子模組一律從 `.py` 原始碼編譯、有鎖時每個檔對雜湊、不加 `sys.path`；每一段路徑都要是 Python 識別字（檔名、資料夾名不能有 `-`）。
  沒有這個功能的舊 SYNOX 載不起來（`pack.py` 用相對 import）。
- `tests/money/conftest.py` 的 `load_money()` 用標準的套件載入方式模擬（快）；`test_contract.py` 最後一條用真的 SYNOX loader 載一次。
- `requires.synox` 現在寫 `>=0.2`：多檔工具支援（`python.package`）已在 SYNOX 的開發線，但還沒有發佈的版本號（SYNOX 仍是 0.2.0）。SYNOX 發佈含它的那一版之後，改成那一版。
- SYNOX 一個畫面最多 40 個讀、40 個動作、10 個匯出（`manifest.APP_MAX_NAMES`）：現在是 27 個讀、39 個動作、2 個匯出。
  要再加動作，先想能不能併進現有的（「沒給 id＝新增」那種）。

## 13. 測試

- `python -m pytest tests/money/test_contract.py`：這份契約。
- `tests/money/conftest.py`：`make_money(per_user=True, now=TODAY)`（假的 pack＋工具服務）、`load_money()`、
  兩個假使用者 `OWNER`、`OTHER`、假的今天 `TODAY`（2030-01-15）。測試資料一律用假的（「範例便當店」「現金」「120」）。
- JS 只用 `node --check` 與純 node 的小腳本；不開瀏覽器、不開埠。腳本放 `tests/money/js/`（共用的假 DOM 在 `prelude.mjs`；`state_text_check.mjs`、`render_home.mjs`、`money_format_check.mjs`），Python 測試用 `conftest.run_node_check` 跑。
- 閘門：`test_isolation.py`（兩個人交錯跑每個讀、動作、指令；A 做完所有動作 B 的檔不變；兩條執行緒各記 200 筆；沒設資料夾引擎一定丟錯）、`test_deid.py`（去識別化）、`test_decimals.py`（小數位數兩條路、時差、小數帳本不開撲滿）、`tests/test_gates.py`（記帳的 import、碰檔案只准在哪些檔、隔離的靜態規則）。
