# ADR-0011：語音工具（speech）——三家供應商的文字轉語音與語音轉文字

- 狀態：**提案（MVP 已做）**（2026-11-26：`packs/speech` 0.1.0-alpha.1，alpha，尚未實機驗證——沒有真金鑰，三家都只對假 HTTP 測過）。
- 範圍：新工具 `packs/speech`。不改 SYNOX 核心；需要 SYNOX 補的東西列在第 5 節。
- 讀者：之後接手開發的人與 Claude。

## 1. 一句話

讓 SYNOX 把文字唸成音檔（TTS）、把音檔轉成文字或字幕（STT），**Azure Speech、OpenAI、Google Cloud 三家都做**，
使用者用自己的金鑰，唸和轉文字可以各選一家；先求「有」（MVP），再慢慢補。

## 2. 決定

| 題目 | 決定 | 為什麼 |
|---|---|---|
| 怎麼接三家 | 只用 `requests` 叫各家 REST API，不裝它們的 SDK | 工具庫的規矩（接外面服務只准 requests）；SDK 很大、會自己開執行緒與連線 |
| 金鑰 | `pack.json` 的 `auth.fields`（`azure_key`＋`azure_region`、`openai_key`、`google_key`，全部 `required: false`），程式只用 `pack.secret(name, user)` | SYNOX 收、SYNOX 存（保管箱的 pack_secrets）；不走聊天、不進工具自己的 store、回覆、log。三家選一家就好，所以都不是必填 |
| Google 金鑰放哪 | `X-goog-api-key` 標頭 | 放網址的 `?key=` 會在出錯訊息、代理 log 裡帶出金鑰 |
| Azure 區域 | 只收英文小寫與數字 | 區域會變成網址的主機名，不擋就能被填成別的主機 |
| 設定 | `pack.store.of(user)`：`tts`、`stt`（`auto`／三家）、`lang`、`voice`（每家各記一個）、`rate`、`audio`（mp3／wav）、`text`（text／srt／vtt）；`/speech_settings` 看、`/speech_set <鍵> <值>` 改；面板唯讀 | 使用者要求「工具裡就能看、能改」；SDK 的面板只能讀，所以改設定一定有指令版 |
| 音檔怎麼給使用者 | 寫進 `pack.path_of(user, "out", …)`，用 channel 的 `send_document` 傳；`out/` 只留最新 30 個 | SDK 0.2.5 沒有傳音檔的介面，`send_document` 是現有工具（diary、calendar）在用的；Telegram 收得到 |
| 使用者的音檔怎麼進來 | 放進 `pack.path_of(user, "inbox")`，`/speech_stt <檔名>`（相對路徑，經 `pack.path_of` 擋絕對路徑與 `..`） | SDK 沒有讓 python 工具收附件的介面（Telegram 的檔案只會變成 `__MEDIA__:` 標記交給 AI 對話）。先用資料夾，等 SYNOX 開放 |
| STT 各家 | OpenAI `whisper-1`（直接要 text／srt／vtt）；Azure fast transcription（`2024-11-15`，段落時間自己組字幕）；Google `speech:recognize` 同步辨識（字的時間自己組字幕；mp3 走 `v1p1beta1`） | 都是一次送出、一次拿回，不用輪詢與雲端儲存；長音檔、批次留到之後 |
| 列聲音 | Azure `voices/list`、Google `v1/voices` 有金鑰就問 API；OpenAI 沒有這個 API、沒金鑰時，用內建常用清單 | 需求：「有 API 就叫，沒有就內建」 |
| 上限 | 文字 2000 字（Google 另限 5000 bytes）；音檔 25 MB（Google 約 7 MB）；連線 10 秒、回應 120 秒 | 各家的硬上限，先在本機擋下、給人話 |
| 錯誤 | 401／403、Google 的 API key not valid、SERVICE_DISABLED、429（OpenAI insufficient_quota 另說要儲值）、404（Azure 區域）、413、415、400、5xx、連不上、逾時 → 一句人話；連線例外只說類別 | 不讓使用者看到原始錯誤、也不讓網址或金鑰跟著錯誤出去 |
| 權限 | `access: owner`、`capabilities: ["filesystem:own", "network"]` | ADR-0006 第 4 節：用擁有者帳號接外面服務的一律只給擁有者；讀寫自己的語音資料夾照實宣告 |

## 3. 共用檔的改動（都只追加）

- `tests/test_gates.py`：`CONNECT` 追加 `speech`；`ALLOWED_CAPS` 蓋成 `filesystem:own`＋`network`；
  另立一段檔案規則（`open` 只在 `_write_own`、`_read_audio`；`os.*` 只有 `makedirs`、`listdir`、`remove`）。
- `tests/test_access.py`：`CONNECT` 追加 `speech`。`tests/test_build.py`：`SMOKE` 追加 `/speech_status`。
- `catalog.json`：「語音」群組。README 工具表、CHANGELOG、公開頁（`scripts/build_site.py` 產生）。

## 4. 測試

`tests/test_speech.py`：三家各一個假服務（照官方文件的網址、標頭、body 形狀），檢查 request 長相；
路徑逃逸（`..`、絕對路徑、磁碟機代號、反斜線、不是音檔的副檔名）一律不連網；金鑰不進回覆、log、工具自己的 store，保管箱是密文；
錯誤翻譯逐條；伺服器版：成員看不到、AI 拿不到，金鑰、設定、檔案每人一份。

## 5. 還沒做、要 SYNOX 補的

1. **工具收附件**：SDK 給 python 工具一個「這則指令帶的檔案」介面（Telegram document／voice、網頁上傳），就能直接「傳音檔 → 轉文字」，
   不用先放資料夾。
2. **傳音檔**：channel 加 `send_audio`／`send_voice`（Telegram 會顯示成可以直接播的語音），以及網頁對話能下載 `send_document` 的檔。
3. 長音檔（Azure batch、Google long-running）、OpenAI 新模型（`gpt-4o-mini-tts`、`gpt-4o-transcribe`）、分段唸長文。
4. 實機驗證：拿三家真的金鑰各跑一次唸、轉文字、列聲音，再拿掉 `-alpha`。
