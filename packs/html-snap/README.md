# 🖼️ 網頁轉長圖（html-snap）

> **alpha（0.1.0-alpha.1，尚未在 SYNOX／Telegram 實機驗證）**：工具本身在 Windows＋Microsoft Edge 直接跑過；
> 裝進 SYNOX、由 `runtime.python: "auto"` 建環境、從聊天叫它，還沒有真的試過。功能是 MVP，「目前不完善的地方」照實寫在最後。

把**網址**或**你資料夾裡的 .html 檔**截成一整張**不截斷**的長圖（PNG 或 JPEG），存到你的資料夾。
做法：用 Playwright 開這台電腦上的瀏覽器（Edge → Chrome → Playwright 內建 Chromium），捲到底觸發延遲載入、等字型，
頁面太長就分片截圖，再用 Pillow 拼成一張。

這是工具庫第一個 **kind=cli** 工具：SYNOX 每次開一個子程序跑 `tool.py`（用附在旁邊的 `synox_sdk.py` 的 `@tool`），
設計與取捨見 `docs/adr/0014-first-cli-tool-html-snap.md`。

## 指令

| 指令 | 做什麼 | 例子 |
|---|---|---|
| `/snap <網址>` | 截網頁（http／https） | `/snap https://example.com` |
| `/snap file=<檔名>` | 截你資料夾裡的 .html | `/snap file=pages/report.html` |
| 選項（可以一起寫） | `width=` 寬度 px、`scale=` 倍率、`wait=` 再等幾秒、`selector=` 只截一塊、`dark=yes` 深色、`format=jpeg`、`quality=` JPEG 品質 | `/snap file=report.html width=800 dark=yes` |
| `/snap_settings` | 看設定 | |
| `/snap_set <名稱> <值>` | 改設定；值寫 `default` 回到預設 | `/snap_set width 1024`、`/snap_set format jpeg` |
| `/snap_status` | 健康檢查：playwright、pillow 裝了沒、找得到哪個瀏覽器（SYNOX 的 `health.check` 也用它） | |
| `/snap_list` | 最近截的 10 張圖 | |

回覆會寫：存在哪（相對與完整路徑）、圖的尺寸（px）、檔案大小、用了哪個瀏覽器；被截短、網站回 4xx／5xx 也會寫。

## 設定（`/snap_settings` 看、`/snap_set` 改）

| 名稱 | 預設 | 範圍 | 意思 |
|---|---|---|---|
| `width` | 1280 | 320～3840 | 視窗寬度（CSS px） |
| `scale` | 2 | 0.5～4 | 倍率（2＝高解析；輸出寬＝width×scale） |
| `format` | png | png／jpeg | 輸出格式 |
| `quality` | 90 | 30～100 | JPEG 品質 |
| `wait` | 1 | 0～30 秒 | 載入、捲完之後再等幾秒 |
| `max_height` | 20000 | 1000～100000 | 最多截幾個 CSS px 高（另外還有總像素上限，見「安全」） |
| `browser` | auto | auto／edge／chrome／chromium | 先試哪個；試不到照 Edge → Chrome → 內建 Chromium 往下 |
| `timeout` | 60 | 10～300 秒 | 開網頁最多等多久 |
| `dark` | 否 | 是／否 | 預設用深色模式（`prefers-color-scheme: dark`；網頁自己沒寫深色樣式就看不出差別） |

## 資料存哪

都在你的資料夾（SYNOX 的 `{user_data_dir}`：個人版是 SYNOX 的 `data/`，伺服器版是 `data/users/<你>/`）底下的 `html-snap/`：

- `html-snap/settings.json`：設定（一般 JSON，**沒有加密**；裡面只有上表的值）
- `html-snap/shots/`：截好的圖，檔名 `<網站或檔名>-<日期時間>.png`

要截的本機 .html 也要放在你的資料夾裡（`file=` 寫相對路徑）。

## 安全

- 只收 `http://`、`https://`，或你資料夾裡的 `.html`／`.htm`（`file=`、沒有開頭的 `report.html`、指到你資料夾的 `file://`）。
  `javascript:`、`data:`、`chrome:`、`ftp:`、指到別處或別台電腦的 `file://`、網路路徑（`\\主機\…`）一律拒絕。
- 瀏覽器的每個請求都再過一次濾網：截網址時不准讀任何本機檔；截本機 .html 時只准讀**那個 .html 所在的資料夾（和子資料夾）**。
- 大小上限：高度最多 `max_height`、整張最多 1.2 億像素（約 360 MB 記憶體）、JPEG 一邊最多 65000 px；超過就只截前面那段，回覆裡會說。
  每片截圖不超過 Chromium 單張上限（約 16000 裝置像素）。
- 超時：開網頁照 `timeout`；整個指令 SYNOX 最多等 240 秒。
- 不會自動下載瀏覽器。找不到時回一句修法（裝 Edge／Chrome，或執行回覆裡那一行 `python -m playwright install chromium`）。
- 只給擁有者（`access: owner`）：它會開瀏覽器、會連網。

## 需要什麼

- SYNOX 安裝時照 `requires.pip` 建 venv（`runtime.python: "auto"`）：`playwright==1.65.2`、`greenlet==3.5.6`、`pillow==13.0.0`。
  這是 **T2** 工具：安裝時要輸入發行者 `SYNOX_Libs`。版本有釘死，**沒有附雜湊**（理由見 ADR-0014）。
- 這台電腦有 Microsoft Edge 或 Google Chrome（或自己裝 Playwright 內建 Chromium）。

## 目前不完善的地方（alpha）

- **圖不會直接傳進聊天**：kind=cli 的工具現在只能回文字，所以回覆的是檔案位置。要 SYNOX 讓 cli 工具也能回圖才做得到。
- 網頁比視窗矮時，圖的高度至少是 900 px（下面是空白）。
- 固定在畫面上的東西（置頂選單、浮動按鈕）在長圖裡可能出現好幾次。
- 一直有請求的網頁（廣告、輪詢）最多等 15 秒「網路安靜」就照樣截。
- 要登入才看得到的網頁截不到（每次都是全新、沒有登入的瀏覽器）。
- 網址可以是區域網路或本機的位址（例如家裡路由器的頁面）；只給擁有者用，所以沒有擋。
- 沒有刪舊圖的指令：`html-snap/shots/` 要自己清。
- 伺服器版若替這個工具開了沙盒，瀏覽器可能開不起來（還沒試過）。
