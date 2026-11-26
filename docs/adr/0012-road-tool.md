# ADR-0012：路況工具（road）——國道開放資料免金鑰，起訖行車時間的供應商可選

- 狀態：**已採用（MVP）**（2026-11-26：`packs/road` 0.1.0-alpha.1，尚未實機驗證）。
- 範圍：新工具 `packs/road`。不改 SYNOX 核心。
- 讀者：之後接手開發的人與 Claude。

## 1. 一句話

讓 SYNOX 回答「國道現在塞不塞、這段要開多久、有沒有車禍施工、從 A 開到 B 要多久」，以及一次查使用者存的常用路線。
先求功能「有」（MVP），不完善的地方在 README「目前不完善」照實寫。

## 2. 資料來源

| 功能 | 來源 | 金鑰 | 為什麼選它 |
|---|---|---|---|
| 國道路段、車速、旅行時間、壅塞 | 高速公路局 `tisvcloud.freeway.gov.tw/history/motc20/` 的 `Section.xml`（路段基本資料，一天更新一次）、`LiveTraffic.xml`（每分鐘） | 不用 | 官方、免註冊、TDX 同一份標準格式（交通資料標準 v2） |
| 事故、施工、管制、天候 | 同一處的 `LiveEvents.xml`（1968 即時事件） | 不用 | 同上；`1min_incident_data_1968.xml` 也有，但欄位是代碼，`LiveEvents.xml` 已經有中文描述 |
| 起點到終點 | TDX（client_id／client_secret 換 token → 地址轉座標 → MaaS 路徑規劃）或 Google Routes API `computeRoutes`（`TRAFFIC_AWARE`） | 要 | 使用者要求兩家都能選；國道資料只到交流道，起訖點要地圖服務 |

讀資料的程式（`parse_doc`）同時收 XML、JSON、gzip 壓縮過的（看開頭 `1f 8b`），XML 與 JSON 都先轉成同一種 dict 再取欄位，
之後換成 TDX 的 JSON 版或壓縮檔不用改取欄位的地方。解壓縮限制大小（防壓縮炸彈），XML 有 DTD／ENTITY 一律不讀。

## 3. 決定

| # | 題目 | 決定 |
|---|---|---|
| 1 | 權限 | `access: owner`、`capabilities: ["network"]`、只用 `requests`（工具庫「接外面服務」的規矩，ADR-0006 第 4 節） |
| 2 | 金鑰 | `auth.fields`（`tdx_client_id`、`tdx_client_secret`、`google_api_key`，全部 `required: false`：國道路況不用金鑰也要能用）；SYNOX 收、存在保管箱，工具只用 `pack.secret(名字, user)` 讀。不存進 `pack.store`、不放進回覆、不寫 log；對方的錯誤訊息裡萬一夾帶金鑰先遮成 `***`。聊天裡不收金鑰（Telegram 會留紀錄），沒設時回一句人話教去哪申請、在網頁「連接」或 `python -m synox connect road` 填 |
| 3 | 設定 | 存在 `pack.store.of(user)` 的 `settings`：`provider`（auto／tdx／google）、`road`（預設國道）、`dir`（預設方向）、`unit`（km／mi）、`lines`（最多列幾段）、`events`（路況後附事件）。`/road_settings` 看、`/road_set <鍵> <值>` 改（中文鍵也收，值寫「預設」回預設）；網頁另有唯讀面板 |
| 4 | 常用路線 | `routes`：名字 → {kind: freeway｜eta, spec: 原話}；存的時候先驗格式。`/road_routes` 一次查全部，國道資料只抓一次，一條出錯不影響其他條 |
| 5 | 路段資料快取 | `Section.xml` 約 300 KB、一天才變一次：精簡後存在這個人自己的 store（key=`sections`），24 小時內不重抓。模組層級不放任何快取（ADR-0006 M4） |
| 6 | 方向與路段順序 | 照里程中點排：南、東向遞增；北、西向遞減。只給起點終點沒給方向時，哪個方向對得上就用哪個 |
| 7 | 沒資料的路段 | 速度 -99 之類當沒資料：用 90 km/h 估時間，回覆標出幾段沒資料，不默默算進去 |
| 8 | TDX 的開車模式 | TDX 的路徑規劃以大眾運輸為主；開車用的參數集中在 `TDX_DRIVE` 一處，回應欄位用幾個可能的名字找（`travel_time`／`duration`、各段 `travel_summary`）。沒拿真的金鑰驗過，README 標「目前不完善」 |

## 4. 測試

`tests/test_road.py` 全部用假 HTTP（假高公局資料含 gzip、假 TDX、假 Google）：路段與方向寫法、快取、事件、錯誤一句話、
金鑰不外洩（回覆、log、store）、供應商切換、常用路線、設定、AI 工具清單；
伺服器版（ADR-0006 第 6 節）：成員打了跟不存在一樣、`pack.store.of` 與 `pack.secret` 每人一份、面板只回自己的、落地是密文、
`data/` 根目錄沒有共用檔。

## 5. 之後

- 拿真的 TDX 金鑰驗開車參數；不行就改用 TDX 的其他資料或只留 Google。
- 交流道暱稱、省道（公路局）路況、定時推播（現在可以用 `/remind_add` 自己排 `/road_routes`）。
