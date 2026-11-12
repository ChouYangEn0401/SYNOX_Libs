# 第三方 MCP 伺服器審查（2026-11-09）：Google 日曆、Todoist、Notion

維護者的原則（2026-11-10）：別人已經做過的 skills 或 MCP 直接採用，先 clone 下來檢查程式碼安不安全。

## 結論先講

| | Google 日曆 | Todoist | Notion |
|---|---|---|---|
| 審的 repo | nspady/google-calendar-mcp（社群，最多星） | Doist/todoist-ai（官方；已改名 todoist-mcp） | makenotion/notion-mcp-server（官方，本機版） |
| 判定 | **可用但要注意** | **安全可用**（建議用它的官方遠端端點） | **本機版不要用**；改用官方遠端 `mcp.notion.com`（可用但要注意） |
| 一句話 | 程式乾淨，但要自己申請 Google OAuth、登入時在 3500–3505 開一個**對所有網卡**的埠、token 明文放在使用者資料夾 | 官方、天天在更新、npm 有 provenance、沒有危險呼叫；同一份程式就是 `ai.todoist.net/mcp` 在跑的 | Notion 自己說本機版「不再積極維護、可能下架」；剛修完路徑穿越（10/28）與 token 寫進 log（3/4） |
| 建議 SYNOX 怎麼接 | 先用本機 `calendar` pack ＋ `/cal_ics` 匯出；真的要同步再接（見下面兩條路） | `kind: mcp` 接 `https://ai.todoist.net/mcp`（OAuth） | `kind: mcp` 接 `https://mcp.notion.com/mcp`（OAuth） |

**最重要的一點**：三個都不建議「在這台電腦上 `npx` 跑本機版」——那等於執行網路上抓來的 Node 程式（加上 100–180 個相依套件），
違反家規「下載來的東西一律當資料，不當程式跑」。SYNOX 的 `kind: mcp` 本來就設計成接 **URL**（遠端），
所以 Todoist 與 Notion 走官方遠端端點就不用在本機跑任何第三方程式；Google 日曆的官方遠端端點還沒驗證（見最後）。

> 審查方式：clone 到維護者電腦上一個不在任何專案裡的隔離資料夾（`_sandbox\synox_mcp_audit\`，有 `_高危險_勿執行.md`），
> **只讀原始碼**：沒有 `npm install`、沒有執行任何腳本、沒有 import。相依數量是用一支自己寫的小程式把 `package-lock.json` 當 JSON 讀出來算的。
> 「開放的安全 issue」是 `gh issue list --search "security OR vulnerability OR CVE OR injection"`。

## 審查表

| 項目 | nspady/google-calendar-mcp | Doist/todoist-mcp | makenotion/notion-mcp-server |
|---|---|---|---|
| 審的 commit | `29a7090`（2026-10-29），v2.8.1 | `34273fd`（2026-11-09），v13.11.2 | `cf16e5c`（2026-10-29），v2.6.1 |
| 誰維護 | 個人（nspady）＋社群；1,213 星 | Doist 官方；563 星；npm `publishConfig.provenance: true` | Notion 官方；4,702 星；**README：不再積極維護、可能下架** |
| 語言／執行環境 | TypeScript，Node ≥ 20 | TypeScript，Node ≥ 24、npm ≥ 11 | TypeScript，Node 22（Docker） |
| 授權 | MIT | MIT | MIT（`src/openapi-mcp-server/` 是 snaggle-ai/openapi-mcp-server 的 MIT fork） |
| 直接相依（runtime） | 6：`@modelcontextprotocol/sdk`、`gaxios`、`google-auth-library`、`googleapis`、`open`、`zod` | 9：`@doist/mcp-core`、`@doist/todoist-sdk`、`@modelcontextprotocol/ext-apps`、`date-fns`、`dompurify`、`dotenv`、`express`、`fractional-indexing`、`zod`（全部釘死版本） | 11：`@modelcontextprotocol/sdk`、`axios`、`express`、`mustache`、`node-fetch`、`openapi-client-axios`、`openapi-schema-validator`、`openapi-types`、`which`、`yargs`、`zod` |
| 相依總數（lockfile） | runtime 166／全部 293 | runtime 100／全部 1,079 | runtime 182／全部 387 |
| 安裝時會跑的腳本 | 自己沒有 `postinstall`；相依裡只有 esbuild、fsevents（都是開發用） | 自己沒有 `postinstall`；**`prepare: lefthook install`**（從 git 裝時會改 git hooks）；esbuild、lefthook、fsevents（開發用） | 自己沒有；esbuild、fsevents（開發用） |
| 憑證怎麼存 | 自己申請的 GCP OAuth client（`gcp-oauth.keys.json`）；refresh token **明文 JSON** 在 `~/.config/google-calendar-mcp/tokens.json`（`mode 0o600`，Windows 上等於沒設）；原子寫入、壞檔改名不刪 | **不存**：`TODOIST_API_KEY` 環境變數（或工作目錄的 `.env`，dotenv）；遠端版走 Todoist OAuth（`data:read_write`） | **不存**：`NOTION_TOKEN` 或 `OPENAPI_MCP_HEADERS` 環境變數（整個 integration 的權限）；HTTP 模式另有 `AUTH_TOKEN`（沒給就隨機產生） |
| 權限範圍 | `https://www.googleapis.com/auth/calendar`（**所有日曆完整讀寫**，不是只有 `calendar.events`） | 整個 Todoist 帳號讀寫 | integration 被分享到的頁面（手動分享） |
| 連到哪裡 | `www.googleapis.com`、`oauth2.googleapis.com`（可用 `GOOGLE_CALENDAR_API_BASE_URL` 改到自己的 proxy）；本機 `localhost:3500–3505`（登入） | 只有 Todoist API（`@doist/todoist-sdk`；可用 `TODOIST_BASE_URL` 改） | 只有 `api.notion.com`（OpenAPI spec 寫死的 server）；啟動時打一次 `/v1/users/me` 印出 integration 設定頁連結 |
| 開埠 | **登入時的 auth server 在 3500–3505 `listen(port)` 沒指定 host ＝所有網卡**（有 PKCE＋32 bytes state 防 CSRF）；HTTP 傳輸預設 127.0.0.1＋檢查 Origin | stdio 版不開埠；HTTP 版預設 127.0.0.1，Host／Origin 白名單防 DNS rebinding | stdio 版不開埠；HTTP 版預設 127.0.0.1，非 loopback 會警告；有 bearer token |
| exec／eval／shell | 原始碼沒有 `child_process`／`eval`／`new Function`；`open` 套件會開預設瀏覽器（它內部會叫系統指令） | 沒有 | 沒有（`parser.ts:353` 一行**被註解掉**的 `eval`） |
| 本機檔案 | 只寫自己的 token 檔 | 沒有 | **2026-10-28 #373 起拒絕檔案上傳、拿掉讀本機檔**（之前有路徑穿越：AI 給的路徑可以讀任意檔，issue #237／#222） |
| 遙測 | 沒有 | 沒有第三方遙測；每個 API 請求帶 `doist-platform`、`doist-version`、`session-id`、`mcp-tool`（用了哪個工具）header 給 **Todoist 自己** | 沒有 |
| 開放的安全 issue | 0（全部開放 issue 1 個） | 0（全部 7 個） | #238 頁面內容的 prompt injection、#237／#222 路徑穿越與 token log（**程式已修，issue 沒關**）、#267 想要寫入政策；全部開放 issue 163 個 |
| repo 裡給 AI／編輯器自動載入的設定 | `.claude/`（agents、commands、skills）、`.cursorignore` | **`.claude/settings.json` 有 hooks**（每次提示都 `node .agents/scripts/guard.ts`）、`.codex/`、`.vscode/`、`.mcp.json` | 沒有 |
| 工具數 | 13（`list-calendars`、`list-events`、`search-events`、`get-event`、`list-colors`、`get-freebusy`、`get-current-time`、`create-event`、`create-events`、`update-event`、`respond-to-event`、`delete-event`、`manage-accounts`）；可用 `ENABLED_TOOLS` 只開一部分 | 約 48（`find-tasks`、`add-tasks`、`complete-tasks`、`delete-object`…），每個有 `readOnlyHint`／`destructiveHint` | 24（從 OpenAPI 產生，`API-post-search`、`API-post-page`、`API-delete-a-block`…；GET 標 readOnly） |

## 每一個的判定與理由

### Google 日曆：nspady/google-calendar-mcp → 可用但要注意

- 好的：程式小（48 個 .ts）、沒有危險呼叫、token 原子寫入、OAuth 有 PKCE＋state、HTTP 模式有 Origin 檢查、可以用 `ENABLED_TOOLS` 只開需要的工具。
- 要注意：
  1. **一般人裝不起來**：要自己到 Google Cloud Console 建專案、開 Calendar API、建 OAuth client、下載 `gcp-oauth.keys.json`；
     app 停在「測試」狀態時 token **7 天就過期**。這對 SYNOX 的「一般人」目標太重。
  2. **登入時開埠**：auth server 對所有網卡聽 3500–3505（這台電腦的家規不開網路埠）。
  3. **token 不在 SYNOX 保管箱裡**：明文放 `~/.config/google-calendar-mcp/tokens.json`，而且是整個 `calendar` scope。
  4. 本機跑＝執行 166 個 npm 套件。
- 什麼時候可以用：在雲端／容器裡跑它的 HTTP 模式（`--transport http`，給 `GOOGLE_CALENDAR_MCP_TOKEN_PATH`、`ENABLED_TOOLS`），SYNOX 用 `kind: mcp` 接那個 URL。

### Todoist：Doist/todoist-mcp → 安全可用

- 官方、MIT、今天還在發版、npm provenance、相依全部釘死版本、沒有 exec／eval、不存 token、只連 Todoist。
- `session-id`、`mcp-tool` 這些 header 是送給 Todoist 本身的用量統計，不是第三方遙測；介意的話這是唯一的資料外流點（而且本來就連 Todoist）。
- **建議接官方遠端 `https://ai.todoist.net/mcp`**（README 寫的主要網址，OAuth、Streamable HTTP）：不用在本機跑 Node 24。
- 不要在 clone 下來的資料夾裡開 Claude Code（`.claude/settings.json` 的 hooks 會執行它的 `guard.ts`）。

### Notion：makenotion/notion-mcp-server → 本機版不要用；官方遠端可用但要注意

- 本機版：Notion 在 README 最上面說**不再積極維護、可能下架**，只支援遠端版；163 個開放 issue；
  一年內修過兩個真的安全問題（路徑穿越可讀任意檔、token 印在 log），issue 都還沒關。工具是從 OpenAPI 機械產生的，24 個、很耗 token。
- 遠端版 `https://mcp.notion.com/mcp`：OAuth、照使用者自己的 Notion 權限、Notion 負責維護。**風險是 prompt injection**（#238：頁面內容裡夾指令）——
  SYNOX 架構 v1 §4 第 5 關把工具回傳包在隨機 nonce 分隔符、寫入類一律核准卡，正好是對策；但這是「別人的伺服器看得到你的 Notion」，本來就是。

## SYNOX 怎麼包成 `kind: mcp`（pack.json v2）

照 SYNOX `manifest.py`：`mcp.tools_allow` 不能空、每個允許的工具都要在 `mcp.effects` 寫 READ／WRITE／DANGER（架構 v1 §4：沒寫＝DANGER）；
`kind: mcp` 不能附程式檔（第 2 關）。**SYNOX 這一版的 `kind: mcp` 只登記、還不連線**（`/todoist` 會說「還沒接上」），下面是接上之後的宣告。
原則：只開日常要用的；刪除一律 DANGER（AI 要做先出核准卡）；帳號管理、改分享、搬專案、改別人的指派這類不開。

### Todoist（遠端官方）

```json
{
  "name": "todoist", "title": "✅ Todoist", "summary": "看今天的待辦、加待辦、完成（官方遠端 MCP）",
  "version": "0.1.0", "kind": "mcp", "group": "todoist", "access": "owner",
  "capabilities": ["network"],
  "auth": { "type": "oauth", "oauth": { "mcp_url": "https://ai.todoist.net/mcp", "scopes": ["data:read_write"] } },
  "mcp": {
    "url": "https://ai.todoist.net/mcp",
    "tools_allow": ["get-overview", "find-tasks", "find-tasks-by-date", "find-projects", "find-labels", "search", "user-info",
                    "add-tasks", "update-tasks", "complete-tasks", "uncomplete-tasks", "reschedule-tasks", "add-comments",
                    "delete-object"],
    "effects": { "get-overview": "READ", "find-tasks": "READ", "find-tasks-by-date": "READ", "find-projects": "READ",
                 "find-labels": "READ", "search": "READ", "user-info": "READ",
                 "add-tasks": "WRITE", "update-tasks": "WRITE", "complete-tasks": "WRITE", "uncomplete-tasks": "WRITE",
                 "reschedule-tasks": "WRITE", "add-comments": "WRITE", "delete-object": "DANGER" }
  },
  "skill": "SKILL.md", "storage": "vault"
}
```

不開：`manage-assignments`、`project-move`、`project-management`、`import-project-template`、`set-user-status`、`find-project-collaborators`、
`get-workspace-insights`、`view-attachment`（會去抓附件網址）、`update-projects`／`update-labels`／`update-filters` 這類改結構的。
（Doist 自己把 `update-tasks`、`complete-tasks`、`reschedule-tasks` 標 `destructiveHint`；SYNOX 這裡當 WRITE，因為改的是使用者自己的待辦、做得回來。
擁有者想更保守就改成 DANGER。）

### Notion（遠端官方）

遠端版的工具名稱跟本機版不同、要接上之後用 `tools/list` 看；**照下面的分類原則填，名字以遠端實際回的為準**：

```json
{
  "name": "notion", "title": "🗂 Notion", "summary": "搜尋頁面、讀頁面、在資料庫加一筆（官方遠端 MCP）",
  "version": "0.1.0", "kind": "mcp", "group": "notion", "access": "owner",
  "capabilities": ["network"],
  "auth": { "type": "oauth", "oauth": { "mcp_url": "https://mcp.notion.com/mcp" } },
  "mcp": {
    "url": "https://mcp.notion.com/mcp",
    "tools_allow": ["notion-search", "notion-fetch", "notion-create-pages", "notion-update-page"],
    "effects": { "notion-search": "READ", "notion-fetch": "READ", "notion-create-pages": "WRITE", "notion-update-page": "DANGER" }
  },
  "skill": "SKILL.md", "storage": "vault"
}
```

分類原則：搜尋、讀＝READ；新增頁面／資料庫一筆＝WRITE；改既有頁面內容、搬移、刪除＝DANGER；改分享、改 workspace 設定＝不開。
（本機版若真的要用，對照名稱是 `API-post-search`、`API-retrieve-a-page`、`API-retrieve-page-markdown`、`API-query-data-source`＝READ，
`API-post-page`、`API-create-a-comment`＝WRITE，`API-patch-page`、`API-update-page-markdown`、`API-patch-block-children`、`API-update-a-block`、
`API-delete-a-block`、`API-move-page`＝DANGER；`API-update-a-data-source`、`API-create-a-data-source` 不開。）

### Google 日曆（兩條路，要擁有者選）

| 選項 | 一句話 | 建議 |
|---|---|---|
| A. 先不接，用本機 `calendar` pack＋`/cal_ics` | 什麼帳號都不用；要放進 Google 日曆就匯入 .ics | ✅ **建議（預設）** |
| B. Google 官方遠端 Calendar MCP（SYNOX connectors 設計稿寫的 `calendarmcp.googleapis.com`） | 不用跑別人的程式、OAuth 在 Google；**這次沒驗證這個端點**（沒有程式可以審，要擁有者在別台電腦或雲端試） | 驗證過再接 |
| C. nspady 的伺服器在雲端／容器跑 HTTP 模式，SYNOX 接那個 URL | 程式審過了；但要自己的 GCP OAuth client、token 7 天過期（測試狀態） | 只給工程師 |

選 B 或 C 時的宣告（工具名用 nspady 的；B 的名字以實際回的為準）：

```json
{
  "name": "google-calendar", "title": "📅 Google 日曆", "summary": "看行程、加行程（MCP）",
  "version": "0.1.0", "kind": "mcp", "group": "gcal", "access": "owner",
  "capabilities": ["network"],
  "auth": { "type": "oauth" },
  "mcp": {
    "url": "https://<你的 MCP 端點>/mcp",
    "tools_allow": ["list-calendars", "list-events", "search-events", "get-event", "get-freebusy", "get-current-time",
                    "create-event", "update-event", "respond-to-event", "delete-event"],
    "effects": { "list-calendars": "READ", "list-events": "READ", "search-events": "READ", "get-event": "READ",
                 "get-freebusy": "READ", "get-current-time": "READ",
                 "create-event": "WRITE", "update-event": "WRITE", "respond-to-event": "WRITE", "delete-event": "DANGER" }
  },
  "skill": "SKILL.md", "storage": "vault"
}
```

不開：`manage-accounts`（會開登入流程、加帳號）、`create-events`（一次建很多個；要的話標 DANGER）、`list-colors`（沒用）。
C 的話伺服器那邊也設 `ENABLED_TOOLS` 同一份清單（兩道門）。

## 沒做的、要擁有者決定的

- 這次**沒有執行**任何一個伺服器（家規），所以「實際行為」只看程式碼；遠端端點（`ai.todoist.net`、`mcp.notion.com`、Google 的）沒有程式可審，安全性靠廠商。
- Google 官方遠端 Calendar MCP 的網址與工具名沒有驗證。
- SYNOX `kind: mcp` 的 OAuth（loopback 回呼會開埠 vs device code）還是 SYNOX connectors README 裡的待決事項。
- clone 下來的三個資料夾留在隔離區；不需要時可以整個刪掉。
