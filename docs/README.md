# docs：工具庫的文件從這裡找

| 我要… | 看 |
|---|---|
| **第一次來**（外部開發者、雲端 session、AI agent）：這個 repo 是什麼、從 clone 到開 PR、規矩、已知落差 | [HANDOFF.md](HANDOFF.md)（給 AI 的精簡版：[../AGENTS.md](../AGENTS.md)） |
| 新增或修改一個工具（資料夾、pack.json、`help` 區塊、測試、版本規則、打包、會出現在哪、SYNOX 怎麼拿到） | [CONTRIBUTING-TOOLS.md](CONTRIBUTING-TOOLS.md) |
| 發佈：打包 → 測試 → 合併 → 上傳 GitHub 發佈頁 → 重新發佈線上頁 → 之後切 Latest | [RELEASING.md](RELEASING.md)（最上面有流程圖與檢查清單） |
| 線上頁（claude.ai Artifact）：每一頁的本機檔與網址、怎麼更新、頁尾的頁面版本、為什麼 `.synoxpack` 不能放那裡 | [ARTIFACTS.md](ARTIFACTS.md)（網址的正本：[artifacts.json](artifacts.json)） |
| 改入口頁的版面或文案（給設計的人） | 樣板 [`scripts/site_index_template.html`](../scripts/site_index_template.html)；卡片的字 [site-tools.json](site-tools.json)；改完 `python scripts/build_site.py` |
| 為什麼這樣設計 | [adr/](adr/)（0001 工具庫與 SDK 依賴、0002 記帳通用版、0003 工具畫面、0004 記帳重做、0005 Google 試算表只加列、0006 個人版與伺服器版、0007 吉他一首歌的流程、0008 記帳對帳單與寫檢討） |
| 真機人工驗證（衣櫃頁在各瀏覽器的開場動畫、下載；工具庫列表顯示警告）：全部勾完才算過關 | [testing/wardrobe-and-index-checklist.md](testing/wardrobe-and-index-checklist.md) |
| 每個檔從哪裡搬來 | [sources.md](sources.md) |
| MCP 工具的審查紀錄 | [mcp-audit-2026-11.md](mcp-audit-2026-11.md) |
| 使用者怎麼裝、穩定度、衣櫃、找 SYNOX 的順序、公開版檢查 | [../README.md](../README.md) |

會產生東西的腳本（都在 `scripts/`，都可以 `--check` 只檢查）：

| 腳本 | 產生 | 檢查 |
|---|---|---|
| `build.py` | `dist/`（每個工具與外觀的 `.synoxpack`、別名、`tools-<VERSION>.zip`、`index.json`、`SHA256SUMS`）；全部打包時也跑下面兩支 | `build.py --check` |
| `build_site.py` | `docs/site/index.html`（入口頁）＋沒有手寫頁的工具的 `docs/site/<名字>.html`；`--list-republish` 列出本機檔 → 線上網址 | `build_site.py --check` |
| `links.py` | 改寫 README 與 `docs/site/*.html` 的下載連結與版本字樣 | `links.py --check` |
| `build_costumes.py page` | `docs/site/wardrobe.html`（衣櫃頁） | `build_costumes.py check`（外觀包本身） |
| `build_costumes.py tokens --write` | [costume-token-usage.md](costume-token-usage.md)（每套用到的顏色變數、寫死的顏色；給 SYNOX 套用前的缺漏檢查；`--json` 給程式讀） | `tests/test_costumes.py` |
| `site_stamp.py [檔名]` | 頁尾的頁面版本 | `tests/test_site_pages.py` |
