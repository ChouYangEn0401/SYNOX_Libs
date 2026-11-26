---
name: html-snap
description: 使用者想把一個網頁（網址）或自己資料夾裡的 .html 檔存成一整張不截斷的長圖（PNG／JPEG），或想看、改截圖的預設值時使用。
---

# 🖼️ 網頁轉長圖（alpha）

把整頁截成一張長圖，存在使用者資料夾的 `html-snap/shots/`，回覆裡有檔案位置和尺寸。圖不會直接出現在對話裡（工具目前只回路徑）。

## 什麼時候用

- 「把這個網頁截成長圖」「整頁截圖」→ `snap__snap`，`url` 放網址（http 或 https）。
- 截使用者自己資料夾裡的網頁檔 → `snap__snap`，`file` 放相對路徑，例如 `pages/report.html`。
- 只要頁面的一塊 → 另外填 `selector`（CSS selector，例如 `#main`）。
- 要深色版 → `dark` 填 `yes`；要 JPEG → `format` 填 `jpeg`（`quality` 30～100）。
- 要窄一點或高解析 → `width`（320～3840）、`scale`（0.5～4）；網頁載入慢 → `wait`（秒）。
- 「預設改成…」→ `snap__set`：`key` 是 width、scale、format、quality、wait、max_height、browser、timeout、dark 其中一個，`value` 寫新值，寫 `default` 是回到預設。
- 「現在設定是什麼」→ `snap__settings`。
- 「最近截了哪些」→ `snap__list`。
- 截圖失敗、說缺瀏覽器或套件 → 先用 `snap__status` 檢查，照它列的修法告訴使用者。

## 注意

- 只收 http／https 網址或使用者資料夾裡的 .html；`javascript:`、`data:`、指到別處的 `file://` 工具會拒絕，不要換個寫法再試。
- 沒有網址或檔名就先問，不要猜。
- 網頁太長時工具只截到上限並在回覆裡說明，照實轉告，不要說截完整了。
