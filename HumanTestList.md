# HumanTestList（要人親手測的）

給維護者看的待測清單：做了、但自動測試碰不到的東西（真的瀏覽器、新版 SYNOX、乾淨的 Linux 環境、要維護者決定的驗收）。
電腦能自己跑的檢查在 [VerifyList.json](VerifyList.json)。

格式：一行一件 `- [ ] 日期 · 什麼 · 怎麼測（步驟）· 看到什麼算過`。測過了打 `[x]`、寫日期與結果，搬到「測過了」。

## 待測

- [ ] 2026-11-19 · 5 個面板在真的瀏覽器裡讀得到資料（todo、calendar、bullet、money、guitar） · 用 SYNOX `dev` 最新版開網頁；每個工具用 `scripts/build.py <工具名> --out <暫存資料夾>` 打包後安裝（或照 docs/HANDOFF.md 的安裝方式），先用指令放一兩筆假資料（例如 todo 加一件、money 記一筆），再從網頁打開它的面板 · 5 個面板都顯示剛放的資料，沒有 401、「讀不到」或空白；全部過了才把這 5 個工具版本號的 `-alpha.N` 拿掉（改 pack.json 與 CHANGELOG），哪個沒過就記下來、那個先不拿
- [ ] 2026-11-19 · guitar 面板的「複製」鈕在隔開的 iframe 裡能不能用 · 在上一項打開的 guitar 面板貼一段譜、移調，按「移調後的譜」旁的「複製」，再到記事本貼上；和弦圖那頁的「複製文字」也試一次 · 記下結果：貼得出來＝過；貼不出來時，面板要提示改用手動選取（全選後 Ctrl+C），而且手動選取真的複製得到；兩種情況都把結果寫進這行
- [ ] 2026-11-19 · Linux 從零走一遍 docs/HANDOFF.md，記 Linux 基準 · 開一個乾淨的 Linux 雲端 session，只照 docs/HANDOFF.md 第 5 節：clone 這個 repo 與並排的 SYNOX（`dev`）、建 venv、設 `SYNOX_HOME`／`SYNOX_SRC`、跑 `python -m pytest -q`、`scripts/build.py --check`、`scripts/public_check.py` · 每一步照文件都做得到（做不到或跟文件不同的地方逐條回報、修文件）；記下 Linux 的 passed／skipped／failed 數字與失敗清單，寫回 docs/HANDOFF.md 5.3 節

## 測過了

（還沒有）
