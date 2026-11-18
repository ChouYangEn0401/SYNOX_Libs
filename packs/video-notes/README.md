# 🎬 影片筆記（video-notes）

> 🔧 **test（測試中）**，`0.1.0-alpha.1`（尚未實機驗證：只對假的 Video Notes 測過）。

把影片網址交給**你電腦上已經裝好的 Video Notes**（命令列＋網頁的影片筆記工具）整理成筆記。
這個工具只是一座橋：不附 Whisper、yt-dlp、AI 套件（那些約 2 GB，都在 Video Notes 自己的環境裡），
下載、語音辨識、AI 服務與金鑰、輸出位置全部照 Video Notes 自己的設定。**沒有裝 Video Notes 就不能用**（只會告訴你怎麼設定）。

## 指令

| 指令 | 做什麼 |
|---|---|
| `/v2n <網址…> [profile=…] [with=…] [preset=…] [focus=…] [outputs=…] [redo=…]` | 影片做筆記（背景做，做完通知你；一次一個工作，其他排隊） |
| `/v2n_status [編號]` | 看進度：最近的工作，或某個工作每支影片做到哪一階段（資訊、下載、抽音訊、語音辨識、AI 筆記） |
| `/v2n_get [編號] [item=…] [slug=…]` | 拿筆記全文（Markdown）：某個工作的，或筆記庫第幾項的 |
| `/v2n_list [關鍵字]` | 列出筆記庫（Video Notes 輸出資料夾裡整理過的影片與合併），編號給 get、combine 用 |
| `/v2n_combine 1,2,3 [title=…] [preset=…] [profile=…] [yes=是]` | 把筆記庫裡好幾支合併成一份（會用到 AI；Video Notes 說規模很大時要加 `yes=是`） |
| `/v2n_cancel [編號]` | 停掉還在做或排隊的工作（做好的檔案留著，再做一次會沿用） |
| `/v2n_check` | Video Notes 的自我檢查（ffmpeg、JS 環境、yt-dlp、金鑰有沒有設…；不連網） |
| `/v2n_setup [program=…] [python=…] [out=…] [timeout=…]` | 設定（**只有你本人**能打，AI 不能叫）；不帶參數＝看現在的設定 |

AI 也可以替你叫前七個（例如「幫我把這支影片做成筆記」）；`setup` 不行。
**AI 想合併時一定先送確認卡給你**（`combine` 標成危險：會花 AI 額度），你按 ✅ 才排進去；你自己打 `/v2n_combine` 直接做。

工具畫面（SYNOX 網頁「工具」→ 影片筆記 → 打開畫面）：上面是做筆記的表單，中間是工作清單（按「重新整理」更新進度、可以停掉），
點一個做好的工作看筆記全文、按「複製 Markdown」；下面可以列出筆記庫、看任一項的筆記、合併（會先跳確認框）。

## 安裝與第一次設定

1. **先裝 Video Notes**（這個工具不附）：到 [PersonalToolZoo 的發佈頁](https://github.com/ChouYangEn0401/PersonalToolZoo/releases)
   下載最新的 `VideoNotes_v…`（Windows 的 `VideoNotes(vX.Y.Z).exe`），放在你自己選的資料夾；或 clone 那個 repo、從原始碼跑
   （`tools\video-notes\run.bat`，第一次會建好它自己的 `.venv`）。照它的 README 準備好 ffmpeg、JS 環境與 AI 服務，
   跑一次 `VideoNotes --check` 沒有錯誤。要用到 `--json` 的版本（v2 以後）。
2. **裝這個工具**：SYNOX 網頁「設定 → 🧩 功能與安裝 → 工具庫（官方）」找「影片筆記」按安裝；或下載 `video-notes-<版本>.synoxpack`
   點兩下、拖進 SYNOX 網頁，或 `python -m synox tools install video-notes`（SYNOX 的 docs/install-packs.md）。
   安全報告會列出 `subprocess` 與 `filesystem:any`（下面「能力」），看完按確認。
3. **告訴它 Video Notes 在哪**（在 SYNOX 打，換成你的路徑）：
   - exe 版：`/v2n_setup program=<放 exe 的資料夾>`（挑資料夾裡最新的 `VideoNotes*.exe`），或直接給 exe 的完整路徑
   - 原始碼版：`/v2n_setup program=<Video Notes 的工具資料夾>`（資料夾或 `VideoNotes.py`；會用它旁邊 `.venv` 的 Python，
     要用別的 Python 再加 `python=<python.exe>`）
   - 選填：`out=downloads`／`out=data`／`out=<資料夾>`（不給＝Video Notes 自己的設定）、`timeout=<分鐘>`（預設 180）
4. `/v2n_check` 看 Video Notes 的環境，沒問題就 `/v2n <網址>`。

沒設定、路徑不對、Video Notes 回的不是 JSON（太舊）時，只會回一句怎麼修，不會出錯。

## 怎麼跟 Video Notes 說話（設計）

- **用命令列，不用它的網頁 API**：`VideoNotes … --json`（Video Notes README「給程式呼叫」：stdout 一份 `{"ok", "data"|"error"}`，
  進度印在 stderr，退出碼 0／2／3／4／1）。命令列不用另外開著 Video Notes 的網頁（127.0.0.1:8766），也不會跳出瀏覽器。
- 每個工作經 SYNOX 的 JobRunner：argv 陣列（不經 shell）、不開視窗、逾時與取消把整棵程序樹（含 ffmpeg）一起收掉、
  SYNOX 關掉時跟著結束（下次打開看到「SYNOX 重開過，中斷了」，再做一次會沿用做好的部分）。
- 網址寫進一個暫時的清單檔交給 `--file`（不放在命令列：SYNOX 的 log 會記下命令列），做完就刪；只收 http／https 網址，最多 20 支。
  其他選項寫成 `--k=v` 一段：值不會被當成另一個旗標。
- 子程序的環境拿掉看起來像密鑰的變數；Video Notes 自己讀它的 `.env`。這個工具不碰、不存任何 AI 金鑰。
- 一次只跑一個工作（同一張顯示卡跑兩個語音辨識只會互搶；Video Notes 自己會在一個工作裡並行處理好幾支）。

## 能力（安裝報告上會看到）

| 能力 | 為什麼 |
|---|---|
| `subprocess`（開別的程式） | 執行你設定的 Video Notes（exe 或它的 Python）。**它會連網**（下載影片、呼叫你在 Video Notes 選的 AI 服務、雲端語音辨識如果你選了）——那是 Video Notes 做的事，這個工具本身不連網 |
| `filesystem:any`（讀 pack 以外的檔案） | 讀 Video Notes 輸出資料夾裡的 `notes.<slug>.md`（只讀、只收這種檔名；不改不刪） |

只給擁有者（`access: owner`），只能私訊用。設定 `program` 等於「讓 SYNOX 執行這支程式」，所以 `/v2n_setup` 一定要你本人打（`callers: human`）。

## 資料存哪

- 設定、工作清單、每個工作的結果（影片標題、網址、筆記檔的位置、錯誤原因）：SYNOX 保管箱（`pack.store`，**密文**），只留最近 30 個。
- 工作**執行中**的輸出（進度與最後那份 JSON，JobRunner 只會寫檔）與網址清單：SYNOX 資料夾裡 `video-notes/files/jobs/`，
  **讀完結果就刪**（列筆記庫、自我檢查的也是）；SYNOX 中途關掉留下來的，下次用這個工具時刪。
- SYNOX 的 log 會記下每個工作的命令列（JobRunner）：裡面有 Video Notes 的位置與選項（`focus`、合併的 `title`、合併時各支影片的資料夾）。
  網址不在裡面。
- 筆記、逐字稿、影音：Video Notes 自己的輸出資料夾（預設「下載\Video Notes」）。

## 測試

`tests/test_video_notes.py`：用一支假的 Video Notes（`tests/fixtures/fake_videonotes.py`，只用標準函式庫）跑完整的路——
設定、做筆記、看進度、拿筆記、筆記庫、合併（含 AI 叫的要先按確認卡）、取消、失敗、重開 SYNOX、沒設定、
工具畫面的讀與動作、打包與裝進空的 SYNOX、跑完不留明文檔。不下載影片、不跑 Whisper、不叫 AI、不開埠。
