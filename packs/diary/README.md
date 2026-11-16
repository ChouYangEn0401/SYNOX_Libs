# 📔 日記（diary）0.1.0-alpha.1

> **alpha（尚未實機驗證）**：只在自動測試（假資料）裡跑過，還沒在真的 SYNOX／Telegram 上試過。
> 實機試過、沒問題之後才會發 0.1.0（沒有 `-alpha` 的版本）。

一個寫日記的工具：**挑一個題組、多半用勾的就寫完了**；寫完可以做成一頁 HTML，或一張像日記本內頁的卡片。
從維護者自己的「每日日記」程式 1:1 搬進 SYNOX：題組、版型、分類與挑題組的規則、寫入的規則、歷史版本、垃圾桶、
備份、匯出匯入，跟原本一樣（`tests/test_diary_parity.py` 拿原本的程式錄下來的答案逐字比對）。
不需要 API key、不連網；日記加密存在 SYNOX 的保管箱，伺服器版每個人一份、互相看不到。

一開始是為了寫靈修日記做的，所以靈修那一套是完整的：讀經、省察、感恩、代禱、講道與小組筆記，還有難到說不出話的日子怎麼向神哀哭。
也有不談信仰的：面對情緒、面對痛苦、工作與創業、想成為什麼樣的人。

## 指令

| 打 | 做什麼 |
|---|---|
| `/diary 今天很好` | 記成今天「隨手記」的新的一則（接在之前的後面，不蓋掉） |
| `/diary_put yesterday jot 昨天忘了記的事` | 別天的隨手記；第二格換成別的題組 id＝寫進那個題組的第一題 |
| `/diary_put 2026-11-02 three-lines {"answers": {"fact": "下雨"}}` | 一次填好幾題（JSON；AI 幫你填時走這條） |
| `/diary_show`、`/diary_show 2026-11-02`、`/diary_show 12` | 今天、某一天、第 12 篇寫了什麼 |
| `/diary_list` | 最近寫過的（日期、題組、字數、編號） |
| `/diary_templates`、`/diary_template three-lines` | 有哪些題組、一個題組有哪些題 |
| `/diary_suggest 今天很累但很感謝` | 替一段話挑題組，附理由（照規則，不叫模型） |
| `/diary_categories` | 照分類看：幾段式、要不要反思／信仰、走哪些步驟 |
| `/diary_export today jot` | 做成一頁 HTML ＋ 一張卡片 SVG，直接傳給你 |
| `/diary_export id=12 layout=ledger theme=midnight gift=yes gift_message=送你` | 換版型、紙張顏色、加一張要點開的封面 |
| `/diary_image_prompt today jot style=ink` | 畫圖用的 prompt（中英各一段，照你寫的） |
| `/diary_overwrite today capsule a_verse user {"answers": {"a_verse": "…"}}` | 換掉已經寫過的一兩題（只動點名的） |
| `/diary_title 12 雨天`、`/diary_switch 12 gratitude` | 取標題、換題組（答案一個都不刪） |
| `/diary_rm 12`、`/diary_trash`、`/diary_undelete 12`、`/diary_purge 12` | 刪（進垃圾桶）、看垃圾桶、救回來、永久刪除 |
| `/diary_history 12`、`/diary_restore 5` | 歷史版本、放回某個版本 |
| `/diary_tidy` | 清掉完全空白的日記（進垃圾桶） |
| `/diary_backups`、`/diary_backup`、`/diary_backup_restore <名稱>`、`/diary_backup_rm <名稱>` | 整本的備份 |
| `/diary_export_all`、`/diary_import`、`/diary_import_replace` | 整本匯出成 JSON（傳給你）、匯入（合併／取代）；只有你本人能用 |
| `/diary_tpl_save {…}`、`/diary_tpl_rm <id>`、`/diary_tpl_restore <id>` | 新增或改題組、刪題組、把內建的拿回來 |
| `/diary_layouts`、`/diary_themes`、`/diary_layout_rm <id>`、`/diary_layout_restore <id>` | 版型、紙張顏色 |
| `/diary_settings`、`/diary_settings_set day_start_hour 4` | 看、改設定 |
| `/diary_setup`、`/diary_setup_init generic` | 狀態與還沒拿出來的題組、挑起始組合 |

照位置填：前面幾格是必填欄位，**最後一格吃掉剩下的字**；也可以寫 `key=value`。
限制（SYNOX 的規矩）：內容不能以 `-` 開頭；照位置填時換行會變成空格（多行要給 AI，或寫 JSON）；一個欄位最多 2000 字
（很長的一篇分幾次 `/diary_put`，每次填幾題；只填空著的題目，不會蓋掉）。
想一次填好幾題、或要 AI 幫你整理，直接跟 AI 說——AI 整理過的一律標「AI 整理」、旁邊印你的原文，而且不會蓋掉你自己寫的。

`date` 收 `YYYY-MM-DD`、`today`、`yesterday`；半夜寫的人可以設 `day_start_hour`（例如 4＝四點以前「今天」還算昨天）。

## 輸出：同一篇日記，兩種形狀

|  | 頁面 · HTML | 卡片 · SVG |
|---|---|---|
| 形狀 | 由**版型**決定，共 4 種 | 單欄，寬度 480～2400（預設 1080） |
| 怎麼看 | 瀏覽器打開、可捲動、可列印成 PDF | 直接看圖；要 PNG 就在瀏覽器打開後截圖 |

- 版型：**信箋 letter**（單欄、最耐看）、**對帳 ledger**（左欄整理勾選答案，右欄放文字）、**稿紙 manuscript**（整頁方格）、
  **明信片 postcard**（郵戳日期＋雙欄密排）。紙張顏色五種：letterpress、notebook、linen、dawn、midnight。
- 卡片的紙：橫線 ruled、方格 grid、空白 plain、點陣 dotted；文字真的坐在橫線上。
- `gift=yes` 加一張要點一下才翻開的封面——把日記送給別人的那個想法。
- 檔案寫在你自己的資料夾、傳給你之後就刪，不會留在 SYNOX 這邊的硬碟上。

## 題組：內建 41 個

原本的 21 個（與神同行 7、心情與難處 3、成為什麼樣的人 5、工作與創業 3、簡單記一下 3），加上 2026-11 新設計的 20 個。
新的裡面 8 個預設就出現（SOAP 靈修簡短版、禱告日誌、代禱名單、講道筆記、小組分享、一週靈命回顧、創業打卡、晚間回顧），
另外 12 個內建、但預設不出現（題組檔寫 `"default_hidden": true`）：`/diary_setup` 列得出來，`/diary_tpl_restore <id>` 拿出來。

每一個在做什麼、什麼時候用，`/diary_templates` 看（`when` 那一句）；怎麼自己寫一個看 [TEMPLATES.md](TEMPLATES.md)。

**起始組合**（`/diary_setup_init`，只加不刪）：`generic`＝不談信仰的八個（記事、三行、感恩、三段回看、情緒、難熬的一天、擔心、週回顧）；
`devotional`＝內建的全部（預設不出現的那幾個除外）。沒跑過＝跟 `devotional` 一樣。

## 存檔：不會弄丟東西

| 層 | 擋什麼 | 怎麼用 |
|---|---|---|
| **歷史版本** | 一次存檔存壞了 | `/diary_history`、`/diary_restore`（內容變短之前一定先留一份） |
| **垃圾桶** | 刪錯了 | `/diary_trash`、`/diary_undelete` |
| **整本備份** | 一次弄壞很多篇 | 寫入時每 6 小時自動留一份（最多 20 份輪流用）、手動的不會被清；還原前也先備份 |
| **匯出 JSON** | 電腦壞了 | `/diary_export_all`，存到雲端；格式跟原本的「匯出全部」一樣（只有 `format` 改叫 `synox.diary.export`），原本程式匯出的檔也匯得進來 |

- **換題組不會刪東西**：新題組沒有的題目，內容仍然留在那篇日記裡，換回原本的題組就會出現。
- **刪掉題組也不會刪日記**：用它寫的日記會退成純文字的「保留下來的內容」；還有日記在用的題組要 `force=yes` 才刪。

## 資料存哪

都在 SYNOX 的保管箱（密文）：個人版 `data/diary.json.enc`（日記、歷史版本）、`data/diary.setup.json.enc`（設定、自己的題組與版型）、
`data/diary.backup-<槽>.json.enc`（整本備份）；伺服器版在 `data/users/<id>/` 底下，每人一份。
匯出、匯入用的暫存檔在你自己的資料夾（`/diary_setup` 會說在哪），傳完、匯完就刪。

**從原本的每日日記搬過來**：在原本的程式按「匯出全部」，把那個 JSON 放進上面那個資料夾、檔名改成 `import.json`，打 `/diary_import`。

## 跟原本不一樣的地方（為了 SYNOX）

- 原本的網頁編輯器（三欄、換色、題組編輯器）沒有搬：SYNOX 這邊是聊天指令＋AI。題組用 JSON 改（`/diary_tpl_save`）。
- 原本的 PNG 是瀏覽器轉的；這裡給 SVG，要 PNG 用瀏覽器打開後截圖。
- 原本「用 AI 設計新版型」「AI 取名」要 Anthropic 的 key、直接叫 API：SYNOX 版不叫外面的 API（SYNOX 自己的 AI 可以替你取標題，用 `/diary_title`）。
  畫圖 prompt 是原本「沒有 key」的那一套規則。
- 原本的設定 `export_dir`（匯出到哪個資料夾）拿掉了：檔案直接傳給你。
- 版型原本是 Jinja2；SYNOX 不能替工具裝套件，所以內建一個只收原本版型用到的語法的小引擎（`if`、`for`、`set`、`include`、濾器 `runs`…），
  輸出跟原本逐字一樣。

## 測試

`tests/test_diary.py`（行為、權限、每人一份、密文）、`tests/test_diary_parity.py`（跟原本逐字比對：95 頁 HTML、60 張卡片、挑題組、23 次寫入）。
比對的期望值由 `scripts/dump_diary_parity.py` 從原本的程式錄下來。
