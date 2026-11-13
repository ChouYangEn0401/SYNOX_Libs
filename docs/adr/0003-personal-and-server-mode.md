# ADR-0003：個人版與伺服器（多人）版——工具的「多人契約」與入口標準

- 狀態：提議中（2026-11-13；審查已做完，實作待辦見最後一節）
- 範圍：`packs/` 全部 9 個工具、`tests/`、`scripts/build.py`
- 對照的 SYNOX：`src/synox/packs/loader.py`、`manifest.py`、`secrets.py`、`connect.py`、`policy.py`、`web/server.py`、`docs/pack-format.md`

## 背景

SYNOX 有兩種用法，寫在 `config.json` 的 `mode`：

| | 個人版 `personal`（預設） | 伺服器版 `server`（多人） |
|---|---|---|
| 誰在用 | 只有擁有者一個人 | 擁有者＋成員＋訪客（陌生人自動登記成訪客） |
| `pack.store.of(user)` | 回共用的那一份 `data/<name>.json.enc` | 回這個人自己的 `data/users/<id>/<name>.json.enc`（金鑰由主金鑰＋id 衍生） |
| `pack.path_of(user, …)` | `data/<name>/files/` | `data/users/<id>/<name>/files/` |
| `access: member` | 等於 `owner` | 成員以上看得到 |
| `access: owner` | 只有擁有者 | 只有擁有者（成員打了＝「未知指令」，AI 工具清單也沒有） |
| 沒鎖／第三方的 python 工具 | 照常 | 整組只給擁有者（要擁有者安裝時選「核心」才開給成員） |

工具頁現在把工具分兩種標：本機五個（notes、todo、money、calendar、bullet）標「不連網／伺服器版也能用」；
接外面服務的四個（notion、todoist、google-calendar、apple-reminders）標「只用模擬測過／單人用」。
維護者問：這樣標對嗎？兩種模式下工具會不會互相影響？入口（`pack.json`、`install`、面板）有沒有一致？

這份 ADR 回答這三件事，並把「多人版要守的規矩」定成可以用測試檢查的契約。

## 審查結論（先講結果）

**標籤認同，不用改方向，只要把字寫準一點。**

- 本機五個「伺服器版也能用」：**對**。實際用真的 SYNOX 開伺服器版、擁有者＋成員各寫一筆驗過：
  指令看不到對方的、面板 API 看不到對方的、落地檔是密文而且各在自己的 `data/users/<id>/`、`/cal_ics` 送完沒有留檔。
- 外部四個「單人用」：**對，而且現在應該維持**。它們是 `access: owner`，伺服器版成員打了是「未知指令」、AI 也拿不到。
  程式其實已經用 `pack.store.of(user)` 存 token（每人一份），差的不是分資料，是「成員怎麼安全地把 token 交進來」（見政策一節）。
- 「只用模擬測過」：**對**（全部是假的 HTTP）。這是 `stage: alpha` 的事，跟單人／多人無關，兩個標籤不要混成一個意思。

建議的字（給工具頁／README）：

| 現在 | 建議 | 為什麼 |
|---|---|---|
| 伺服器版也能用 | 伺服器版每人一份 | 講清楚「怎麼能用」：資料分開 |
| 單人用 | 只給擁有者 | 「單人用」會被讀成「伺服器版不能裝」；其實能裝，只是只有擁有者用得到 |
| 只用模擬測過 | 不變 | 對 |

**單人版零影響**：這次審查沒有發現任何一條多人規矩會改到個人版的行為（保證方式見下面）。

## 決定

### 1. 兩種模式的定義（工具作者要記的）

- 工具**不准自己判斷模式**（不讀 `config.mode`、不寫 `if pack.server:`）。同一份程式兩邊都對，靠的是只用 SYNOX 給的三個「分人」的入口：
  `pack.store.of(user)`、`pack.path_of(user, …)`、`pack.secret(name, user)`。個人版它們回共用的那一份，伺服器版回這個人的。
- 誰能用由 `pack.json` 的 `access` 決定，工具程式裡**不寫**「是不是擁有者」的判斷。

### 2. 多人契約（每個工具都要守；括號是怎麼檢查）

| # | 規矩 | 檢查 |
|---|---|---|
| M1 | 個人資料只經 `pack.store.of(user)`；不准裸用 `pack.store.load／save／update` | SYNOX 第 2 關（`data: user`）；`tests/test_gates.py` |
| M2 | 個人檔案只經 `pack.path_of(user, …)`；不准 `pack.path(…)`、不准自己組路徑、不准系統暫存資料夾 | 第 2 關；`test_gates` 的檔案白名單 |
| M3 | 分人的鍵只能是 `user`（handler 給的那個）或 `cb.clicker_id`；**不准用 `chat_id`** 當鍵 | `test_gates` 加靜態檢查（待辦 T3） |
| M4 | 模組層級不准放會變的狀態：沒有 dict／list／set 快取、沒有共用的 HTTP session、沒有「上一次的結果」。空資料用函式回新的（`def empty()`），不拿常數淺拷貝 | `test_gates` 加 AST 檢查（待辦 T3）；`tests/test_isolation.py` |
| M5 | 面板 route `fn(user)` 只回這個人自己的；`panel.html` 只打自己宣告的 `/api/packs/<name>/…`，用 `textContent` 不用 `innerHTML`，不用 `localStorage` | 雙人面板測試（待辦 T2）；`test_gates` 掃 panel（待辦 T3） |
| M6 | 匯出檔寫在 `path_of(user)` 底下、送出後刪掉；檔名不固定（同一個人同時匯出兩次不互踩） | 測試：送完資料夾是空的 |
| M7 | 密鑰（token、密碼、client secret）不出現在回覆、AI 工具結果、log；設定密鑰的指令一律 `ai=False` | 每個外部工具一條 log 測試（待辦 T4） |
| M8 | 「上次列出來的編號」這類暫存也算個人資料，照 M1 存 | 雙人測試 |
| M9 | `surfaces` 只有 `["dm"]`（個人資料不進頻道） | SYNOX 第 1 關（`channel`＋`data: user` 會被拒） |
| M10 | 時間：「今天」現在是伺服器那台的時區。工具不准自己猜使用者時區；之後 SYNOX 給每人時區時，只改 `now()` 一個地方 | 每個工具只有一個 `now()`（已經是這樣） |

### 3. 入口（entry）標準

**`pack.json`——這個工具庫規定一定要明寫的欄位**（SYNOX 有預設值的也要寫，讓人一眼看得出來、也讓 `build.py --check` 擋得住）：

| 欄位 | 值 | 意思 |
|---|---|---|
| `name`、`title`、`summary`、`version`、`stage` | — | 照 README |
| `kind` | `python` | — |
| `group` | 指令前綴 | — |
| `access` | 本機工具 `member`；接外面服務的 `owner`（見政策） | 誰看得到 |
| `surfaces` | `["dm"]` | 只能私訊 |
| `data` | `user` | 資料每人一份（第 2 關照它檢查 M1、M2） |
| `storage` | `vault` | 落地是密文 |
| `capabilities` | `[]`、`["filesystem:own"]` 或 `["network"]` | 沒有也要寫 `[]` |
| `requires` | 含 `synox`、`sdk` | ADR-0001 |
| `skill` | `SKILL.md` | — |
| `web` | 有面板才寫：`panel`＋`api` | — |
| `auth` | 接外面服務的才寫；`fields` 一律寫成物件 `{name, ask, secret}` | — |

**`pack.py`——進入點的形狀**

```python
def now(): ...                 # 唯一碰時鐘的地方（測試換掉）
def empty(): ...               # 新的空資料（每次都是新的）

def install(app, pack):
    g = pack.group
    def handler(api, chat_id, user, values): ...   # chat_id 只拿來回訊息；資料一律 pack.store.of(user)
    kit.command(g, "add", handler, "…", default=True, effect=WRITE, fields=[…])
    pack.web_route("today", route)                 # 有面板才有；route(user) 只回這個人的
```

- 指令一律用 `kit.command` 掛；預設指令叫 `add`（或外部工具的 `list`／`search`），讀的不寫 `effect`，改的 `WRITE`，救不回來的 `DANGER`。
- 面板 route 直接 `pack.web_route(…)`，不用 `if hasattr(pack, "web_route")`（測試的假 pack 也有這個方法）。
- 外部工具固定有 `status`、`logout` 兩個指令，都 `ai=False`。

**`panel.html`**：一個檔、不載外部資源、只 `fetch("/api/packs/<name>/<route>", {credentials: "same-origin"})`、只用 `textContent`。

### 4. 接外面服務的工具在伺服器版的政策

**現在（這一版）：維持 `access: owner`。** 伺服器版裝了，只有擁有者用得到，token 存在擁有者自己的 `data/users/<擁有者>/`；成員完全看不到。這是安全的，也不影響成員用本機工具。

**不馬上開給成員的三個理由**（都不是「分資料」的問題，那個已經好了）：

1. **密鑰現在走聊天**。notion、todoist、google-calendar 都是在 Telegram 貼 token。SYNOX 自己的規矩是「Telegram 不收 secret」
   （訊息留在 Telegram、`[ROUTE]` log 會印）。擁有者自己用是自己的風險；替成員收就不行。
2. **伺服器的管理者解得開每個人的保管箱**（每人的金鑰是主金鑰＋id 衍生的）。本機筆記是這樣，成員的 Notion／Google 授權也會是這樣——
   開放前要在說明裡講明白，讓成員自己決定。
3. **還沒對真的服務用過**（alpha）。先讓擁有者自己用穩。

**之後要開給成員的條件**（每個工具各自達成、各自升版；`access` 變寬，SYNOX 升級時會要擁有者重新核准）：

- 密鑰改走 SYNOX 管的那條路：`pack.json` 的 `auth.fields`（物件、`secret: true`）＋程式用 `pack.secret(name, user)`。
  這樣網頁工具頁會出現「連接」，每個人在網頁填自己的（伺服器版每人一份、值永遠不回給網頁、AI 帶出來會被第 4 關擋）。
- 聊天裡收密鑰的指令拿掉或只留給個人版相容；舊位置的 token 照樣認（讀的時候先看新的、再看舊的），不要逼擁有者重設。
- 有「成員 A 的 token，成員 B 拿不到、擁有者的指令也用不到」的測試。
- README 寫明第 2 點（管理者解得開）。
- 這時才把 `access` 改成 `member`、標籤改成「伺服器版每人接自己的帳號」。

google-calendar 的 OAuth（要來回貼網址）先不排，等 SYNOX 的「連接」支援 oauth 再說；apple-reminders 現在就已經不經聊天，最接近可以開。

### 5. 單人版零影響——怎麼保證

- 契約裡的三個入口在個人版回的就是原本那一份檔（`of()` 回自己、`path_of` 回 `data/<name>/files/`），**檔案位置與格式不變**。
- 工具不判斷模式，所以沒有「只在伺服器版跑的分支」會壞到個人版。
- 待辦裡補寫 `surfaces`／`data`／`capabilities` 都是寫出跟預設值一樣的值，SYNOX 讀到的結果不變（已驗：三個沒寫的工具實際值就是 `["dm"]`／`user`）。
- 測試規定每個工具兩種模式都要跑（下一節），個人版的測試一條都不准因為這份 ADR 而改期待值。
- 提醒（不是工具能解的）：**個人版改成伺服器版時，SYNOX 不會自動搬資料**（SYNOX `docs/security.md`）。原本的 `data/<name>.json.enc` 還在，
  但擁有者在伺服器版看到的是空的 `data/users/<擁有者>/`，外部服務的 token 也要重設。README 要寫一句。

### 6. 測試要求

每個工具都要有（用真的 SYNOX：`synox_bot`／`host_bot`，不是假的 store）：

| # | 測試 | 適用 |
|---|---|---|
| S1 | 雙人隔離：伺服器版兩個成員各寫一筆，每個讀的指令都只看到自己的；擁有者也看不到成員的 | 本機五個 |
| S2 | 落地：檔在 `data/users/<id>/`、內容是密文、`data/` 根目錄沒有這個工具的共用檔 | 全部 |
| S3 | 面板：`web_routes[route](A)` 沒有 B 的字 | 有面板的四個 |
| S4 | 權限：個人版陌生人打了＝未知指令；伺服器版訪客看不到；`access: owner` 的，成員看不到、AI 工具清單沒有 | 全部 |
| S5 | 個人版：同一組指令照跑，檔在 `data/<name>.json.enc` | 全部 |
| S6 | 密鑰不進 log：打設定指令之後，log 裡沒有那串值 | 外部四個 |
| S7 | token 每人一份：`store.of(擁有者)` 有、`store.of(別人)` 沒有、沒有共用檔 | 外部四個 |
| S8 | 匯出檔：送完那個人的 `files/` 是空的；別人的資料夾沒有東西 | calendar |

## 審查發現

「確定」＝讀程式＋實際跑過（或對過 SYNOX 的程式）。「可能」＝有條件才會發生，或是風險不是 bug。

### 確定

| # | 哪裡 | 發現 | 影響 |
|---|---|---|---|
| C1 | `packs/google-calendar/pack.py:786-793` | `/gcal_setup`、`/gcal_code`、`/gcal_token` 沒有叫 `kit.redact_in_logs`（todoist 有：`packs/todoist/pack.py:520`；notion 自己掛濾網：`packs/notion/pack.py:397`）。實測：`[ROUTE]` log 印出整串 client secret、refresh token、授權碼 | **兩種模式都中**。密鑰明文進 log |
| C2 | `packs/notion/pack.py:556-572,621`、`packs/todoist/pack.py:727-745,767`、`packs/google-calendar/pack.py:661-738` | 密鑰在 Telegram 聊天裡收。SYNOX 的規矩是不在聊天收（`synox/packs/secrets.py`、`synox/connect.py` 開頭說明）。只有 apple-reminders 照做（`packs/apple-reminders/pack.py:5-7`） | 個人版：既有行為、擁有者自己的風險。伺服器版：擋住「開給成員」 |
| C3 | `packs/todoist/pack.json:16`、`packs/notion/pack.json:15`、`packs/google-calendar/pack.json:16`、`packs/apple-reminders/pack.json:16` | 四個外部工具四種接法：notion＝聊天＋`synox connect`（存 `key="auth"`）；todoist＝只有聊天（`auth.fields` 寫成字串 `["token"]`，`synox connect todoist` 會說沒有欄位）；google-calendar＝聊天 OAuth；apple-reminders＝只有 `synox connect`。沒有一個用 SYNOX 管的 `pack.secret(name, user)`＋網頁「連接」。伺服器版 `synox connect --user <id>` 等於管理者替成員打密碼 | 入口不一致；開給成員前要統一 |
| C4 | `packs/calendar/pack.json`、`packs/todo/pack.json`、`packs/bullet/pack.json` | 沒寫 `surfaces`、`data`（另外六個有寫）。現在靠預設值（有 `storage` → `user`；沒寫 → `["dm"]`），實際值一樣、行為沒錯。SYNOX `docs/pack-format.md` 說「工具庫每個工具都寫了」，跟事實不符 | 沒有漏洞；宣告不一致，預設值改了會悄悄變 |
| C5 | `packs/notes/pack.json`、`packs/money/pack.json` | 沒寫 `capabilities`（todo、bullet 寫 `[]`）；`tests/test_gates.py:22` 還把「沒寫」當成期待值 | 宣告不一致 |
| C6 | `tests/test_isolation.py:6-8,22-23` | 只測 calendar、todo、bullet 三個；用假的 store、直接叫 `node.fn`，沒經過真的 loader、權限、保管箱。註解也寫了「個人版的權限只給擁有者；這裡直接呼叫」 | 檔名叫 isolation，但 notes、money 不在裡面；測不到真的分檔 |
| C7 | `tests/test_notion.py:292-299`、`tests/test_todoist.py:226-229`、`tests/test_apple_reminders.py` | 「別人看不到」只在個人版測；伺服器版「成員看不到」只有 google-calendar 有（`tests/test_gcal.py:401`）。token 每人一份只有 todoist 有（`tests/test_todoist.py:414`）。notes 完全沒有伺服器版測試 | 現在行為是對的（這次手動驗過），但沒有測試守著 |
| C8 | 全部 9 個（SYNOX 的行為：`synox/packs/loader.py:89-101`、SYNOX `docs/security.md:135`） | 個人版改成伺服器版不會搬資料：擁有者的舊資料、舊 token 留在 `data/<name>.json.enc`，伺服器版讀的是 `data/users/<擁有者>/` | 不是工具的 bug；README 沒提醒 |
| C9 | `packs/*/pack.py`（本機四個：`calendar:512`、`todo:478`、`bullet:335`、`money:601`） | `if hasattr(pack, "web_route")` 的保護是舊 SYNOX 留下的；`requires.synox >=0.2` 一定有 | 只是不一致 |

沒有發現的（特別查過）：用 `chat_id` 分資料、模組層級的快取、面板讀別人的資料、`innerHTML`、共用的匯出路徑（0.1.1 已修）、工具自己判斷擁有者。
本機五個的每個讀寫都經 `pack.store.of(user)`；bullet 的按鈕用 `cb.clicker_id`（`packs/bullet/pack.py:206`），符合 M3。

### 可能

| # | 哪裡 | 發現 | 什麼情況會出事 |
|---|---|---|---|
| P1 | `packs/calendar/pack.py:476-485` | 匯出檔名固定 `export.ics`。同一個人同時匯出兩次（Telegram 一次、AI 一次）會互相蓋、互相刪；程序在寫完到刪掉之間被關掉，會留一個**沒加密**的 .ics（只在那個人自己的資料夾） | 少見；留下的是明文 |
| P2 | `packs/calendar/pack.json:13` | `filesystem:own` 是整個工具的宣告。管理者如果把成員的能力上限裡的 `filesystem:own` 拿掉，成員會整個行事曆都看不到，不只 `/cal_ics` | 只有改過 `policy.roles` 的伺服器 |
| P3 | 每個工具的 `now()`（例 `packs/calendar/pack.py:23-25`）、`packs/notes/pack.py:32` | 「今天」「這個月」用伺服器那台的時區 | 成員跟伺服器不同時區 |
| P4 | SYNOX `synox/vault/vault.py:71-79` | 每人的金鑰由主金鑰衍生，管理者解得開全部人的資料 | 外部工具開給成員之後，等於成員把帳號授權交給管理者 |
| P5 | `packs/google-calendar/pack.py:785` | `/gcal_status` 沒有 `ai=False`（另外三個外部工具的 status 都有）；回覆帶 client_id 前 8 碼（`:750-751`） | 不是密鑰；只是不一致 |
| P6 | `packs/todoist/pack.py:32,313`、`packs/google-calendar/pack.py:52` | 模組層級的 `HTTP`／`transport`（給測試換假的）。正式環境是 `None`、每次新開；但這是 M4 不准的形狀，哪天有人放一個真的 Session 進去就變成全部人共用 | 改程式的人不小心 |
| P7 | `tests/conftest.py:88-97` | 假的 `FakePack`：`path()` 在伺服器版 `data: user` 不會像真的那樣丟錯；個人版 `path_of` 的位置跟真的不一樣 | 只用假 pack 的測試會漏掉真的會丟的錯 |

## 實作待辦（給下一位工程師；T4 先做，其餘照順序，每項一個 commit）

改了工具就升那個工具的 `version`、在 CHANGELOG 記一行。**不准改個人版測試的期待值。**

**T1　宣告補齊（不改行為）**　✅ 已完成
- `packs/calendar/pack.json`、`packs/todo/pack.json`、`packs/bullet/pack.json`：加 `"surfaces": ["dm"], "data": "user"`。
- `packs/notes/pack.json`、`packs/money/pack.json`：加 `"capabilities": []`；`tests/test_gates.py:22` 的 `ALLOWED_CAPS` 把 `None` 改成 `[]`。
- `packs/todoist/pack.json:16`：`auth.fields` 改成物件 `[{"name": "token", "ask": "Todoist API token", "secret": true}]`（跟 notion 一樣）。
  注意：改了之後 `synox connect todoist` 就會動、而且存到 `key="auth"`，所以**同一個 commit** 要讓 todoist 也讀那一份
  （照 `packs/notion/pack.py:59-65` 的 `token_of`：先 `key="auth"`、再舊位置；`/todoist_logout` 兩份都清），並加測試。
- `scripts/build.py` 的 `problems_of`：沒明寫 `access`、`surfaces`、`data`、`storage`、`capabilities` 就不打包（學 `stage_problems` 的寫法）；
  `tests/test_gates.py` 加一條 `test_declares_scope`（九個都要 `surfaces == ["dm"]`、`data == "user"`、`storage == "vault"`、三個欄位都 `*_declared`）。
  （實際：版本 calendar／todo／bullet／notes／todoist 0.1.2、money 0.2.1。SYNOX 的 Manifest 只有 `surfaces`／`data`／`capabilities`
  有 `*_declared`，`access`、`storage` 沒有，所以 build 與測試都直接看 `pack.json` 原文有沒有那個鍵；`tests/test_build.py` 另加一條「少一個就不打包」。）

**T2　雙人測試改用真的 SYNOX**　✅ 已完成（只改測試）
- 重寫 `tests/test_isolation.py`：參數化五個本機工具（加 notes、money），用 `synox_bot(mode="server")`＋兩個成員走 `bot.say`，
  檢查 S1、S2、S3、S5（同一張表也跑一次 `mode="personal"`）。保留現在那條「第一次寫入不共用 list」的回歸測試。
- 新增 `tests/test_access.py`：參數化九個工具，檢查 S4（個人版陌生人、伺服器版訪客、伺服器版成員對 `access: owner`、
  `policy.decide(via=AI)` 對成員是空的）。
- `tests/test_notion.py`、`tests/test_apple_reminders.py`、`tests/test_gcal.py`：各加一條 S7（照 `tests/test_todoist.py:414` 的寫法）。
- `tests/test_calendar.py`：加 S8。
- `tests/conftest.py:88-97`：`FakePack.path()` 在 `per_user` 而且 `data == "user"` 時丟錯；`path_of` 個人版改成 `data/<name>/files/`（P7）。
  （實際：`path()` 照做（丟 SYNOX 的 `PackPathError`），伺服器版 `path_of` 沒給人也丟。**`path_of` 個人版的位置沒改**：
  `tests/test_calendar.py::test_ics_export_is_valid_and_leaves_no_plaintext`（個人版測試）斷言假 pack 的 `data/users/<id>/…`，
  改位置就要改個人版測試的期待值，這份 ADR 不准。改成另外用真的 SYNOX 守真的位置：
  `test_calendar.py::test_personal_mode_ics_export_uses_real_folder`（`data/calendar/files/`）與 `test_isolation` 的 S5。）
- （實際，S4：伺服器版訪客打本機工具的指令，SYNOX 回的是「沒有執行此指令的權限」不是「未知指令」——群組看不到、
  `policy.decide` 不給、`/help` 沒有，只是字不一樣；測試兩種字都收。成員打 `access: owner` 的才是「未知指令」。）

**T3　契約的靜態檢查（`tests/test_gates.py`）**　✅ 已完成
- M3：`pack.py` 裡 `chat_id` 不准出現在 `store.of(`、`path_of(`、`secret(` 的參數，也不准當 dict 的鍵。
- M4：AST 掃模組層級的賦值，值是 `{}`／`[]`／`set()`／`dict()`／`list()` 的不准（常數表用 tuple 或寫進白名單）；
  `HTTP = None`、`transport = None` 這種「測試換掉用」的改成函式（T5）。
- M5：`panel.html` 不准有 `innerHTML`、`localStorage`、`sessionStorage`、`http://`、`https://`；`fetch(` 的網址只能是 `/api/packs/<自己的名字>/`。
- （實際：M4 的「常數表」不改成 tuple——全部是對照表（`_CN`、`KIND_NAMES`…），改成 tuple 會動到程式；規則改成
  「有內容的 dict／list／set 名字要全大寫、整個模組沒有地方改它、不拿它淺拷貝」，空的一律不准，模組層級的呼叫只准
  `re.compile`／`tuple`／`frozenset`，函式裡不准 `global`。`X = None` 的掛勾在 T5 之前暫列白名單 `TEST_HOOKS`。
  M5 另外要求 `fetch` 的 route 在 `pack.json` 的 `web.api` 裡。三個檢查各有一條「故意寫錯的樣子要抓到」的測試。）

**T4　google-calendar 的 log 外洩（C1；兩種模式都中，優先做）**　✅ 已完成（google-calendar 0.1.2）
- `packs/google-calendar/pack.py` 的 `install` 開頭：照 `packs/todoist/pack.py:518-520` 的寫法，
  對 `/gcal_setup`、`/gcal_code`、`/gcal_token` 各叫一次 `kit.redact_in_logs`。
- `packs/google-calendar/pack.py:785`：`status` 加 `ai=False`（P5）。
- `tests/test_gcal.py`：加 S6（`caplog`：打完三個指令，log 沒有 client secret、refresh token、code）。
  `tests/test_notion.py`、`tests/test_todoist.py` 沒有的話也各補一條 S6。
  （實際：notion、todoist 已經有 S6——`test_token_flow_vault_and_redaction`、`test_token_is_checked_encrypted_and_not_logged`——不用補；
  `tests/test_gcal.py::test_commands_ai_flags_and_effects` 原本斷言 `status` 是 `ai=True`，照 P5 改成 `ai=False` 那一組。）
- 升 `google-calendar` 版本；CHANGELOG 寫在 Fixed。

**T5　小修（不改資料格式）**
- `packs/calendar/pack.py:476`：檔名改成不固定（例如 `export-<時間>-<亂數>.ics`）；`ics` 開頭先清掉這個人 `files/` 裡留下的舊 `export*.ics`（P1）。
- `packs/todoist/pack.py:32,313`、`packs/google-calendar/pack.py:52`：把模組層級的 `HTTP`／`transport` 換成 `def new_session()`
  （notion、apple-reminders 已經是這個形狀：`packs/notion/pack.py:68`、`packs/apple-reminders/pack.py:43`），測試改成換函式（P6）。
- 本機四個：拿掉 `if hasattr(pack, "web_route")`（C9）。

**T6　文件**
- 五個本機工具的 README：加一句「伺服器版每人一份；管理者解得開」。四個外部工具的 README：加「伺服器版只有擁有者用得到（這份 ADR 第 4 節）」。
- 根目錄 README 已經在工具清單下面寫了「個人版改成伺服器版，舊資料不會自己搬」（C8）；T7 開給成員時要回來改那一段。
- 請 SYNOX 那邊改 `docs/pack-format.md:105` 那句（T1 做完它才是真的）。

**T7　之後（另開 ADR 或在這份加一節再做）：外部工具開給成員**
- 順序：apple-reminders → notion → todoist；google-calendar 等 SYNOX 的「連接」支援 oauth。
- 每個：`auth.fields` 物件化＋程式讀 `pack.secret(name, user)`（先新、後舊位置）→ 拿掉聊天收密鑰 → S7 換成「兩個成員各自的 token」→
  README 寫 P4 → `access: member`、升次版號。
- P2（`filesystem:own` 讓整個行事曆對成員消失）、P3（每人時區）要 SYNOX 那邊配合，先記著。

## 考慮過但沒選的

| 做法 | 為什麼不選 |
|---|---|
| 現在就把四個外部工具改成 `access: member` | 分資料已經好了，但密鑰還走聊天（C2）、gcal 還會進 log（C1）；成員的帳號授權不能這樣收 |
| 外部工具在伺服器版改成「全部人共用擁有者的 token」（`data: shared`） | 成員等於拿到擁有者的 Notion／Google；跟「每人自己的資料」相反 |
| 工具裡寫 `if pack.server:` 走不同路 | 兩份邏輯、個人版容易被改壞；SYNOX 的三個入口已經把差別包起來 |
| 伺服器版不准裝外部工具 | 擁有者自己在伺服器版用是安全的（實測成員看不到），沒必要擋 |
| 只靠 SYNOX 的預設值、`pack.json` 少寫幾個欄位 | 現在三個工具就是這樣，結果宣告不一致、文件跟事實不符（C4） |
