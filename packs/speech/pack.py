"""🎙️ 語音：文字轉語音（TTS）、語音轉文字（STT），三家供應商 Azure Speech／OpenAI／Google Cloud 任選。

指令（群組 speech）：
  /speech <文字>                 文字 → 音檔（存在自己的語音資料夾 out/，並當檔案傳給你）
  /speech_stt <檔名> [format=srt] 自己語音資料夾裡的音檔 → 文字／srt／vtt
  /speech_voices [供應商] [語言]   列出聲音（Azure、Google 有金鑰時問 API；OpenAI 與沒金鑰時用內建常用清單）
  /speech_status                 哪些供應商有設金鑰、現在用哪一家
  /speech_settings               看設定；/speech_set <鍵> <值> 改設定（tts、stt、lang、voice、rate、audio、text）
  /speech_files                  看語音資料夾裡有哪些檔（inbox/ 放要轉文字的、out/ 是做好的）

金鑰：pack.json 的 auth.fields（azure_key＋azure_region、openai_key、google_key），使用者在 SYNOX 網頁工具頁「🔌 連接」
或電腦上 `python -m synox connect speech` 填，SYNOX 收進保管箱；這裡只用 pack.secret(name, user) 讀，
不進 pack.store、不進回覆、不進 log。設定（供應商、語言、聲音、語速、格式）存 pack.store.of(user)。
檔案只經 pack.path_of(user, …)：inbox/（使用者放要轉文字的音檔）、out/（做好的音檔與字幕，最多留 MAX_KEEP 個）。

連線只用 requests、只連三家官方的 REST API（不裝它們的 SDK）：
  Azure：<region>.tts.speech.microsoft.com（TTS、聲音清單）、<region>.api.cognitive.microsoft.com（fast transcription）
  OpenAI：api.openai.com/v1/audio/speech、/v1/audio/transcriptions（whisper-1）
  Google：texttospeech.googleapis.com/v1、speech.googleapis.com（v1；mp3 走 v1p1beta1）
Google 的金鑰放 X-goog-api-key 標頭（不放網址，出錯時網址不會帶出金鑰）。
測試換掉 new_session（假的 requests.Session）與 now()，不連網。
"""

import base64
import os
import re
import time
from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

import requests

from isdtelekit.core.registry import READ, WRITE, Field

from synox.packs import kit


PROVIDERS = ("azure", "openai", "google")
NAMES = {"azure": "Azure Speech", "openai": "OpenAI", "google": "Google Cloud"}
SECRET_NAMES = {"azure": ("azure_key", "azure_region"), "openai": ("openai_key",), "google": ("google_key",)}
ALIASES = {"微軟": "azure", "ms": "azure", "microsoft": "azure", "谷歌": "google", "gcp": "google", "gpt": "openai",
           "自動": "auto"}

MAX_TEXT = 2000                 # 一次唸的字數（SYNOX 的參數本來就限 2000 字）
GOOGLE_MAX_BYTES = 5000         # Google TTS 一次最多 5000 bytes（中文約 1600 字）
MAX_AUDIO = 25 * 1024 * 1024    # 轉文字的音檔上限（OpenAI 的上限）
GOOGLE_MAX_AUDIO = 7 * 1024 * 1024    # Google 同步辨識：請求最多 10MB，base64 會變大 1/3
MAX_KEEP = 30                   # out/ 最多留幾個檔（舊的刪掉）
MAX_REPLY = 3500                # 轉好的文字直接貼在訊息裡的上限，超過就另外傳 .txt
TIMEOUT = (10, 120)             # 連線 10 秒、等回應 120 秒

AUDIO_EXTS = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
              ".mpga": "audio/mpeg", ".mpeg": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg",
              ".opus": "audio/ogg", ".webm": "audio/webm", ".flac": "audio/flac"}
AUDIO_FORMATS = ("mp3", "wav")
TEXT_FORMATS = ("text", "srt", "vtt")

LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$")
VOICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,79}$")
REGION_RE = re.compile(r"^[a-z0-9]{2,30}$")
FILE_NAME_RE = re.compile(r"^[^<>:\"|?*\x00-\x1f]{1,120}$")

AZURE_FORMATS = {"mp3": "audio-24khz-48kbitrate-mono-mp3", "wav": "riff-24khz-16bit-mono-pcm"}
GOOGLE_ENCODINGS = {"mp3": "MP3", "wav": "LINEAR16"}      # LINEAR16 回的內容本來就帶 WAV 檔頭
OPENAI_TTS_MODEL = "tts-1"
OPENAI_STT_MODEL = "whisper-1"
AZURE_STT_VERSION = "2024-11-15"

# 內建常用聲音（OpenAI 沒有列聲音的 API；Azure、Google 沒金鑰時也拿這份給人挑）
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer")
BUILTIN_VOICES = {
    "azure": {"zh-TW": ("zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural", "zh-TW-YunJheNeural"),
              "zh-CN": ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural"),
              "en-US": ("en-US-JennyNeural", "en-US-GuyNeural", "en-US-AriaNeural"),
              "ja-JP": ("ja-JP-NanamiNeural", "ja-JP-KeitaNeural")},
    "google": {"zh-TW": ("cmn-TW-Standard-A", "cmn-TW-Wavenet-A", "cmn-TW-Wavenet-B"),
               "zh-CN": ("cmn-CN-Standard-A", "cmn-CN-Wavenet-A"),
               "en-US": ("en-US-Standard-C", "en-US-Neural2-F", "en-US-Neural2-D"),
               "ja-JP": ("ja-JP-Standard-A", "ja-JP-Neural2-B")},
}

KEYS = {"tts": "TTS（文字轉語音）用哪一家", "stt": "STT（語音轉文字）用哪一家", "lang": "預設語言",
        "voice": "目前 TTS 那一家的預設聲音", "rate": "語速（倍）", "audio": "音檔格式", "text": "轉文字的輸出格式"}
KEY_ALIASES = {"語言": "lang", "language": "lang", "聲音": "voice", "語速": "rate", "speed": "rate",
               "音檔": "audio", "音檔格式": "audio", "文字": "text", "文字格式": "text", "format": "text",
               "朗讀": "tts", "轉文字": "stt"}
RESET_WORDS = ("預設", "default", "reset")


def now():                      # 唯一碰時鐘的地方（測試會換掉）
    return datetime.fromtimestamp(time.time())


def new_session():
    """每次叫 API 開一個新的（模組層級不放共用的 session）；測試換成假的。"""
    return requests.Session()


def empty():
    """每次回新的預設設定。"""
    return {"v": 1, "tts": "auto", "stt": "auto", "lang": "zh-TW", "rate": "1.0", "audio": "mp3", "text": "text",
            "voices": {}}


class SpeechError(Exception):
    """回給使用者的一句人話（不含金鑰）。"""


# ── 小工具（純函式，測試直接叫）──────────────────────────────────────────────

def provider_word(value):
    v = (value or "").strip().lower()
    v = ALIASES.get(v, v)
    return v if v in PROVIDERS + ("auto",) else None


def rate_problem(value):
    try:
        r = float(value)
    except (TypeError, ValueError):
        return "語速要是數字，例如 1.0（正常）、1.2（快一點）、0.8（慢一點）"
    if not 0.5 <= r <= 2.0:
        return "語速只能在 0.5 到 2.0 倍之間"
    return None


def azure_rate(rate):
    pct = int(round((float(rate) - 1.0) * 100))
    return f"{pct:+d}%"


def default_voice(provider, lang):
    if provider == "openai":
        return "alloy"
    choices = BUILTIN_VOICES.get(provider, {}).get(lang)
    if choices:
        return choices[0]
    if provider == "azure":
        return "zh-TW-HsiaoChenNeural" if lang.startswith("zh") else "en-US-JennyNeural"
    return ""                   # google：沒指定就只送語言，讓 Google 自己挑


def stamp(ms):
    ms = max(0, int(ms))
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return h, m, s, ms


def to_srt(segments):
    out = []
    for i, (start, end, text) in enumerate(segments, 1):
        a, b = stamp(start), stamp(end)
        out.append(f"{i}\n{a[0]:02d}:{a[1]:02d}:{a[2]:02d},{a[3]:03d} --> {b[0]:02d}:{b[1]:02d}:{b[2]:02d},{b[3]:03d}\n"
                   f"{text.strip()}\n")
    return "\n".join(out)


def to_vtt(segments):
    out = ["WEBVTT\n"]
    for start, end, text in segments:
        a, b = stamp(start), stamp(end)
        out.append(f"{a[0]:02d}:{a[1]:02d}:{a[2]:02d}.{a[3]:03d} --> {b[0]:02d}:{b[1]:02d}:{b[2]:02d}.{b[3]:03d}\n"
                   f"{text.strip()}\n")
    return "\n".join(out)


def render(segments, fmt):
    if fmt == "srt":
        return to_srt(segments)
    if fmt == "vtt":
        return to_vtt(segments)
    return "\n".join(t.strip() for _, _, t in segments if t.strip())


def google_seconds(value):
    """Google 的時間 "1.300s" → 毫秒。"""
    try:
        return int(round(float(str(value or "0").rstrip("s")) * 1000))
    except ValueError:
        return 0


def size_text(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{max(1, n // 1024)} KB"


def _body(resp):
    try:
        return resp.json()
    except Exception:
        return {}


def _message_of(resp):
    """供應商錯誤回應裡的那句說明（遮掉像金鑰的字、最多 160 字）。"""
    data = _body(resp)
    msg = ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code") or ""
        elif isinstance(err, str):
            msg = err
        msg = msg or data.get("message") or data.get("Message") or ""
    if not msg:
        msg = (getattr(resp, "text", "") or "")[:160]
    return kit.mask_tokens(" ".join(str(msg).split()))[:160]


def explain(provider, resp):
    """供應商的錯誤 → 一句人話。"""
    name = NAMES[provider]
    status = getattr(resp, "status_code", 0)
    text = (getattr(resp, "text", "") or "")
    msg = _message_of(resp)
    if status in (401, 403) or "API_KEY_INVALID" in text or "API key not valid" in text:
        if "SERVICE_DISABLED" in text or "has not been used" in text or "is disabled" in text:
            return (f"❌ {name}：這個 Google Cloud 專案還沒啟用要用的 API（Text-to-Speech 或 Speech-to-Text）。"
                    "到 Google Cloud 主控台的「API 和服務 → 程式庫」啟用後再試。")
        extra = "，或區域跟金鑰不是同一個資源" if provider == "azure" else ""
        return (f"❌ {name} 不認這把金鑰（{status}）：可能打錯、過期、被停用{extra}。"
                "到 SYNOX 網頁工具頁「🔌 連接」重新填，或用 /speech_status 看設定。")
    if status == 429 or "RESOURCE_EXHAUSTED" in text:
        if "insufficient_quota" in text:
            return "❌ OpenAI 帳戶的額度用完了（429）：到 OpenAI 後台的 Billing 儲值或調高上限後再試。"
        return f"❌ {name} 說太頻繁，或超過這個月的免費額度／配額（429）。等一下再試，或到 {name} 後台看用量。"
    if status == 404:
        if provider == "azure":
            return "❌ Azure 找不到這個服務（404）：區域可能填錯（例 eastasia、japaneast），或這個區域不提供這項功能。"
        return f"❌ {name} 找不到這個服務（404）：{msg or '網址或模型名稱不對'}"
    if status == 413:
        return f"❌ 檔案太大，{name} 不收（413）。"
    if status == 415:
        return f"❌ {name} 不收這種音檔格式（415）：換成 wav 或 mp3 再試。"
    if status == 400:
        if "too long" in text.lower() or "Sync input too long" in text:
            return f"❌ {name}：音檔太長（Google 一次只收約 1 分鐘以內的）。剪短，或 /speech_set stt openai 改用別家。"
        return f"❌ {name} 說這個請求不對（400）：{msg or '可能是聲音名字跟語言對不上，或這家不支援這個語言'}"
    if status >= 500:
        return f"❌ {name} 那邊暫時出問題（{status}），等一下再試。"
    return f"❌ {name} 回了錯誤（{status}）：{msg or '沒有說明'}"


def call(provider, method, url, **kw):
    """叫一次 API；成功回 response，失敗丟 SpeechError（人話）。例外只說類別，不帶網址與標頭。"""
    session = new_session()
    try:
        resp = session.request(method, url, timeout=TIMEOUT, allow_redirects=False, **kw)
    except requests.Timeout:
        raise SpeechError(f"❌ {NAMES[provider]} 等太久沒有回應，等一下再試。") from None
    except requests.RequestException as e:
        raise SpeechError(f"❌ 連不上 {NAMES[provider]}（{type(e).__name__}）：檢查網路後再試。") from None
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()
    if not 200 <= resp.status_code < 300:
        raise SpeechError(explain(provider, resp))
    return resp


# ── 三家的 API ───────────────────────────────────────────────────────────────

def tts_azure(creds, text, lang, voice, rate, fmt):
    key, region = creds
    ssml = (f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang={quoteattr(lang)}>"
            f"<voice name={quoteattr(voice)}><prosody rate={quoteattr(azure_rate(rate))}>{escape(text)}</prosody>"
            "</voice></speak>")
    resp = call("azure", "POST", f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                         "X-Microsoft-OutputFormat": AZURE_FORMATS[fmt], "User-Agent": "synox-speech"},
                data=ssml.encode("utf-8"))
    return resp.content


def tts_openai(creds, text, lang, voice, rate, fmt):
    resp = call("openai", "POST", "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {creds[0]}"},
                json={"model": OPENAI_TTS_MODEL, "input": text, "voice": voice, "response_format": fmt,
                      "speed": float(rate)})
    return resp.content


def tts_google(creds, text, lang, voice, rate, fmt):
    if len(text.encode("utf-8")) > GOOGLE_MAX_BYTES:
        raise SpeechError(f"❌ Google 一次最多唸 {GOOGLE_MAX_BYTES} bytes（中文大約 1600 字）。分段再唸，或改用別家。")
    v = {"languageCode": lang}
    if voice:
        v["name"] = voice
    resp = call("google", "POST", "https://texttospeech.googleapis.com/v1/text:synthesize",
                headers={"X-goog-api-key": creds[0]},
                json={"input": {"text": text}, "voice": v,
                      "audioConfig": {"audioEncoding": GOOGLE_ENCODINGS[fmt], "speakingRate": float(rate)}})
    content = (_body(resp) or {}).get("audioContent")
    if not content:
        raise SpeechError("❌ Google 沒有回音檔（回應裡沒有 audioContent）。")
    try:
        return base64.b64decode(content)
    except (ValueError, TypeError):
        raise SpeechError("❌ Google 回的音檔解不開。") from None


def tts(provider, *args):
    """照供應商叫對應的那一個（寫成明確的 if，靜態掃描才看得出叫的是誰）。"""
    if provider == "azure":
        return tts_azure(*args)
    if provider == "openai":
        return tts_openai(*args)
    return tts_google(*args)


def stt_openai(creds, name, data, mime, lang, fmt):
    form = {"model": OPENAI_STT_MODEL, "response_format": fmt}
    if lang:
        form["language"] = lang.split("-")[0]
    resp = call("openai", "POST", "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {creds[0]}"},
                files={"file": (name, data, mime)}, data=form)
    return resp.text if fmt != "text" else (resp.text or "").strip()


def stt_azure(creds, name, data, mime, lang, fmt):
    key, region = creds
    definition = '{"locales": ["%s"], "profanityFilterMode": "None"}' % lang
    resp = call("azure", "POST",
                f"https://{region}.api.cognitive.microsoft.com/speechtotext/transcriptions:transcribe"
                f"?api-version={AZURE_STT_VERSION}",
                headers={"Ocp-Apim-Subscription-Key": key},
                files={"audio": (name, data, mime), "definition": (None, definition, "application/json")})
    body = _body(resp) or {}
    segments = []
    for p in body.get("phrases") or []:
        start = int(p.get("offsetMilliseconds") or 0)
        segments.append((start, start + int(p.get("durationMilliseconds") or 0), str(p.get("text") or "")))
    if fmt == "text":
        combined = [str(c.get("text") or "") for c in body.get("combinedPhrases") or []]
        if any(combined):
            return "\n".join(c for c in combined if c).strip()
    return render(segments, fmt)


def stt_google(creds, name, data, mime, lang, fmt):
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".wav", ".flac", ".mp3"):
        raise SpeechError("❌ Google 目前只接 wav、flac、mp3（這個工具的 alpha 版）。轉成 wav 再試，"
                          "或 /speech_set stt openai（OpenAI 收的格式最多）。")
    if len(data) > GOOGLE_MAX_AUDIO:
        raise SpeechError(f"❌ Google 一次只收 {size_text(GOOGLE_MAX_AUDIO)} 以內、約 1 分鐘的音檔。剪短，或改用 OpenAI／Azure。")
    config = {"languageCode": lang, "enableAutomaticPunctuation": True, "enableWordTimeOffsets": True}
    version = "v1"
    if ext == ".mp3":                     # MP3 只有 v1p1beta1 收
        config["encoding"] = "MP3"
        config["sampleRateHertz"] = 44100
        version = "v1p1beta1"
    resp = call("google", "POST", f"https://speech.googleapis.com/{version}/speech:recognize",
                headers={"X-goog-api-key": creds[0]},
                json={"config": config, "audio": {"content": base64.b64encode(data).decode("ascii")}})
    body = _body(resp) or {}
    segments, last_end = [], 0
    for r in body.get("results") or []:
        alts = r.get("alternatives") or []
        if not alts:
            continue
        alt = alts[0]
        words = alt.get("words") or []
        start = google_seconds(words[0].get("startTime")) if words else last_end
        end = google_seconds(words[-1].get("endTime")) if words else google_seconds(r.get("resultEndTime"))
        end = max(end, start)
        segments.append((start, end, str(alt.get("transcript") or "")))
        last_end = end
    return render(segments, fmt)


def stt_call(provider, *args):
    if provider == "azure":
        return stt_azure(*args)
    if provider == "openai":
        return stt_openai(*args)
    return stt_google(*args)


def voices_azure(creds, lang):
    key, region = creds
    resp = call("azure", "GET", f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list",
                headers={"Ocp-Apim-Subscription-Key": key})
    rows = _body(resp)
    rows = rows if isinstance(rows, list) else []
    out = []
    for v in rows:
        if not lang or str(v.get("Locale", "")).lower() == lang.lower():
            extra = "、".join(x for x in (v.get("LocalName"), v.get("Gender")) if x)
            out.append(f"{v.get('ShortName')}" + (f"（{extra}）" if extra else ""))
    return out


def voices_google(creds, lang):
    resp = call("google", "GET", "https://texttospeech.googleapis.com/v1/voices",
                headers={"X-goog-api-key": creds[0]}, params={"languageCode": lang} if lang else None)
    out = []
    for v in (_body(resp) or {}).get("voices") or []:
        gender = {"FEMALE": "女", "MALE": "男"}.get(v.get("ssmlGender"), "")
        out.append(f"{v.get('name')}" + (f"（{gender}）" if gender else ""))
    return out


# ── install ──────────────────────────────────────────────────────────────────

def install(app, pack):
    g = pack.group

    def settings(user):
        data = pack.store.of(user).load(empty())
        base = empty()
        base.update({k: v for k, v in (data or {}).items() if k in base})
        if not isinstance(base.get("voices"), dict):
            base["voices"] = {}
        return base

    def secret(name, user):
        try:
            value = pack.secret(name, user)
        except Exception:                    # 保管箱沒開、伺服器版沒給 user…：當沒設
            return ""
        return (value or "").strip()

    def creds_of(provider, user):
        """(金鑰…) 或 None（沒設）。Azure 的區域格式不對＝丟 SpeechError（它會變成網址的一部分）。"""
        values = tuple(secret(n, user) for n in SECRET_NAMES[provider])
        if not all(values):
            return None
        if provider == "azure":
            region = values[1].lower().replace(" ", "")
            if not REGION_RE.match(region):
                raise SpeechError("❌ Azure 的區域格式不對：只能是英文小寫與數字，例如 eastasia、japaneast、westus2。"
                                  "到 SYNOX 網頁工具頁「🔌 連接」改。")
            return values[0], region
        return values

    def configured(user):
        out = {}
        for p in PROVIDERS:
            try:
                out[p] = creds_of(p, user) is not None
            except SpeechError:
                out[p] = False
        return out

    def pick(choice, user, what):
        """設定或參數指定的供應商 → (供應商, 金鑰)。auto＝第一個有設金鑰的。"""
        p = provider_word(choice)
        if p is None:
            raise SpeechError(f"❌ 不認得「{choice}」：供應商只有 azure、openai、google（或 auto）。")
        if p == "auto":
            for q in PROVIDERS:
                creds = creds_of(q, user)
                if creds:
                    return q, creds
            raise SpeechError(NOT_SET)
        creds = creds_of(p, user)
        if creds is None:
            raise SpeechError(f"❌ 還沒設定 {NAMES[p]} 的金鑰，沒辦法{what}。\n{HOW_TO_KEY}\n"
                              "或用 /speech_set tts auto（自動用有金鑰的那一家）。")
        return p, creds

    def out_dir(user):
        folder = pack.path_of(user, "out")
        os.makedirs(folder, exist_ok=True)
        return folder

    def inbox_dir(user):
        folder = pack.path_of(user, "inbox")
        os.makedirs(folder, exist_ok=True)
        return folder

    def new_name(user, prefix, ext):
        base = f"{prefix}-{now().strftime('%Y%m%d-%H%M%S')}"
        folder = out_dir(user)
        name, n = f"{base}.{ext}", 1
        while os.path.exists(os.path.join(folder, name)):
            n += 1
            name = f"{base}-{n}.{ext}"
        return name

    def _write_own(path, data):
        with open(path, "wb") as f:
            f.write(data)

    def prune(user):
        """out/ 只留最新的 MAX_KEEP 個（檔名帶時間，照名字排）。"""
        folder = out_dir(user)
        names = sorted(n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n)))
        for n in names[:-MAX_KEEP]:
            os.remove(pack.path_of(user, "out", n))

    def save_and_send(api, chat_id, user, name, data, caption):
        path = pack.path_of(user, "out", name)
        _write_own(path, data)
        prune(user)
        try:
            sent = api.send_document(chat_id, path, caption=caption)
        except Exception:
            sent = {"ok": False}
        return bool((sent or {}).get("ok"))

    def find_audio(user, raw):
        """使用者給的檔名（自己語音資料夾裡的相對路徑）→ 真的路徑。先看 files/ 底下，再看 inbox/。"""
        rel = (raw or "").strip().strip("\"'").replace("\\", "/")
        if not rel or not FILE_NAME_RE.match(rel) or rel.startswith("/") or ".." in rel.split("/"):
            raise SpeechError("❌ 檔名不對：寫你語音資料夾裡的檔名，例如 meeting.mp3 或 inbox/meeting.mp3（不能用絕對路徑或 ..）。")
        ext = os.path.splitext(rel)[1].lower()
        if ext not in AUDIO_EXTS:
            raise SpeechError(f"❌ 「{ext or '沒有副檔名'}」不是收的音檔格式。可以是：{'、'.join(sorted(e[1:] for e in AUDIO_EXTS))}。")
        for parts in ((rel,), ("inbox", rel)):
            try:
                path = pack.path_of(user, *parts)
            except Exception:
                raise SpeechError("❌ 檔名不對：只能是你語音資料夾裡的檔。") from None
            if os.path.isfile(path):
                return path, os.path.basename(rel), ext
        raise SpeechError(f"❌ 找不到 {rel}。先把音檔放進你的語音資料夾的 inbox/（/speech_files 看位置與現有的檔）。")

    def _read_audio(path):
        with open(path, "rb") as f:
            return f.read(MAX_AUDIO + 1)

    def guard(fn):
        def run(api, chat_id, user, values):
            try:
                fn(api, chat_id, user, values)
            except SpeechError as e:
                api.send_message(chat_id, str(e))
        return run

    # ── 指令 ──────────────────────────────────────────────────────────────────

    def say(api, chat_id, user, values):
        text = (values.get("text") or "").strip()
        if not text:
            raise SpeechError("用法：/speech <要唸的文字>")
        if len(text) > MAX_TEXT:
            raise SpeechError(f"❌ 一次最多唸 {MAX_TEXT} 字（這次 {len(text)} 字）。分段再唸。")
        s = settings(user)
        provider, creds = pick(values.get("provider") or s["tts"], user, "唸")
        lang = (values.get("lang") or s["lang"]).strip()
        if not LANG_RE.match(lang):
            raise SpeechError("❌ 語言代碼不對，例如 zh-TW、en-US、ja-JP。")
        voice = (values.get("voice") or s["voices"].get(provider) or default_voice(provider, lang)).strip()
        if voice and not VOICE_RE.match(voice):
            raise SpeechError("❌ 聲音名字不對：只能是英數字與 - _ . :（/speech_voices 看有哪些）。")
        if provider == "openai" and voice not in OPENAI_VOICES:
            raise SpeechError(f"❌ OpenAI 沒有「{voice}」這個聲音。可以是：{'、'.join(OPENAI_VOICES)}。")
        fmt = s["audio"] if s["audio"] in AUDIO_FORMATS else "mp3"
        rate = s["rate"] if rate_problem(s["rate"]) is None else "1.0"
        audio = tts(provider, creds, text, lang, voice, rate, fmt)
        if not audio:
            raise SpeechError(f"❌ {NAMES[provider]} 回了空的音檔。")
        name = new_name(user, "tts", fmt)
        who = f"{NAMES[provider]}・{voice or lang}・{rate} 倍・{fmt}"
        sent = save_and_send(api, chat_id, user, name, audio, f"🎙️ {text[:60]}{'…' if len(text) > 60 else ''}（{who}）")
        where = f"存在你的語音資料夾 out/{name}"
        if sent:
            api.send_message(chat_id, f"✅ 唸好了（{who}，{size_text(len(audio))}），音檔已經傳給你；{where}。")
        else:
            api.send_message(chat_id, f"✅ 唸好了（{who}），但這個對話傳不了檔案；{where}（/speech_files 看全部）。")

    def stt(api, chat_id, user, values):
        s = settings(user)
        fmt = (values.get("format") or s["text"]).strip().lower()
        if fmt not in TEXT_FORMATS:
            raise SpeechError("❌ 輸出格式只能是 text、srt、vtt。")
        lang = (values.get("lang") or s["lang"]).strip()
        if not LANG_RE.match(lang):
            raise SpeechError("❌ 語言代碼不對，例如 zh-TW、en-US、ja-JP。")
        path, name, ext = find_audio(user, values.get("file"))
        size = os.path.getsize(path)
        if size > MAX_AUDIO:
            raise SpeechError(f"❌ 音檔太大（{size_text(size)}）：一次最多 {size_text(MAX_AUDIO)}。剪短或壓成 mp3 再試。")
        if size == 0:
            raise SpeechError("❌ 這個音檔是空的。")
        provider, creds = pick(values.get("provider") or s["stt"], user, "轉文字")
        data = _read_audio(path)
        result = stt_call(provider, creds, name, data, AUDIO_EXTS[ext], lang, fmt)
        if not (result or "").strip():
            api.send_message(chat_id, f"🤔 {NAMES[provider]} 沒有聽出任何字（可能是靜音、語言設錯，或音質太差）。")
            return
        stem = os.path.splitext(name)[0][:40] or "audio"
        safe = re.sub(r"[^\w\-]+", "_", stem).strip("_") or "audio"
        head = f"📝 {name} 轉好了（{NAMES[provider]}・{lang}・{fmt}）"
        if fmt == "text" and len(result) <= MAX_REPLY:
            api.send_message(chat_id, f"{head}：\n{result}")
            return
        out = new_name(user, f"stt-{safe}", "txt" if fmt == "text" else fmt)
        sent = save_and_send(api, chat_id, user, out, result.encode("utf-8"), head)
        preview = result[:600] + ("…" if len(result) > 600 else "")
        tail = "已經當檔案傳給你" if sent else "這個對話傳不了檔案"
        api.send_message(chat_id, f"{head}，{tail}；存在你的語音資料夾 out/{out}。\n開頭：\n{preview}")

    def voices(api, chat_id, user, values):
        s = settings(user)
        lang = (values.get("lang") or s["lang"]).strip()
        if not LANG_RE.match(lang):
            raise SpeechError("❌ 語言代碼不對，例如 zh-TW、en-US、ja-JP。")
        choice = values.get("provider") or s["tts"]
        p = provider_word(choice)
        if p is None:
            raise SpeechError(f"❌ 不認得「{choice}」：供應商只有 azure、openai、google。")
        if p == "auto":
            have = configured(user)
            p = next((q for q in PROVIDERS if have[q]), "azure")
        current = s["voices"].get(p) or default_voice(p, lang) or "（讓 Google 依語言自己挑）"
        if p == "openai":
            rows, src = list(OPENAI_VOICES), "OpenAI 的聲音（內建清單：OpenAI 沒有列聲音的 API；每個聲音都能說多種語言）"
        else:
            creds = creds_of(p, user)
            if creds is None:
                rows = list(BUILTIN_VOICES[p].get(lang) or ())
                src = f"{NAMES[p]} 的常用聲音（內建清單；設好金鑰後會改問 {NAMES[p]} 拿完整的）"
            else:
                rows = voices_azure(creds, lang) if p == "azure" else voices_google(creds, lang)
                src = f"{NAMES[p]} 的聲音（{lang}，共 {len(rows)} 個）"
        if not rows:
            api.send_message(chat_id, f"🤔 {NAMES[p]} 沒有 {lang} 的聲音。換個語言試試：/speech_voices {p} en-US")
            return
        shown = rows[:40]
        more = f"\n…還有 {len(rows) - len(shown)} 個" if len(rows) > len(shown) else ""
        api.send_message(chat_id, f"🎙️ {src}：\n" + "\n".join(f"• {r}" for r in shown) + more
                         + f"\n\n現在的預設：{current}\n改：/speech_set tts {p}，再 /speech_set voice <名字>")

    def status(api, chat_id, user, values):
        s = settings(user)
        have = configured(user)
        lines = ["🎙️ 語音：金鑰狀態"]
        for p in PROVIDERS:
            mark = "✅ 已設定" if have[p] else "—  還沒設定"
            if p == "azure":
                region = secret("azure_region", user)
                if secret("azure_key", user) and not have[p]:
                    mark = "⚠️ 金鑰有了，區域沒填或格式不對"
                elif region and have[p]:
                    mark += f"（區域 {region.lower()}）"
            lines.append(f"• {NAMES[p]}：{mark}")

        def using(choice):
            p = provider_word(choice) or "auto"
            if p == "auto":
                q = next((x for x in PROVIDERS if have[x]), None)
                return f"自動（現在是 {NAMES[q]}）" if q else "自動（還沒有能用的）"
            return NAMES[p] + ("" if have[p] else "（⚠️ 還沒設金鑰）")

        lines.append(f"\n文字轉語音用：{using(s['tts'])}")
        lines.append(f"語音轉文字用：{using(s['stt'])}")
        if not any(have.values()):
            lines.append("\n" + HOW_TO_KEY)
        lines.append("\n設定：/speech_settings")
        api.send_message(chat_id, "\n".join(lines))

    def show_settings(api, chat_id, user, values):
        s = settings(user)
        voices_line = "、".join(f"{p} {s['voices'].get(p) or '（預設 ' + (default_voice(p, s['lang']) or '依語言') + '）'}"
                               for p in PROVIDERS)
        lines = ["⚙️ 語音設定（/speech_set <鍵> <值> 改；值寫「預設」改回預設）",
                 f"• tts（文字轉語音用哪家）：{s['tts']}　可以是 auto、azure、openai、google",
                 f"• stt（語音轉文字用哪家）：{s['stt']}　可以是 auto、azure、openai、google",
                 f"• lang（語言）：{s['lang']}　例 zh-TW、en-US、ja-JP",
                 f"• voice（聲音，每家各一個）：{voices_line}",
                 f"• rate（語速）：{s['rate']} 倍　0.5～2.0",
                 f"• audio（音檔格式）：{s['audio']}　mp3 或 wav",
                 f"• text（轉文字輸出）：{s['text']}　text、srt、vtt",
                 "\n例：/speech_set tts openai、/speech_set rate 1.2、/speech_set voice nova"]
        api.send_message(chat_id, "\n".join(lines))

    def set_(api, chat_id, user, values):
        raw_key = (values.get("key") or "").strip().lower()
        key = KEY_ALIASES.get(raw_key, raw_key)
        value = (values.get("value") or "").strip()
        if key not in KEYS:
            raise SpeechError("❌ 不認得這個設定。可以改：" + "、".join(f"{k}（{v}）" for k, v in KEYS.items())
                              + "\n例：/speech_set rate 1.2")
        if not value:
            raise SpeechError(f"用法：/speech_set {key} <值>（/speech_settings 看可以填什麼）")
        reset = value.lower() in RESET_WORDS
        base = empty()
        s = settings(user)
        if key in ("tts", "stt"):
            p = "auto" if reset else provider_word(value)
            if p is None:
                raise SpeechError("❌ 只能是 auto、azure、openai、google。")
            new, shown = p, p
        elif key == "lang":
            new = base["lang"] if reset else value
            if not LANG_RE.match(new):
                raise SpeechError("❌ 語言代碼不對，例如 zh-TW、en-US、ja-JP。")
            shown = new
        elif key == "rate":
            new = base["rate"] if reset else value
            problem = rate_problem(new)
            if problem:
                raise SpeechError("❌ " + problem)
            new = f"{float(new):.2f}".rstrip("0").rstrip(".")
            new = new if "." in new else new + ".0"
            shown = new + " 倍"
        elif key == "audio":
            new = base["audio"] if reset else value.lower()
            if new not in AUDIO_FORMATS:
                raise SpeechError("❌ 音檔格式只能是 mp3 或 wav。")
            shown = new
        elif key == "text":
            new = base["text"] if reset else value.lower()
            if new not in TEXT_FORMATS:
                raise SpeechError("❌ 轉文字輸出只能是 text、srt、vtt。")
            shown = new
        else:                                   # voice：存給「現在 TTS 用的那一家」
            p = provider_word(s["tts"])
            if p in (None, "auto"):
                have = configured(user)
                p = next((q for q in PROVIDERS if have[q]), None)
                if p is None:
                    raise SpeechError("❌ 先決定 TTS 用哪一家：/speech_set tts azure（或 openai、google），再設聲音。")
            if not reset:
                if not VOICE_RE.match(value):
                    raise SpeechError("❌ 聲音名字只能是英數字與 - _ . :（/speech_voices 看有哪些）。")
                if p == "openai" and value.lower() not in OPENAI_VOICES:
                    raise SpeechError(f"❌ OpenAI 沒有這個聲音。可以是：{'、'.join(OPENAI_VOICES)}。")

            def put_voice(d):
                vs = d.setdefault("voices", {})
                if not isinstance(vs, dict):
                    d["voices"] = vs = {}
                if reset:
                    vs.pop(p, None)
                else:
                    vs[p] = value.lower() if p == "openai" else value
            pack.store.of(user).update(put_voice, empty())
            shown = "預設" if reset else (value.lower() if p == "openai" else value)
            api.send_message(chat_id, f"✅ {NAMES[p]} 的聲音改成 {shown}。")
            return

        def put(d):
            d[key] = new
        pack.store.of(user).update(put, empty())
        api.send_message(chat_id, f"✅ {key}（{KEYS[key]}）改成 {shown}。")

    def files(api, chat_id, user, values):
        lines = ["📁 你的語音資料夾"]
        for sub, label in (("inbox", "inbox/（放要轉文字的音檔）"), ("out", "out/（做好的音檔、字幕）")):
            folder = inbox_dir(user) if sub == "inbox" else out_dir(user)
            names = sorted((n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))), reverse=True)
            lines.append(f"\n{label}：{len(names)} 個")
            for n in names[:20]:
                lines.append(f"• {n}（{size_text(os.path.getsize(os.path.join(folder, n)))}）")
            if len(names) > 20:
                lines.append(f"…還有 {len(names) - 20} 個")
        lines.append(f"\n位置：{inbox_dir(user)}")
        lines.append("把音檔放進 inbox/ 之後：/speech_stt <檔名>（例 /speech_stt meeting.mp3 format=srt）")
        api.send_message(chat_id, "\n".join(lines))

    def panel_settings(user):
        """網頁面板（唯讀）：設定與哪幾家有金鑰（只有有沒有，不給值）。"""
        s = settings(user)
        return {"settings": {k: s[k] for k in ("tts", "stt", "lang", "rate", "audio", "text")},
                "voices": {p: s["voices"].get(p) or default_voice(p, s["lang"]) or "" for p in PROVIDERS},
                "keys": configured(user), "names": NAMES}

    fmt_field = Field("format", "輸出格式 text、srt 或 vtt（不填用設定）")
    lang_field = Field("lang", "語言代碼，例 zh-TW、en-US（不填用設定）")
    provider_field = Field("provider", "供應商 azure、openai 或 google（不填用設定）")

    kit.command(g, "add", guard(say), "文字轉語音：唸成音檔傳給你", default=True, effect=WRITE, positional=1,
                fields=[Field("text", "要唸的文字", required=True), provider_field,
                        Field("voice", "聲音名字（不填用設定）"), lang_field])
    kit.command(g, "stt", guard(stt), "語音轉文字：把語音資料夾裡的音檔轉成文字／字幕", effect=WRITE, positional=1,
                fields=[Field("file", "語音資料夾裡的檔名，例 meeting.mp3", required=True), fmt_field, lang_field,
                        provider_field])
    kit.command(g, "voices", guard(voices), "列出可以用的聲音", effect=READ,
                fields=[provider_field, lang_field])
    kit.command(g, "status", guard(status), "哪些供應商有設金鑰、現在用哪一家", effect=READ)
    kit.command(g, "settings", guard(show_settings), "看語音設定", effect=READ)
    kit.command(g, "set", guard(set_), "改一個語音設定", effect=WRITE,
                fields=[Field("key", "設定的名字：tts、stt、lang、voice、rate、audio、text", required=True),
                        Field("value", "新的值（寫「預設」改回預設）", required=True)])
    kit.command(g, "files", guard(files), "看語音資料夾裡的檔", effect=READ)
    pack.web_route("settings", panel_settings)


HOW_TO_KEY = ("設定金鑰：到 SYNOX 網頁 → 工具頁「🎙️ 語音」→「🔌 連接」填（Azure 要金鑰＋區域、OpenAI 要 API key、Google 要 API 金鑰），"
              "或在跑 SYNOX 的電腦上打 python -m synox connect speech。金鑰不要貼在聊天裡。")
NOT_SET = "❌ 還沒設定任何語音供應商的金鑰。\n" + HOW_TO_KEY
