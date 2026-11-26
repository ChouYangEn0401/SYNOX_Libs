# -*- coding: utf-8 -*-
"""🎙️ 語音（packs/speech）：全部用假的 HTTP session（三家各一個記憶體裡的小假服務），不連網、不開埠。
金鑰走 SYNOX 的 pack secret（保管箱的 pack_secrets，跟網頁「連接」、python -m synox connect 同一份）；
真的 SYNOX（conftest.host_bot）：真的 loader、保管箱、權限、伺服器版每人一份。"""

import base64
import json
import logging
import os
import sys
from datetime import datetime
from urllib.parse import urlsplit

import pytest
import requests

from isdtelekit.core.registry import AI
from isdtelekit.testing import OWNER_ID
from isdtelekit.users.roles import BasicRoles

from synox import policy
from synox.packs import scan
from synox.packs import secrets as pack_secrets
from synox.packs.manifest import read

from conftest import OTHER_ID, PACKS

# 假的金鑰（不是任何真的帳號）
AZ_KEY = "0123456789abcdef0123456789abcdef"
OA_KEY = "openai-test-key-00000000000000000000"
GG_KEY = "AIzaTEST000000000000000000000000000000"
REGION = "eastasia"
ALL_KEYS = {"azure_key": AZ_KEY, "azure_region": REGION, "openai_key": OA_KEY, "google_key": GG_KEY}
WHEN = datetime(2026, 11, 9, 9, 30, 0)
MEMBER = 77


class Resp:
    def __init__(self, status, data=None, content=None, text=None):
        self.status_code = status
        self.headers = {}
        if content is not None:
            self.content = content
        elif data is not None:
            self.content = json.dumps(data).encode("utf-8")
        else:
            self.content = (text or "").encode("utf-8")
        self.text = text if text is not None else self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


AZ_VOICES = [{"ShortName": "zh-TW-HsiaoChenNeural", "Locale": "zh-TW", "LocalName": "曉臻", "Gender": "Female"},
             {"ShortName": "zh-TW-YunJheNeural", "Locale": "zh-TW", "LocalName": "雲哲", "Gender": "Male"},
             {"ShortName": "en-US-JennyNeural", "Locale": "en-US", "LocalName": "Jenny", "Gender": "Female"}]
GG_VOICES = {"voices": [{"name": "cmn-TW-Wavenet-A", "languageCodes": ["cmn-TW"], "ssmlGender": "FEMALE"},
                        {"name": "cmn-TW-Wavenet-B", "languageCodes": ["cmn-TW"], "ssmlGender": "MALE"}]}


class FakeSpeech:
    """requests.Session 的替身：三家的 REST API 都在這裡（照各家官方文件的形狀）。"""

    def __init__(self):
        self.calls = []
        self.fail = None                     # 下一次回這個（Resp 或 Exception）
        self.closed = 0

    def close(self):
        self.closed += 1

    def request(self, method, url, headers=None, data=None, json=None, files=None, params=None, timeout=None,
                allow_redirects=True):
        headers = dict(headers or {})
        self.calls.append({"method": method, "url": url, "headers": headers, "data": data, "json": json,
                           "files": files, "params": params, "timeout": timeout, "redirects": allow_redirects})
        assert timeout, "every request must have a timeout"
        assert allow_redirects is False
        if self.fail is not None:
            fail, self.fail = self.fail, None
            if isinstance(fail, Exception):
                raise fail
            return fail
        u = urlsplit(url)
        host, path = u.hostname, u.path
        if host.endswith(".tts.speech.microsoft.com") or host.endswith(".api.cognitive.microsoft.com"):
            if headers.get("Ocp-Apim-Subscription-Key") != AZ_KEY:
                return Resp(401, {"error": {"code": "401", "message": "Access denied due to invalid subscription key."}})
            if path == "/cognitiveservices/v1":
                return Resp(200, content=b"ID3-azure-audio")
            if path == "/cognitiveservices/voices/list":
                return Resp(200, AZ_VOICES)
            if path == "/speechtotext/transcriptions:transcribe":
                return Resp(200, {"durationMilliseconds": 5000,
                                  "combinedPhrases": [{"text": "大家好。今天開會。"}],
                                  "phrases": [{"offsetMilliseconds": 0, "durationMilliseconds": 1500, "text": "大家好。"},
                                              {"offsetMilliseconds": 2000, "durationMilliseconds": 2500,
                                               "text": "今天開會。"}]})
        if host == "api.openai.com":
            if headers.get("Authorization") != f"Bearer {OA_KEY}":
                return Resp(401, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}})
            if path == "/v1/audio/speech":
                return Resp(200, content=b"ID3-openai-audio")
            if path == "/v1/audio/transcriptions":
                fmt = data["response_format"]
                if fmt == "srt":
                    return Resp(200, text="1\n00:00:00,000 --> 00:00:01,500\n大家好\n")
                if fmt == "vtt":
                    return Resp(200, text="WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n大家好\n")
                return Resp(200, text="大家好，今天開會。\n")
        if host == "texttospeech.googleapis.com" or host == "speech.googleapis.com":
            if headers.get("X-goog-api-key") != GG_KEY:
                return Resp(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                            "status": "INVALID_ARGUMENT"}})
            if path == "/v1/text:synthesize":
                return Resp(200, {"audioContent": base64.b64encode(b"ID3-google-audio").decode()})
            if path == "/v1/voices":
                return Resp(200, GG_VOICES)
            if path.endswith("/speech:recognize"):
                return Resp(200, {"results": [
                    {"alternatives": [{"transcript": "大家好", "words": [
                        {"startTime": "0s", "endTime": "0.500s", "word": "大家"},
                        {"startTime": "0.500s", "endTime": "1.200s", "word": "好"}]}]},
                    {"alternatives": [{"transcript": "今天開會", "words": [
                        {"startTime": "2s", "endTime": "3.250s", "word": "今天開會"}]}]}]})
        return Resp(404, {"error": {"message": "no such endpoint"}})

    def last(self):
        return self.calls[-1] if self.calls else None


@pytest.fixture
def speech(host_bot):
    """(bot, fake, mod, pack)：裝好 packs/speech、HTTP 換成 FakeSpeech、時間固定；keys＝先存進 pack secret 的金鑰。"""
    def factory(keys=None, *, raw=None, script=None):
        bot = host_bot(script, raw=raw)
        mod = sys.modules["synox_pack_speech"]
        fake = FakeSpeech()
        mod.new_session = lambda: fake
        mod.now = lambda: WHEN
        pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "speech")
        if keys:
            set_keys(bot, pack, keys)
        return bot, fake, mod, pack
    return factory


def set_keys(bot, pack, keys, user=OWNER_ID):
    """跟網頁工具頁「🔌 連接」同一條路：存進保管箱的 pack_secrets。"""
    pack_secrets.of(bot.app).update("speech", pack.secret_fields, keys, user=user)


def put_audio(pack, name, data=b"RIFF-fake-wav-data", user=OWNER_ID, sub="inbox"):
    path = pack.path_of(user, sub, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def doc(bot):
    """最後一個傳出去的檔：(路徑, caption)。"""
    files = [f for f in bot.api.files if f[0] == "document"]
    assert files, "沒有傳檔案"
    return files[-1][2], files[-1][3]


def all_text(bot, who=OWNER_ID):
    return "\n".join(bot.texts(chat_id=who))


# ── 安裝五關、宣告 ────────────────────────────────────────────────────────────

def test_manifest_and_static_scan_pass():
    m = read(os.path.join(PACKS, "speech"), strict=True)
    assert m.kind == "python" and m.access == "owner" and m.storage == "vault" and m.data == "user"
    assert m.capabilities == ["filesystem:own", "network"]
    assert m.version.endswith("-alpha.1") and m.stage == "alpha"
    report = scan.scan(m)
    assert report.problems(m) == [], report.format(m)
    assert report.required_capabilities == ["filesystem:own", "network"]
    assert pack_secrets.managed(m)                                  # 原始碼有 pack.secret → SYNOX 收金鑰
    names = [f["name"] for f in pack_secrets.managed_fields(m)]
    assert names == ["azure_key", "azure_region", "openai_key", "google_key"]
    assert all(not f["required"] for f in pack_secrets.managed_fields(m))      # 三家選一家就好
    secret = {f["name"]: f["secret"] for f in pack_secrets.managed_fields(m)}
    assert secret == {"azure_key": True, "azure_region": False, "openai_key": True, "google_key": True}


def test_commands_and_ai_tools(speech):
    bot, _, _, _ = speech()
    reg = bot.app.registry
    owner = bot.app.users.get(OWNER_ID)
    for name in ("add", "stt", "voices", "status", "settings", "set", "files"):
        assert policy.decide(bot.app, owner, reg.node(f"speech.{name}"), via=AI).allowed, name
    assert reg.node("speech.add").default


# ── 還沒設定 ──────────────────────────────────────────────────────────────────

def test_not_configured_says_how_and_does_not_connect(speech):
    bot, fake, _, pack = speech()
    bot.say(OWNER_ID, "/speech 你好")
    assert "還沒設定任何語音供應商的金鑰" in bot.last(OWNER_ID) and "連接" in bot.last(OWNER_ID)
    put_audio(pack, "a.wav")
    bot.say(OWNER_ID, "/speech_stt a.wav")
    assert "還沒設定任何" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/speech_status")
    assert "還沒設定" in bot.last(OWNER_ID) and "python -m synox connect speech" in bot.last(OWNER_ID)
    assert fake.calls == []


def test_status_lists_each_provider(speech):
    bot, fake, _, _ = speech({"openai_key": OA_KEY, "azure_key": AZ_KEY})
    bot.say(OWNER_ID, "/speech_status")
    text = bot.last(OWNER_ID)
    assert "OpenAI：✅ 已設定" in text and "Google Cloud：—" in text
    assert "金鑰有了，區域沒填" in text                                # Azure 沒有區域＝不算設好
    assert "現在是 OpenAI" in text
    assert OA_KEY not in text and AZ_KEY not in text and fake.calls == []


# ── 文字轉語音 ────────────────────────────────────────────────────────────────

def test_tts_azure_sends_ssml_and_returns_a_file(speech):
    bot, fake, _, pack = speech(ALL_KEYS)
    bot.say(OWNER_ID, "/speech 你好 <&> 世界")
    call = fake.last()
    assert call["method"] == "POST"
    assert call["url"] == f"https://{REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    assert call["headers"]["Ocp-Apim-Subscription-Key"] == AZ_KEY
    assert call["headers"]["Content-Type"] == "application/ssml+xml"
    assert call["headers"]["X-Microsoft-OutputFormat"] == "audio-24khz-48kbitrate-mono-mp3"
    ssml = call["data"].decode("utf-8")
    assert "xml:lang=\"zh-TW\"" in ssml and "name=\"zh-TW-HsiaoChenNeural\"" in ssml
    assert "你好 &lt;&amp;&gt; 世界" in ssml and "rate=\"+0%\"" in ssml
    path, caption = doc(bot)
    assert path == pack.path_of(OWNER_ID, "out", "tts-20261109-093000.mp3")
    assert open(path, "rb").read() == b"ID3-azure-audio"
    assert "Azure Speech" in caption
    assert "唸好了" in bot.last(OWNER_ID) and "out/tts-20261109-093000.mp3" in bot.last(OWNER_ID)
    assert fake.closed == 1


def test_tts_openai_uses_settings(speech):
    bot, fake, _, _ = speech(ALL_KEYS)
    bot.say(OWNER_ID, "/speech_set tts openai")
    bot.say(OWNER_ID, "/speech_set voice nova")
    bot.say(OWNER_ID, "/speech_set rate 1.25")
    bot.say(OWNER_ID, "/speech_set audio wav")
    bot.say(OWNER_ID, "/speech Good morning")
    call = fake.last()
    assert call["url"] == "https://api.openai.com/v1/audio/speech"
    assert call["headers"]["Authorization"] == f"Bearer {OA_KEY}"
    assert call["json"] == {"model": "tts-1", "input": "Good morning", "voice": "nova", "response_format": "wav",
                            "speed": 1.25}
    path, _ = doc(bot)
    assert path.endswith(".wav") and open(path, "rb").read() == b"ID3-openai-audio"


def test_tts_google_key_in_header_not_url(speech):
    bot, fake, _, _ = speech({"google_key": GG_KEY})
    bot.say(OWNER_ID, "/speech Hello lang=en-US")
    call = fake.last()
    assert call["url"] == "https://texttospeech.googleapis.com/v1/text:synthesize"
    assert GG_KEY not in call["url"] and call["headers"]["X-goog-api-key"] == GG_KEY
    assert call["json"]["input"] == {"text": "Hello"}
    assert call["json"]["voice"] == {"languageCode": "en-US", "name": "en-US-Standard-C"}
    assert call["json"]["audioConfig"] == {"audioEncoding": "MP3", "speakingRate": 1.0}
    path, _ = doc(bot)
    assert open(path, "rb").read() == b"ID3-google-audio"


def test_tts_per_call_provider_and_voice(speech):
    bot, fake, _, _ = speech(ALL_KEYS)
    bot.say(OWNER_ID, "/speech 你好 provider=openai voice=onyx")
    assert fake.last()["json"]["voice"] == "onyx"
    bot.say(OWNER_ID, "/speech 你好 provider=openai voice=nobody")
    assert "OpenAI 沒有「nobody」" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/speech 你好 provider=yahoo")
    assert "不認得「yahoo」" in bot.last(OWNER_ID)


def test_tts_chosen_provider_without_key(speech):
    bot, fake, _, _ = speech({"openai_key": OA_KEY})
    bot.say(OWNER_ID, "/speech_set tts google")
    bot.say(OWNER_ID, "/speech 你好")
    assert "還沒設定 Google Cloud 的金鑰" in bot.last(OWNER_ID) and fake.calls == []


def test_text_limits(speech):
    bot, fake, mod, _ = speech({"google_key": GG_KEY})
    bot.say(OWNER_ID, "/speech " + "字" * 1700)                     # 5100 bytes：Google 不收
    assert "5000 bytes" in bot.last(OWNER_ID) and fake.calls == []
    mod.MAX_TEXT = 10
    bot.say(OWNER_ID, "/speech " + "字" * 11)
    assert "一次最多唸 10 字" in bot.last(OWNER_ID) and fake.calls == []


def test_out_folder_keeps_only_the_newest(speech):
    bot, fake, mod, pack = speech(ALL_KEYS)
    mod.MAX_KEEP = 2
    for minute in range(3):
        mod.now = lambda m=minute: datetime(2026, 11, 9, 9, m, 0)
        bot.say(OWNER_ID, f"/speech 第{minute}段")
    assert sorted(os.listdir(pack.path_of(OWNER_ID, "out"))) == ["tts-20261109-090100.mp3", "tts-20261109-090200.mp3"]


def test_same_second_gets_a_new_name(speech):
    bot, _, _, pack = speech(ALL_KEYS)
    bot.say(OWNER_ID, "/speech 一")
    bot.say(OWNER_ID, "/speech 二")
    assert sorted(os.listdir(pack.path_of(OWNER_ID, "out"))) == ["tts-20261109-093000-2.mp3", "tts-20261109-093000.mp3"]


def test_channel_cannot_send_files(speech):
    bot, _, _, _ = speech(ALL_KEYS)
    bot.api.send_document = lambda *a, **kw: {"ok": False}
    bot.say(OWNER_ID, "/speech 你好")
    assert "這個對話傳不了檔案" in bot.last(OWNER_ID) and "out/tts-" in bot.last(OWNER_ID)


# ── 語音轉文字 ────────────────────────────────────────────────────────────────

def test_stt_openai_text_srt_vtt(speech):
    bot, fake, _, pack = speech({"openai_key": OA_KEY})
    put_audio(pack, "meeting.mp3", b"ID3-meeting")
    bot.say(OWNER_ID, "/speech_stt meeting.mp3")
    call = fake.last()
    assert call["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert call["data"] == {"model": "whisper-1", "response_format": "text", "language": "zh"}
    assert call["files"]["file"] == ("meeting.mp3", b"ID3-meeting", "audio/mpeg")
    assert "大家好，今天開會。" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/speech_stt inbox/meeting.mp3 format=srt")
    assert fake.last()["data"]["response_format"] == "srt"
    path, _ = doc(bot)
    assert path.endswith(".srt") and "00:00:00,000 --> 00:00:01,500" in open(path, encoding="utf-8").read()
    bot.say(OWNER_ID, "/speech_set text vtt")
    bot.say(OWNER_ID, "/speech_stt meeting.mp3 lang=en-US")
    assert fake.last()["data"] == {"model": "whisper-1", "response_format": "vtt", "language": "en"}
    assert doc(bot)[0].endswith(".vtt")


def test_stt_azure_fast_transcription_to_srt(speech):
    bot, fake, _, pack = speech(ALL_KEYS)
    put_audio(pack, "a.wav")
    bot.say(OWNER_ID, "/speech_stt a.wav format=srt")
    call = fake.last()
    assert call["url"] == (f"https://{REGION}.api.cognitive.microsoft.com/speechtotext/transcriptions:transcribe"
                           "?api-version=2024-11-15")
    assert call["headers"]["Ocp-Apim-Subscription-Key"] == AZ_KEY
    assert call["files"]["audio"] == ("a.wav", b"RIFF-fake-wav-data", "audio/wav")
    assert json.loads(call["files"]["definition"][1])["locales"] == ["zh-TW"]
    text = open(doc(bot)[0], encoding="utf-8").read()
    assert text == ("1\n00:00:00,000 --> 00:00:01,500\n大家好。\n\n"
                    "2\n00:00:02,000 --> 00:00:04,500\n今天開會。\n")
    bot.say(OWNER_ID, "/speech_stt a.wav")
    assert "大家好。今天開會。" in bot.last(OWNER_ID)


def test_stt_google_words_to_vtt(speech):
    bot, fake, _, pack = speech({"google_key": GG_KEY})
    put_audio(pack, "a.wav")
    bot.say(OWNER_ID, "/speech_stt a.wav format=vtt")
    call = fake.last()
    assert call["url"] == "https://speech.googleapis.com/v1/speech:recognize"
    assert call["headers"]["X-goog-api-key"] == GG_KEY
    assert call["json"]["config"]["languageCode"] == "zh-TW" and "encoding" not in call["json"]["config"]
    assert base64.b64decode(call["json"]["audio"]["content"]) == b"RIFF-fake-wav-data"
    text = open(doc(bot)[0], encoding="utf-8").read()
    assert text.startswith("WEBVTT\n") and "00:00:00.000 --> 00:00:01.200\n大家好" in text
    assert "00:00:02.000 --> 00:00:03.250\n今天開會" in text
    put_audio(pack, "b.mp3")
    bot.say(OWNER_ID, "/speech_stt b.mp3")
    assert fake.last()["url"] == "https://speech.googleapis.com/v1p1beta1/speech:recognize"
    assert fake.last()["json"]["config"]["encoding"] == "MP3"
    put_audio(pack, "c.m4a")
    n = len(fake.calls)
    bot.say(OWNER_ID, "/speech_stt c.m4a")
    assert "Google 目前只接 wav、flac、mp3" in bot.last(OWNER_ID) and len(fake.calls) == n


def test_stt_long_text_goes_out_as_a_file(speech):
    bot, fake, mod, pack = speech({"openai_key": OA_KEY})
    mod.MAX_REPLY = 5
    put_audio(pack, "a.mp3")
    bot.say(OWNER_ID, "/speech_stt a.mp3")
    path, _ = doc(bot)
    assert path.endswith(".txt") and "大家好" in open(path, encoding="utf-8").read()
    assert "開頭" in bot.last(OWNER_ID)


@pytest.mark.parametrize("name, needle", [
    ("../secret.mp3", "檔名不對"),
    ("../../x.wav", "檔名不對"),
    ("/etc/a.wav", "檔名不對"),
    ("C:/Windows/a.wav", "檔名不對"),
    ("..\\a.wav", "檔名不對"),
    (".env", "不是收的音檔格式"),
    ("id_rsa", "不是收的音檔格式"),
    ("notes.txt", "不是收的音檔格式"),
    ("missing.mp3", "找不到 missing.mp3"),
])
def test_stt_only_reads_own_folder(speech, name, needle):
    bot, fake, _, _ = speech(ALL_KEYS)
    bot.say(OWNER_ID, f"/speech_stt {name}")
    assert needle in bot.last(OWNER_ID), bot.last(OWNER_ID)
    assert fake.calls == []


def test_stt_size_limits(speech):
    bot, fake, mod, pack = speech(ALL_KEYS)
    mod.MAX_AUDIO = 10
    put_audio(pack, "big.wav", b"x" * 11)
    bot.say(OWNER_ID, "/speech_stt big.wav")
    assert "音檔太大" in bot.last(OWNER_ID) and fake.calls == []
    put_audio(pack, "empty.wav", b"")
    bot.say(OWNER_ID, "/speech_stt empty.wav")
    assert "是空的" in bot.last(OWNER_ID) and fake.calls == []
    mod.MAX_AUDIO = 1000
    mod.GOOGLE_MAX_AUDIO = 10
    put_audio(pack, "g.wav", b"y" * 20)
    bot.say(OWNER_ID, "/speech_stt g.wav provider=google")
    assert "Google 一次只收" in bot.last(OWNER_ID) and fake.calls == []


def test_stt_nothing_heard(speech):
    bot, fake, _, pack = speech({"google_key": GG_KEY})
    put_audio(pack, "a.wav")
    fake.fail = Resp(200, {})
    bot.say(OWNER_ID, "/speech_stt a.wav")
    assert "沒有聽出任何字" in bot.last(OWNER_ID)


# ── 錯誤翻成人話、金鑰不外洩 ──────────────────────────────────────────────────

@pytest.mark.parametrize("keys, fail, needle", [
    ({"openai_key": OA_KEY}, Resp(401, {"error": {"message": "Incorrect API key"}}), "不認這把金鑰"),
    ({"openai_key": OA_KEY}, Resp(429, {"error": {"message": "You exceeded your current quota",
                                                  "type": "insufficient_quota"}}), "額度用完了"),
    ({"openai_key": OA_KEY}, Resp(429, {"error": {"message": "Rate limit reached"}}), "太頻繁"),
    ({"google_key": GG_KEY}, Resp(400, {"error": {"message": "API key not valid. Please pass a valid API key."}}),
     "不認這把金鑰"),
    ({"google_key": GG_KEY}, Resp(403, {"error": {"message": "Cloud Text-to-Speech API has not been used in project",
                                                  "status": "PERMISSION_DENIED",
                                                  "details": [{"reason": "SERVICE_DISABLED"}]}}), "還沒啟用"),
    ({"google_key": GG_KEY}, Resp(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}}),
     "配額"),
    ({"azure_key": AZ_KEY, "azure_region": REGION}, Resp(404, text="Not Found"), "區域可能填錯"),
    ({"azure_key": AZ_KEY, "azure_region": REGION}, Resp(403, text=""), "區域跟金鑰不是同一個資源"),
    ({"openai_key": OA_KEY}, Resp(400, {"error": {"message": "Invalid voice"}}), "Invalid voice"),
    ({"openai_key": OA_KEY}, Resp(503, text="Service Unavailable"), "暫時出問題"),
    ({"openai_key": OA_KEY}, requests.ConnectionError("boom " + OA_KEY), "連不上 OpenAI"),
    ({"openai_key": OA_KEY}, requests.Timeout("slow"), "等太久"),
])
def test_errors_become_one_sentence(speech, keys, fail, needle):
    bot, fake, _, _ = speech(keys)
    fake.fail = fail
    bot.say(OWNER_ID, "/speech 你好")
    reply = bot.last(OWNER_ID)
    assert needle in reply, reply
    for key in (OA_KEY, AZ_KEY, GG_KEY):
        assert key not in reply
    assert bot.api.files == []


def test_wrong_key_from_the_service(speech):
    bot, _, _, _ = speech({"openai_key": "openai-wrong-key-0000000000000000000"})
    bot.say(OWNER_ID, "/speech 你好")
    assert "OpenAI 不認這把金鑰（401）" in bot.last(OWNER_ID)
    assert "openai-wrong" not in bot.last(OWNER_ID)


def test_bad_azure_region_never_becomes_a_url(speech):
    bot, fake, _, _ = speech({"azure_key": AZ_KEY, "azure_region": "evil.example/x"})
    bot.say(OWNER_ID, "/speech_set tts azure")
    bot.say(OWNER_ID, "/speech 你好")
    assert "Azure 的區域格式不對" in bot.last(OWNER_ID) and fake.calls == []


def test_keys_stay_in_the_vault_and_out_of_logs(speech, caplog):
    bot, fake, _, pack = speech()
    with caplog.at_level(logging.DEBUG):
        set_keys(bot, pack, ALL_KEYS)
        bot.say(OWNER_ID, "/speech 你好")
        bot.say(OWNER_ID, "/speech_status")
        bot.say(OWNER_ID, "/speech_settings")
    vault = bot.app.extras["vault"]
    raw = open(vault.path("pack_secrets"), "rb").read()
    for key in (AZ_KEY, OA_KEY, GG_KEY):
        assert key.encode() not in raw                              # 保管箱是密文
        assert key not in all_text(bot)
        assert key not in "\n".join(r.getMessage() for r in caplog.records)
    stored = json.dumps(vault.load_json("speech") or {})            # 工具自己的資料只有設定
    assert all(key not in stored for key in (AZ_KEY, OA_KEY, GG_KEY))


# ── 設定 ─────────────────────────────────────────────────────────────────────

def test_settings_show_and_change(speech):
    bot, _, _, _ = speech({"openai_key": OA_KEY})
    bot.say(OWNER_ID, "/speech_settings")
    text = bot.last(OWNER_ID)
    for word in ("tts", "stt", "lang", "voice", "rate", "audio", "text", "zh-TW", "1.0 倍", "mp3"):
        assert word in text
    bot.say(OWNER_ID, "/speech_set 語速 1.5")
    assert "rate" in bot.last(OWNER_ID) and "1.5 倍" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/speech_set lang en-US")
    bot.say(OWNER_ID, "/speech_set stt google")
    bot.say(OWNER_ID, "/speech_set text srt")
    bot.say(OWNER_ID, "/speech_set voice shimmer")                  # tts=auto → 現在用 OpenAI
    assert "OpenAI 的聲音改成 shimmer" in bot.last(OWNER_ID)
    vault = bot.app.extras["vault"]
    data = vault.load_json("speech")
    assert data["rate"] == "1.5" and data["lang"] == "en-US" and data["stt"] == "google" and data["text"] == "srt"
    assert data["voices"] == {"openai": "shimmer"}
    bot.say(OWNER_ID, "/speech_set rate 預設")
    bot.say(OWNER_ID, "/speech_set voice 預設")
    data = vault.load_json("speech")
    assert data["rate"] == "1.0" and data["voices"] == {}


@pytest.mark.parametrize("args, needle", [
    ("color red", "不認得這個設定"),
    ("rate 3", "0.5 到 2.0"),
    ("rate fast", "語速要是數字"),
    ("lang 中文", "語言代碼不對"),
    ("audio ogg", "mp3 或 wav"),
    ("text docx", "text、srt、vtt"),
    ("tts yahoo", "auto、azure、openai、google"),
    ("voice bad/name", "聲音名字"),
])
def test_settings_reject_bad_values(speech, args, needle):
    bot, _, _, _ = speech({"openai_key": OA_KEY})
    bot.say(OWNER_ID, f"/speech_set {args}")
    assert needle in bot.last(OWNER_ID), bot.last(OWNER_ID)
    assert not (bot.app.extras["vault"].load_json("speech") or {}).get("rate") == "3"


def test_voice_needs_a_provider_first(speech):
    bot, _, _, _ = speech()
    bot.say(OWNER_ID, "/speech_set voice nova")
    assert "先決定 TTS 用哪一家" in bot.last(OWNER_ID)


# ── 聲音清單 ──────────────────────────────────────────────────────────────────

def test_voices_from_api_and_builtin(speech):
    bot, fake, _, _ = speech(ALL_KEYS)
    bot.say(OWNER_ID, "/speech_voices azure zh-TW")
    assert fake.last()["url"] == f"https://{REGION}.tts.speech.microsoft.com/cognitiveservices/voices/list"
    text = bot.last(OWNER_ID)
    assert "zh-TW-YunJheNeural（雲哲、Male）" in text and "en-US-JennyNeural" not in text
    bot.say(OWNER_ID, "/speech_voices google")
    assert fake.last()["params"] == {"languageCode": "zh-TW"} and GG_KEY not in fake.last()["url"]
    assert "cmn-TW-Wavenet-B（男）" in bot.last(OWNER_ID)
    n = len(fake.calls)
    bot.say(OWNER_ID, "/speech_voices openai")
    assert "nova" in bot.last(OWNER_ID) and "內建清單" in bot.last(OWNER_ID) and len(fake.calls) == n


def test_voices_without_key_use_builtin_list(speech):
    bot, fake, _, _ = speech()
    bot.say(OWNER_ID, "/speech_voices azure ja-JP")
    assert "ja-JP-NanamiNeural" in bot.last(OWNER_ID) and "內建清單" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/speech_voices google xx-YY")
    assert "沒有 xx-YY 的聲音" in bot.last(OWNER_ID)
    assert fake.calls == []


# ── 檔案、面板 ────────────────────────────────────────────────────────────────

def test_files_lists_inbox_and_out(speech):
    bot, _, _, pack = speech(ALL_KEYS)
    put_audio(pack, "meeting.mp3")
    bot.say(OWNER_ID, "/speech 你好")
    bot.say(OWNER_ID, "/speech_files")
    text = bot.last(OWNER_ID)
    assert "inbox/" in text and "meeting.mp3" in text and "tts-20261109-093000.mp3" in text


def test_panel_route_has_no_key_values(speech):
    bot, _, _, pack = speech(ALL_KEYS)
    out = pack.web_routes["settings"](bot.app.users.get(OWNER_ID))
    assert out["keys"] == {"azure": True, "openai": True, "google": True}
    assert out["settings"]["lang"] == "zh-TW"
    dumped = json.dumps(out, ensure_ascii=False)
    assert all(key not in dumped for key in (AZ_KEY, OA_KEY, GG_KEY)) and REGION not in dumped


# ── 純函式 ────────────────────────────────────────────────────────────────────

def test_subtitle_helpers():
    from conftest import load_module
    mod = load_module("speech")
    segs = [(0, 1500, "a"), (3723456, 3725000, "b")]
    assert mod.to_srt(segs) == "1\n00:00:00,000 --> 00:00:01,500\na\n\n2\n01:02:03,456 --> 01:02:05,000\nb\n"
    assert mod.to_vtt(segs).startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\na\n")
    assert mod.google_seconds("1.300s") == 1300 and mod.google_seconds(None) == 0
    assert mod.azure_rate("1.2") == "+20%" and mod.azure_rate("0.8") == "-20%"
    assert mod.provider_word("微軟") == "azure" and mod.provider_word("x") is None


# ── 權限、伺服器版：每個人自己的（ADR-0006 第 6 節）────────────────────────────

def test_stranger_cannot_see_it(speech):
    bot, fake, _, _ = speech(ALL_KEYS)
    bot.say(OTHER_ID, "/speech 你好", chat_id=OTHER_ID)
    assert "唸好了" not in bot.last(chat_id=OTHER_ID) and fake.calls == []


def test_server_mode_owner_only_and_per_user_data(speech):
    bot, fake, _, pack = speech(ALL_KEYS, raw={"mode": "server"})
    bot.make(MEMBER, BasicRoles.MEMBER, "member")
    member = bot.app.users.get(MEMBER)
    reg = bot.app.registry
    for name in ("add", "stt", "set", "status"):
        node = reg.node(f"speech.{name}")
        assert not reg.visible(member, node)
        assert not policy.decide(bot.app, member, node, via=AI).allowed
    bot.say(MEMBER, "/speech 你好", chat_id=MEMBER)
    assert "未知指令" in bot.last(chat_id=MEMBER) and fake.calls == []
    # 擁有者的金鑰、設定、檔案都在他自己的那一份；換一個人什麼都拿不到
    bot.say(OWNER_ID, "/speech_set rate 1.5")
    bot.say(OWNER_ID, "/speech 你好")
    assert doc(bot)[0].startswith(os.path.join(bot.app.config.data_dir, "users", str(OWNER_ID)))
    vault = bot.app.extras["vault"]
    assert vault.for_user(OWNER_ID).load_json("speech")["rate"] == "1.5"
    assert not os.path.exists(vault.path("speech"))                 # 不是共用的那一份
    assert pack.store.of(MEMBER).load(None) is None
    assert pack.secret("openai_key", OWNER_ID) == OA_KEY
    assert pack.secret("openai_key", MEMBER) is None               # 別人的保管箱裡沒有
    assert pack.path_of(MEMBER) != pack.path_of(OWNER_ID)
    assert not os.listdir(pack.path_of(MEMBER))
    route = pack.web_routes["settings"]
    assert route(member)["keys"] == {"azure": False, "openai": False, "google": False}
    assert route(member)["settings"]["rate"] == "1.0"


def test_server_mode_two_people_files_are_separate(speech):
    """兩個人各放一個音檔：A 的名字 B 拿不到（各自的 path_of）。"""
    bot, _, _, pack = speech(ALL_KEYS, raw={"mode": "server"})
    set_keys(bot, pack, ALL_KEYS, user=MEMBER)
    put_audio(pack, "mine.wav", user=MEMBER)
    bot.say(OWNER_ID, "/speech_stt mine.wav")
    assert "找不到 mine.wav" in bot.last(OWNER_ID)
    put_audio(pack, "owner.wav")
    bot.say(OWNER_ID, "/speech_stt owner.wav")
    assert "轉好了" in bot.last(OWNER_ID)
