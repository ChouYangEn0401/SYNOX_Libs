# 線上頁（claude.ai Artifact）

工具庫的公開頁放在 claude.ai 的 Artifact（一個網址一頁）。**每一頁都有本機正本** `docs/site/*.html`，
網址登記在 [`docs/artifacts.json`](artifacts.json)（產生器與測試都讀它）。SYNOX 那邊的 `docs/artifacts.json` 也列了這些頁（`repo: SYNOX_Libs`）、
SYNOX 的 `synox/packs/catalog.py` 寫死了入口頁、衣櫃頁與 9 個工具頁的網址：網址換了，兩個 repo 一起改。

## 每一頁

| 頁 | 本機檔 | 線上網址 | 怎麼來的 |
|---|---|---|---|
| SYNOX 工具庫（入口） | `docs/site/index.html` | https://claude.ai/artifact/M8zGBGy9kRtGTjNS24wBVL | **產生**：`scripts/build_site.py`（樣板 `scripts/site_index_template.html`） |
| SYNOX 衣櫃 | `docs/site/wardrobe.html` | https://claude.ai/artifact/USzGNERZ2477E7XzPaPCsR | **產生**：`scripts/build_costumes.py page`（樣板 `scripts/wardrobe_template.html`） |
| 隨手記（notes） | `docs/site/notes.html` | https://claude.ai/artifact/NpYbd3FtMAkXmASXc1pJzh | 手寫 |
| 待辦（todo） | `docs/site/todo.html` | https://claude.ai/artifact/JuKURoMhZY58fWyR4j3961 | 手寫 |
| 記帳（money） | `docs/site/money.html` | https://claude.ai/artifact/6kA9jSkwVFPPBthsCjmz6d | 手寫 |
| 行事曆（calendar） | `docs/site/calendar.html` | https://claude.ai/artifact/Fm6dXH5oiua8zPY3U5RYQ5 | 手寫 |
| 子彈筆記（bullet） | `docs/site/bullet.html` | https://claude.ai/artifact/BTdPArDtbddRk4ijNqExZ3 | 手寫 |
| Notion | `docs/site/notion.html` | https://claude.ai/artifact/B36np3VigMinvyFuyz74dJ | 手寫 |
| Todoist | `docs/site/todoist.html` | https://claude.ai/artifact/D3zgGcZ9h1LRrDLu2QD5Mw | 手寫 |
| Google 日曆 | `docs/site/google-calendar.html` | https://claude.ai/artifact/NyhRhPrRL1n9NMMbMEv3mx | 手寫 |
| Apple 提醒事項 | `docs/site/apple-reminders.html` | https://claude.ai/artifact/Jm4tdCTZdaQ1mc1waTatYp | 手寫 |

這張表跟 `docs/artifacts.json` 是同一份資料；以 JSON 為準（`python scripts/build_site.py --list-republish` 印的就是它）。
新工具沒有手寫頁時，`build_site.py` 會產生 `docs/site/<名字>.html`，第一次發佈前它還沒有網址（見下面「新增一頁」）。

## 頁尾規則（頁面版本）

每一頁最下面一行（`tests/test_site_pages.py` 每次都檢查）：

```html
<p class="stamp" data-online="<線上網址>">線上版 <a href="<線上網址>">…</a> · 本機版 docs/site/<檔> · 頁面版本 <span data-stamp>0123abcdef · 2026-11-14</span></p>
```

- `data-online` ＝ `docs/artifacts.json` 登記的網址（測試比對）。唯一的例外：產生的、還沒第一次發佈的頁寫 `data-online=""`、「還沒發佈」。
- 頁面版本＝把 `<span data-stamp>` 裡清空後整頁的 sha256 前 10 碼＋日期（`scripts/site_stamp.py`）。
  **線上版跟本機版的頁面版本一樣＝內容一樣**：打開線上頁看最下面那行，就知道它有沒有跟上。
- 改了頁就要重新蓋：`python scripts/site_stamp.py <檔名>`（例 `notes.html`；不給檔名＝全部重蓋，日期會全部變，少用）。
  `build.py`、`build_site.py`、`links.py` 有改到的頁會自己重新蓋；`build_costumes.py page` 之後要自己跑 `site_stamp.py wardrobe.html`。

## 更新一頁（內容改了 → 線上也要改）

1. 改正本：
   - 入口頁：版面、文案改 `scripts/site_index_template.html`；工具卡片的字改 `docs/site-tools.json` 或 pack.json 的 `site`；
     然後 `python scripts/build_site.py`（**不要手改 `docs/site/index.html`**，`build_site.py --check` 與測試會擋）。
   - 衣櫃頁：`python scripts/build_costumes.py page && python scripts/site_stamp.py wardrobe.html`。
   - 手寫的工具頁：直接改 `docs/site/<名字>.html`，再 `python scripts/site_stamp.py <名字>.html`。
   - 只是版本變了：`python scripts/build.py`（或 `python scripts/links.py`）會把每一頁的下載連結與版本字樣改好、重新蓋版本。
2. `python -m pytest -q`（`tests/test_site_pages.py`、`tests/test_build_site.py`、`tests/test_links.py`）。
3. commit。
4. 列出要重新發佈的頁：

   ```bash
   python scripts/build_site.py --list-republish                     # 全部頁：本機檔 → 線上網址
   python scripts/build_site.py --list-republish --since origin/main  # 只列跟 origin/main 比有改的（含還沒 commit 的）
   ```

5. **重新發佈到同一個網址**（有 Artifact 工具的 Claude 做，例如擁有者的 Claude Code）：用 Artifact 工具 publish，
   `file_path` ＝那個本機檔、`url` ＝上面列出的網址（帶 `url` 才會更新同一頁，網址不變；不帶就會變成一個新網址）。
   這個對話之前沒發佈或讀過那一頁的話，先 `action: "read"` 那個網址再發佈（工具的規定）。
6. 打開線上頁，看最下面的頁面版本跟本機檔的一樣。

## 新增一頁（新工具第一次上線）

1. `python scripts/build.py`（或 `python scripts/build_site.py`）產生 `docs/site/<名字>.html`（或自己寫手寫頁，見 [CONTRIBUTING-TOOLS.md](CONTRIBUTING-TOOLS.md) 第 3 節）。
2. 用 Artifact 工具發佈那個檔（**不帶** `url`）→ 拿到新網址。
3. 把網址登記進 `docs/artifacts.json`：`{"title": …, "url": …, "local": "docs/site/<名字>.html", "tool": "<名字>", "made_by": "手寫" 或 "scripts/build_site.py（產生）"}`。
4. `python scripts/build_site.py`：入口頁的卡片改連到線上頁、產生的頁尾填上網址（手寫頁的頁尾要自己填 `data-online` 與連結，再 `site_stamp.py`）。
5. 因為頁尾改了，那一頁**再發佈一次**（這次帶 `url`），入口頁也重新發佈。`pytest`、commit。
6. 要不要也讓 SYNOX 的工具卡片連到它：改 SYNOX 的 `catalog.py`（`HELP_PAGES`）與 SYNOX 的 `docs/artifacts.json`（另一個 repo）。

## 為什麼 `.synoxpack` 不能放在 Artifact

Artifact 只提供一般網頁的檔案類型（HTML、CSS、JS、圖片、字型、PDF、文字…）。`.synoxpack` 是 zip，不是能放的類型，
放了也不會被當成可以下載的檔；而且 SYNOX 下載工具時只接受 GitHub 發佈頁的網址（`catalog.py` 寫死、轉址只跟到 GitHub 自己的主機）。
所以：**頁面放 Artifact，檔案放 GitHub 發佈頁**（[RELEASING.md](RELEASING.md)），頁面上的下載連結由 `scripts/links.py` 指到發佈頁。
