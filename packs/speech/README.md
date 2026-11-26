# 🎙️ 語音

讓 SYNOX 把文字**唸成音檔**（文字轉語音，TTS），或把音檔**轉成文字、字幕**（語音轉文字，STT）。
三家供應商任選：**Azure Speech**、**OpenAI**（TTS＋Whisper）、**Google Cloud**（Text-to-Speech＋Speech-to-Text），用你自己的金鑰；
唸和轉文字可以分開選不同家。

> 這是 **SYNOX 工具庫（SYNOX_Libs）** 的工具，**🧪 alpha（尚未實機驗證）**：功能都做了，但只用假的 HTTP 測過，
> 還沒拿真的金鑰對三家服務試過，目前功能不完善（見最後「目前不完善的地方」）。
> 先把 `speech-<版本>.synoxpack` 裝進 SYNOX（裝法見工具庫的 [README](../../README.md)「給使用者：怎麼裝」），再照下面設定。

## 1. 申請金鑰（三家選一家以上）

只要設一家就能用；設了好幾家，可以用 `/speech_set` 決定唸、轉文字各用哪一家。

### Azure Speech（金鑰＋區域）

1. 到 Azure 入口網站（portal.azure.com）登入，沒有帳號先註冊（有免費額度）。
2. 搜尋「語音服務」（Speech services）→ **建立**：選訂用帳戶、資源群組、**區域**（例 East Asia）、名稱，定價層選 **Free F0**（免費）或 S0。
3. 建好後打開那個資源 →「**金鑰與端點**」：複製 **KEY 1**，並記下「**位置／區域**」（英文小寫，例 `eastasia`、`japaneast`、`westus2`）。
   轉文字用的是 Azure 的「fast transcription」，不是每個區域都有；轉不了時換一個區域（例 `eastus`、`westeurope`）建資源。

### OpenAI（API key）

1. 到 OpenAI 平台（platform.openai.com）登入 →「**API keys**」→ **Create new secret key**，複製（只顯示一次）。
2. 到「**Billing**」儲值（API 沒有免費額度；額度用完會回「額度用完了」）。
3. 唸用 `tts-1`（聲音：alloy、ash、ballad、coral、echo、fable、nova、onyx、sage、shimmer），轉文字用 `whisper-1`。

### Google Cloud（API 金鑰）

1. 到 Google Cloud 主控台（console.cloud.google.com）建立（或選）一個專案，並**啟用帳單**（有每月免費額度）。
2. 「API 和服務 → 程式庫」：啟用 **Cloud Text-to-Speech API** 與 **Cloud Speech-to-Text API**。
3. 「API 和服務 → 憑證 → 建立憑證 → **API 金鑰**」，複製。建議按「限制金鑰」只允許上面兩個 API。

## 2. 把金鑰交給 SYNOX（不要貼在聊天裡）

金鑰由 SYNOX 收進保管箱（加密），**不經過聊天**：

- **網頁**：SYNOX 網頁 → 工具頁「🎙️ 語音」→「🔌 連接」，填你有的那幾格（沒有的留空）：
  `Azure Speech 金鑰`＋`Azure Speech 區域`、`OpenAI API key`、`Google Cloud API 金鑰`。
- **電腦上**：在跑 SYNOX 的電腦打 `python -m synox connect speech`（輸入時不顯示）。

設好後在對話打 `/speech_status`，看到「✅ 已設定」就好了。

## 3. 指令

| 指令 | 做什麼 |
|---|---|
| `/speech <文字>` | 唸成音檔，**當檔案傳給你**，也存在語音資料夾的 `out/`。可加 `lang=en-US`、`provider=openai`、`voice=nova` 只改這一次 |
| `/speech_stt <檔名>` | 把語音資料夾裡的音檔轉成文字；`format=srt`／`format=vtt` 做成字幕檔（傳給你，也存在 `out/`）。可加 `lang=`、`provider=` |
| `/speech_voices [供應商] [語言]` | 列出聲音。例 `/speech_voices azure zh-TW`、`/speech_voices openai` |
| `/speech_status` | 哪幾家設好金鑰、唸／轉文字現在用哪一家 |
| `/speech_settings` | 看全部設定 |
| `/speech_set <鍵> <值>` | 改一個設定（下表）；值寫「預設」改回預設 |
| `/speech_files` | 看語音資料夾裡的檔（`inbox/`、`out/`）與資料夾在哪 |

AI：跟 AI 說「幫我把這段唸出來」「把 meeting.mp3 轉成字幕」「改用 OpenAI 唸」就會用這組指令。

### 設定（`/speech_set`）

| 鍵 | 意思 | 可以填 | 預設 |
|---|---|---|---|
| `tts` | 唸（文字轉語音）用哪一家 | `auto`、`azure`、`openai`、`google` | `auto`（第一個有金鑰的：Azure → OpenAI → Google） |
| `stt` | 轉文字用哪一家 | 同上 | `auto` |
| `lang` | 語言 | `zh-TW`、`en-US`、`ja-JP`… | `zh-TW` |
| `voice` | **目前 `tts` 那一家**的聲音（每家各記一個） | `/speech_voices` 列的名字 | Azure `zh-TW-HsiaoChenNeural`、OpenAI `alloy`、Google 依語言自動 |
| `rate` | 語速（倍） | `0.5`～`2.0` | `1.0` |
| `audio` | 音檔格式 | `mp3`、`wav` | `mp3` |
| `text` | 轉文字的輸出 | `text`、`srt`、`vtt` | `text` |

例：`/speech_set tts openai`、`/speech_set voice nova`、`/speech_set rate 1.2`、`/speech_set stt google`、`/speech_set text srt`。
也可以寫中文鍵：`語言`、`聲音`、`語速`、`音檔格式`、`文字格式`。設定存在保管箱（每個人一份）。

網頁工具頁還有一格**唯讀**面板，列出哪幾家有金鑰與目前設定（改設定請用指令）。

## 4. 檔案怎麼進出

- **唸好的音檔**：用 SYNOX 的「傳檔案」送給你（Telegram 會收到一個檔案），同時存在你的語音資料夾 `out/`
  （個人版在 SYNOX 資料夾的 `data/speech/files/out/`；伺服器版是 `data/users/<你的 id>/speech/files/out/`）。`out/` 只留最新的 30 個。
  網頁對話目前**還不能下載檔案**（SYNOX 網頁會說「請到 Telegram 拿」），可以從上面的資料夾拿。
- **要轉文字的音檔**：先放進語音資料夾的 `inbox/`（`/speech_files` 會告訴你在哪），再打 `/speech_stt <檔名>`。
  也可以轉 `out/` 裡的檔（`/speech_stt out/tts-….mp3`）。只讀得到你自己語音資料夾裡的檔。
- 收的音檔：mp3、wav、m4a、mp4、mpga、mpeg、ogg、oga、opus、webm、flac（Google 只收 wav、flac、mp3）。

## 5. 上限

| | 上限 | 超過時 |
|---|---|---|
| 唸的文字 | 一次 2000 字（Google 另外限 5000 bytes，中文約 1600 字） | 告訴你要分段 |
| 轉文字的音檔 | 25 MB（OpenAI 的上限）；Google 約 7 MB、約 1 分鐘 | 告訴你太大、建議剪短或換一家 |
| 一次等多久 | 連線 10 秒、等回應 120 秒 | 「等太久沒有回應」 |

供應商的錯誤會翻成一句話：金鑰不對（401／403、Google 的 API key not valid）、Google 專案沒啟用 API、
太頻繁或配額用完（429；OpenAI 的 insufficient_quota 會說要儲值）、Azure 區域填錯（404）、檔案太大（413）、格式不收（415）、對方暫時壞掉（5xx）。

## 6. 安全與隱私（說實話版）

- **只給擁有者**（`access: owner`）：伺服器版的成員、訪客打了跟不存在一樣，AI 也拿不到（ADR-0006 第 4 節）。
- **金鑰**只在 SYNOX 的保管箱（`pack.secret`）；工具不把金鑰存進自己的資料、不放進回覆、不寫 log。Google 的金鑰放在
  `X-goog-api-key` 標頭（不放網址），連線出錯時只說錯誤類別，不印網址。
- **你唸的文字、轉的錄音會送到你選的那一家雲端**，並用掉你的額度／費用。不想外流的錄音不要轉。
- 會連到的只有：`<區域>.tts.speech.microsoft.com`、`<區域>.api.cognitive.microsoft.com`、`api.openai.com`、
  `texttospeech.googleapis.com`、`speech.googleapis.com`。不開埠、不執行別的程式，只用 `requests`。
- 檔案只在你自己的語音資料夾（`pack.path_of`）；`pack.json` 宣告 `capabilities: ["filesystem:own", "network"]`。

## 7. 目前不完善的地方（alpha）

- **三家都只對假的 HTTP 測過**：request 格式照各家官方文件寫，還沒拿真的金鑰打過；欄位名或預設聲音可能要再對一次。
- **傳檔案給你**：用 SYNOX 現有的 `send_document`（Telegram 收得到）；SYNOX 的網頁對話還不能下載檔案，只能到資料夾拿。
  沒有「語音訊息」的樣式（SDK 還沒有 send_audio／send_voice），收到的是一般檔案。
- **收你傳來的音檔**：SDK 還沒有讓工具直接收 Telegram／網頁附件的介面，所以現在要先把音檔放進語音資料夾的 `inbox/`
  （個人版就是 SYNOX 資料夾裡的那個資料夾）。等 SYNOX 開放附件給工具，就能直接「傳音檔 → 轉文字」。
- Azure 轉文字用 fast transcription（2024-11-15 版），不是每個區域都有；Google 轉文字是同步辨識，只收約 1 分鐘、wav／flac／mp3，
  mp3 走 v1p1beta1 並假設 44.1kHz（不對時請轉成 wav）；長音檔建議用 OpenAI 或 Azure。
- Google、Azure 的字幕時間軸是用它們回的段落時間組的，切段可能比較粗；OpenAI 的 srt／vtt 是 Whisper 直接給的。
- OpenAI 唸固定用 `tts-1`（有語速），還不能選 `tts-1-hd`、`gpt-4o-mini-tts`。
- 一次一段：長文要自己分段；沒有批次、沒有排程。

## 給工程師

- 程式：`pack.py` 單檔。三家各一組函式：`tts_azure`／`tts_openai`／`tts_google`、`stt_azure`／`stt_openai`／`stt_google`、
  `voices_azure`／`voices_google`；`call()` 統一叫 API（逾時、不跟隨轉址、錯誤經 `explain()` 翻成人話）。
- API：Azure TTS `POST https://<region>.tts.speech.microsoft.com/cognitiveservices/v1`（SSML、`X-Microsoft-OutputFormat`）、
  聲音 `GET …/cognitiveservices/voices/list`、STT `POST https://<region>.api.cognitive.microsoft.com/speechtotext/transcriptions:transcribe?api-version=2024-11-15`
  （multipart：`audio`＋`definition`）；OpenAI `POST /v1/audio/speech`（`tts-1`）、`POST /v1/audio/transcriptions`（`whisper-1`，`response_format` text／srt／vtt）；
  Google `POST https://texttospeech.googleapis.com/v1/text:synthesize`、`GET …/v1/voices`、`POST https://speech.googleapis.com/v1/speech:recognize`（mp3 用 `v1p1beta1`）。
- 設定：`pack.store.of(user)` 一份 `{"v": 1, "tts", "stt", "lang", "rate", "audio", "text", "voices": {供應商: 聲音}}`。
- Azure 區域會變成網址的一部分：只收英文小寫與數字（`REGION_RE`），不合就不連。
- 測試：工具庫的 `tests/test_speech.py`，換掉 `new_session`（假的 HTTP）與 `now`，不連網；含雙人隔離、權限、金鑰不外洩。
- 設計：[ADR-0011](../../docs/adr/0011-speech-tool.md)。
