# ADR-0007：吉他「一首歌」的流程——抓和弦時間軸、抓歌詞、合併、出譜

- 狀態：**提議中**；第 1 階段（兩個書籤）已寫好，只在**模擬的頁面**上測過（見最後一節），還沒在真的 Chordify／YouTube 上跑過。
- 範圍：`docs/guitar/capture/`（書籤）、`scripts/guitar_bookmarklets.py`（產生安裝頁）、之後的 `packs/guitar`（匯入、合併、出譜）。
- 背景：擁有者原本想做一個書籤，從 Chordify 抓譜。做下去發現**一個書籤做不完**：
  Chordify 頁面上只有「第幾格是什麼和弦」，沒有歌的長度、沒有歌詞；歌詞在 YouTube；兩邊要合併、再出成看得懂的譜、還要能移調。
  這是一條流程（pipeline），所以做成 `guitar` 工具的一部分，書籤只負責「在瀏覽器裡把資料拿出來」。

## 決定

### 1. 誰做什麼：資料在瀏覽器拿，其他都在工具裡、不連網

```
 ① Chordify 歌曲頁 ──書籤 chordify.js──→ chordify-<歌>.json   （和弦＋時間）
 ② YouTube 影片頁 ──書籤 youtube.js───→ youtube-<歌>.json    （歌詞＋時間、影片長度）
 ③（選用）AI 用 web search 找官方的和弦譜 ──→ 和弦／段落比較準的版本
                         │
                         ▼
 ④ guitar 工具「匯入」（面板拖兩個檔，或聊天貼）：檢查、合併 → song.json（可下載）
 ⑤ 出譜：小節譜（每行 4～5 小節）、歌詞對到小節、和弦圖、移調／Capo（工具裡現有的）
```

- **為什麼用書籤，不讓工具自己去抓**：Chordify、YouTube 的頁面要在你的瀏覽器、用你的登入狀態才看得到；工具（`capabilities: []`）不連網，
  也不該替你登入別的網站。書籤只讀你正在看的那一頁、下載成檔案，不上傳任何地方。
- **為什麼合併在工具裡**：合併、對小節、移調都是純計算，放在工具裡可以測、手機面板也能用，跟現有的 `/chord_tp`、小節譜共用同一套樂理。

### 2. 時間怎麼來（「拿不到歌的長度」的解法）

Chordify 頁面上每個和弦格子有 `data-chord-i`（格子編號），進度條是百分比，**沒有毫秒**。書籤用「校正」補上：

1. 按播放，書籤每 200 ms 記一次（牆上時間、進度條 %、正在亮的格子）。
2. **歌長**＝經過的毫秒 ÷ 進度條走了幾 %（播放速度要 1x、不要拖進度條）。
3. **每格幾毫秒、第 0 格在哪**：每次換格記下當時的歌曲時間，用最小平方法擬合 `時間 = offset + 編號 × 每格毫秒`。
4. 跟 bpm 比：每格 ≈ 60000/bpm → 1 格＝1 拍；≈ 一半 → 半拍。寫進 `timeline.calibration.index_unit`。
5. 沒校正也能下載：假設 1 格＝1 拍、第 0 格在 0 ms，標 `uncalibrated`。
6. YouTube 書籤另外給**影片長度**（`videoDetails.lengthSeconds`）。合併時拿兩個長度互相檢查：差超過 2 秒＝可能不是同一支影片，要提醒。

和弦名稱從和弦圖的網址解（`/chords/A%3Amaj%252FG` → `A:maj/G` → `A/G`），**不讀畫面上的 `Eₘ⁷`**（那是上下標字元，容易解錯）。

### 3. 三種 JSON

**chords（Chordify 書籤）**：`schema: "synox.guitar.chords/1"`
```json
{"schema": "synox.guitar.chords/1",
 "metadata": {"title": "…", "artist": null, "key": "D", "bpm": 130, "source": "Chordify", "page_url": "…"},
 "timeline": {"unit": "milliseconds", "duration": 245000, "beat_ms": 461.54, "ms_per_index": 461.54, "offset_ms": 0,
              "calibration": {"duration_ms": 245000, "changes": 12, "index_unit": "beat"}},
 "chords": [{"index": 0, "chord": "D", "start": 0, "end": 13846, "raw": "D:maj"}]}
```

**lyrics（YouTube 書籤）**：`schema: "synox.guitar.lyrics/1"`
```json
{"schema": "synox.guitar.lyrics/1",
 "metadata": {"youtube_id": "…", "title": "…", "channel": "…", "duration_ms": 245000},
 "lyrics_source": "captions:zh-TW", "lyrics": [{"start": 12000, "end": 15500, "text": "主 我神"}]}
```

**song（合併後，可下載、也是出譜的輸入）**：`schema: "synox.guitar.song/1"`。保留擁有者定的 `metadata`／`timeline`／`events`，另外加回原始的兩條軌與小節：
```json
{"schema": "synox.guitar.song/1",
 "metadata": {"title": "My Lord, My God", "artist": "約書亞樂團", "key": "D", "bpm": 130, "time": "4/4", "source": "Chordify"},
 "timeline": {"duration": 245000, "unit": "milliseconds", "beat_ms": 461.54, "bar_offset_beats": 0},
 "events": [{"start": 0, "end": 1846, "lyric": "歌詞第一段", "chord": "D", "chord_index": 0}],
 "chords": [ … ], "lyrics": [ … ],
 "bars": [{"n": 1, "start": 0, "end": 1846, "chords": [["D", 4]], "lyric": "歌詞第一段"}]}
```

- `events`：把「和弦換」和「歌詞換」的時間點全部當切點，每一段＝一個 event（`lyric` 是那段時間裡在唱的句子，沒有就空字串；
  `chord_index` 是 Chordify 的格子編號）。這就是擁有者要的「像逐字稿一樣：每一段時間是什麼字幕、什麼時候刷」。
- `bars`：用拍子格線切小節（4/4＝4 拍一小節）。**第一拍在哪 Chordify 沒給**：預設第 0 格是第一拍，
  不對就改 `bar_offset_beats`（面板上可以拖），所有小節跟著移。
- 這三種都能直接轉成小節譜的格式（`/chord_chart` 的 JSON），所以出譜、移調、和弦圖都不用重寫。

### 4. 「抓出節奏」能做到哪裡

| 能 | 不能 |
|---|---|
| bpm、每一拍的時間、小節線（拍子格線） | **刷法**（下下上上下上這種）：Chordify 頁面上沒有，要分析音檔，不在這個範圍 |
| 和弦在第幾拍換、一小節裡幾個和弦、各幾拍 | 確定的第一拍位置（要人看一下、拖一次） |
| 依 bpm 與拍號建議刷法（工具裡現有的節奏型） | 保證和弦正確：Chordify 是電腦聽出來的，常把 sus、7th、轉位聽錯 |

### 5. 要不要改成用 web search 找官方譜？——兩個都要，分工

- **官方或樂團自己公開的和弦譜比 Chordify 準**（和弦、段落、轉位都是人寫的）。很多樂團（尤其詩歌）會放和弦譜。
  建議流程的第一步是讓 AI 用 web search 找：找到 → 照小節譜格式抄（看不清楚的標 `?`，不猜）；
  **Chordify＋YouTube 只拿來提供時間**（跟著音樂亮的那種練習）。
- 找譜要 SYNOX 的 AI 有 web search：工具本身不連網。SYNOX 用 `claude -p` 時要 `--allowedTools "WebSearch,WebFetch"`，
  直接打 API 時要在 `tools` 加 `web_search_20250305`（同一天的調查結果）。
- 找到的譜只給自己練習用：工具不存網址以外的原文、不轉貼。

## 階段

1. ✅ **書籤**（這次）：`docs/guitar/capture/chordify.js`、`youtube.js`；`python scripts/guitar_bookmarklets.py` 產生可拖的 `install.html`（`--check` 檢查）。
2. **匯入與合併**（等小節譜的工作合進來後做）：`packs/guitar` 加合併函式（Python，有測試）＋面板「匯入」分頁（拖兩個檔 → 預覽 events、
   調第一拍、下載 song.json、一鍵出小節譜）。聊天：`/chord_song`（貼 song.json 或兩個 JSON）。
3. **找官方譜**：SKILL.md 教 AI 先搜尋、再抄成小節譜格式；工具不連網。
4. **跟著播**（選用）：面板裡依時間亮小節（要使用者自己同時開 YouTube；不嵌入播放器，因為面板不准連外部網址）。

## 風險與注意

- **Chordify 的使用條款**可能不允許自動擷取；書籤只讀你自己畫面上的東西、只給自己用，但要擁有者自己判斷。不做批次、不做伺服器端抓取。
- **頁面改版會壞**：Chordify 的 class 名稱是亂碼（`ct1wuqa`），所以書籤只靠比較穩的東西（`data-chord-i`、和弦圖網址、`input[type=range]`、`dl/dt/dd`）。
  YouTube 的字幕檔（timedtext）最容易被擋：備案是讀「顯示轉錄稿」面板；「歌詞 MV」常常是歌詞燒在畫面上、根本沒有字幕。
- Chordify 的 h1 在擁有者給的例子裡是「頻道 by 影片標題」，**歌名、歌手不可靠**：書籤把原文都存下來（`metadata.h1`），歌名留給人或 AI 填。

## 已經驗證的（第 1 階段）

用 Playwright 在**模擬的頁面**上跑（不是真的網站）：
- Chordify：用擁有者貼的 HTML 做的頁面，12 個格子全部解對（含兩次編碼的 `A/G`、`D/F#`、`F#m7`）；標題、Key、bpm 讀對。
  模擬播放（已知每格 30 ms、第 0 格 300 ms、歌長 40 秒）→ 校正結果每格 31.8 ms、第 0 格 270 ms、歌長 40.9 秒（取樣 200 ms，誤差約 2～10%；放越久越準）。
- YouTube：模擬的播放器資料＋字幕檔 → 選了人工的 zh-TW（沒選自動的英文）、兩句的開始／結束對、影片長度對。
- `install.html` 裡的書籤連結解開後在模擬頁上跑得起來。
- **沒驗證**：真的 Chordify、真的 YouTube（timedtext 會不會擋）、手機上用書籤、下載的檔名（無頭瀏覽器裡檔名沒帶出來）。
