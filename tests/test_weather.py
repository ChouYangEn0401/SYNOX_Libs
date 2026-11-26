# -*- coding: utf-8 -*-
"""天氣與穿衣（packs/weather）：全部用假的 HTTP session（記憶體裡的小 Open-Meteo 與人事總處網頁），不連網、不開埠。
真的 SYNOX（synox_bot）：權限、保管箱加密、伺服器版每人一份（ADR-0006 第 6 節 S1、S2、S5）。"""

import json
import os
import sys
from urllib.parse import urlsplit

import pytest
import requests

from isdtelekit.core.registry import AI
from isdtelekit.testing import OWNER_ID
from isdtelekit.users.roles import BasicRoles as R

from synox import policy
from synox.packs import scan
from synox.packs.manifest import read

from conftest import PACKS

FOLDER = os.path.join(PACKS, "weather")
A, B = 77, 78

HOSTS = ("api.open-meteo.com", "geocoding-api.open-meteo.com", "air-quality-api.open-meteo.com", "www.dgpa.gov.tw")


def forecast(temp=20.0, feels=21.0, code=3, rain=10, rain_mm=0.0, uv=4.0, gust=20.0, wind=10.0, humid=70,
             fmin=17.0, fmax=24.0, tmin=16.0, tmax=23.0, days=3):
    dates = [f"2026-11-{9 + i:02d}" for i in range(days)]          # 2026-11-09 是星期一
    return {
        "current": {"time": "2026-11-09T09:30", "temperature_2m": temp, "apparent_temperature": feels,
                    "relative_humidity_2m": humid, "precipitation": 0.0, "weather_code": code,
                    "wind_speed_10m": wind, "wind_gusts_10m": gust, "uv_index": 1.0, "is_day": 1},
        "daily": {"time": dates, "weather_code": [code] * days,
                  "temperature_2m_max": [tmax] * days, "temperature_2m_min": [tmin] * days,
                  "apparent_temperature_max": [fmax] * days, "apparent_temperature_min": [fmin] * days,
                  "precipitation_probability_max": [rain] * days, "precipitation_sum": [rain_mm] * days,
                  "uv_index_max": [uv] * days, "wind_speed_10m_max": [wind] * days, "wind_gusts_10m_max": [gust] * days},
    }


DGPA_CLOSED = """<HTML><HEAD><TITLE>行政院人事行政總處全球資訊網-天然災害停止上班及上課情形查詢</TITLE>
<script>var x = "<tr><td>臺南市</td><td>假的</td></tr>";</script><style>td{color:red}</style></HEAD><BODY>
<div class="Header_YMD">115年 9月 3日 天然災害停止上班及上課情形</div>
<h4>更新時間：2026/09/03 21:05:10</h4>
<TABLE id="Table" rules="ALL">
<TR class="Table_Head"><TH>縣市名稱</TH><TH>是否停止上班上課情形 </TH></TR>
<TBODY class="Table_Body">
<TR><TD colspan=2><h2>北部地區</h2></TD></TR>
<TR><TD headers="city_Name"><FONT>臺北市</FONT></TD><TD headers="StopWorkSchool_Info"><FONT color=#ff0000>明天停止上班、停止上課。</FONT><br></TD></TR>
<TR><TD headers="city_Name"><FONT>新北市</FONT></TD><TD headers="StopWorkSchool_Info"><FONT color=#ff0000>明天停止上班、停止上課。</FONT><br><FONT>烏來區&nbsp;今天已停止上班、停止上課。</FONT></TD></TR>
<TR><TD headers="city_Name"><FONT>桃園市</FONT></TD><TD headers="StopWorkSchool_Info"><FONT>明天照常上班、照常上課。</FONT></TD></TR>
<TR><TD colspan=2><h2>東部地區</h2></TD></TR>
<TR><TD headers="city_Name"><FONT>花蓮縣</FONT></TD><TD headers="StopWorkSchool_Info"><FONT>明天上午照常上班上課，下午停止上班、停止上課。</FONT></TD></TR>
<TR style="background:#eee"><TD colspan=3><p>1.若欲進入本總處網站首頁版面</p></TD></TR>
</TBODY></TABLE></BODY></HTML>"""

DGPA_NONE = """<HTML><HEAD><TITLE>天然災害停止上班及上課情形查詢</TITLE></HEAD><BODY>
<div>115年 11月 9日 天然災害停止上班及上課情形</div><h4>更新時間：2026/11/09 09:44:15</h4>
<TABLE id="Table"><TR><TH>縣市名稱</TH><TH>是否停止上班上課情形</TH></TR>
<TBODY><TR><TD colspan='2'><FONT><h2>無停班停課訊息。</h2><br></FONT></TD></TR></TBODY></TABLE></BODY></HTML>"""


class Resp:
    def __init__(self, status, data=None, content=None):
        self.status_code = status
        if content is not None:
            self.content = content
        else:
            self.content = b"" if data is None else json.dumps(data).encode("utf-8")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class FakeWeb:
    """requests.Session 的替身：get(url, params=, timeout=, headers=) → 記憶體裡的 Open-Meteo 與人事總處。"""

    def __init__(self):
        self.calls = []
        self.fkw = {}                        # forecast() 的參數（天數照請求的 forecast_days）
        self.air = {"current": {"us_aqi": 65, "pm2_5": 22.6, "pm10": 26.5, "uv_index": 1.6}}
        self.geo = {"Taipei": [{"name": "台北市", "latitude": 25.05, "longitude": 121.53, "country_code": "TW",
                                "admin2": "臺北市", "admin1": "臺灣省 or 台灣省"}],
                    "板橋": [{"name": "板橋", "latitude": 38.23, "longitude": 126.54, "country_code": "KP"},
                             {"name": "板橋", "latitude": 23.66, "longitude": 120.52, "country_code": "TW", "admin2": "雲林縣"}],
                    "Paris": [{"name": "巴黎", "latitude": 48.85, "longitude": 2.35, "country_code": "FR", "country": "法國"}]}
        self.dgpa = DGPA_CLOSED.encode("utf-8")
        self.fail = {}                       # host → Resp 或 Exception

    def get(self, url, params=None, timeout=None, headers=None):
        host = urlsplit(url).hostname
        self.calls.append({"host": host, "url": url, "params": dict(params or {})})
        assert host in HOSTS, url
        assert url.startswith("https://"), url
        assert timeout, "every request must have a timeout"
        if host in self.fail:
            fail = self.fail[host]
            if isinstance(fail, Exception):
                raise fail
            return fail
        if host == "geocoding-api.open-meteo.com":
            return Resp(200, {"results": self.geo[params["name"]]} if params["name"] in self.geo else {"generationtime_ms": 0.1})
        if host == "api.open-meteo.com":
            return Resp(200, forecast(days=int(params["forecast_days"]), **self.fkw))
        if host == "air-quality-api.open-meteo.com":
            return Resp(200, self.air)
        return Resp(200, content=self.dgpa)

    def hosts(self):
        return [c["host"] for c in self.calls]


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
def wbot(synox_bot, web):
    def factory(mode="personal"):
        bot = synox_bot("weather", mode=mode)
        sys.modules["synox_pack_weather"].new_session = lambda: web
        return bot
    return factory


def module():
    return sys.modules["synox_pack_weather"]


# ── 第 1、2 關 ────────────────────────────────────────────────────

def test_manifest_and_static_scan_pass():
    m = read(FOLDER, strict=True)
    assert m.kind == "python" and m.access == "owner" and m.capabilities == ["network"] and m.storage == "vault"
    assert m.stage == "alpha" and m.version.startswith("0.1.0-alpha")
    report = scan.scan(m)
    assert report.problems(m) == [] and report.required_capabilities == ["network"]
    assert not any(f.kind == "env" for f in report.findings)


def test_no_network_before_anything_is_asked(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx_settings")
    assert "目前設定" in bot.last(OWNER_ID) and web.calls == []


# ── 天氣與預報 ───────────────────────────────────────────────────

def test_no_place_asks_how(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx")
    assert "要查哪裡" in bot.last(OWNER_ID) and "/wx_set 地點" in bot.last(OWNER_ID) and web.calls == []


def test_weather_for_county_uses_builtin_table(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx 台北")
    reply = bot.last(OWNER_ID)
    assert web.hosts() == ["api.open-meteo.com"]                         # 縣市不用問地名 API
    p = web.calls[0]["params"]
    assert abs(p["latitude"] - 25.04) < 0.1 and abs(p["longitude"] - 121.56) < 0.1 and p["forecast_days"] == 3
    assert p["timezone"] == "Asia/Taipei"
    assert "臺北市 現在 20.0°C" in reply and "體感 21.0°C" in reply and "陰" in reply
    assert "未來 3 天" in reply and "今天 11/9（一）" in reply and "明天 11/10（二）" in reply
    assert "👕" in reply and "Open-Meteo" in reply


def test_weather_days_trailing_number_and_field(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx 高雄 5")
    assert web.calls[-1]["params"]["forecast_days"] == 5 and "高雄市" in bot.last(OWNER_ID)
    assert "未來 5 天" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx 花蓮 days=7")
    assert web.calls[-1]["params"]["forecast_days"] == 7 and "花蓮縣" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx 台東 days=abc")
    assert "天數要是" in bot.last(OWNER_ID)


def test_geocoding_fallback_prefers_taiwan(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx Taipei")
    assert web.hosts() == ["geocoding-api.open-meteo.com", "api.open-meteo.com"]
    assert web.calls[0]["params"]["language"] == "zh"
    assert "台北市・臺灣省" not in bot.last(OWNER_ID) and "台北市" in bot.last(OWNER_ID)
    web.calls.clear()
    bot.say(OWNER_ID, "/wx 板橋")
    assert abs(web.calls[-1]["params"]["latitude"] - 23.66) < 0.01            # 台灣的那一個，不是第一個
    bot.say(OWNER_ID, "/wx Paris")
    assert "巴黎" in bot.last(OWNER_ID) and abs(web.calls[-1]["params"]["latitude"] - 48.85) < 0.01


def test_unknown_place_is_plain_chinese(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx 不存在的地方")
    reply = bot.last(OWNER_ID)
    assert "找不到「不存在的地方」" in reply and "座標" in reply
    assert "api.open-meteo.com" not in web.hosts()
    tried = [c["params"]["name"] for c in web.calls]
    assert "不存在的地方市" in tried and "不存在的地方縣" in tried           # 中文多試幾種寫法


def test_county_with_district_falls_back_to_county(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx 台北市某某區")
    assert "先用臺北市的天氣" in bot.last(OWNER_ID) and "臺北市 現在" in bot.last(OWNER_ID)


def test_coordinates(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx 25.03,121.56")
    assert web.hosts() == ["api.open-meteo.com"] and web.calls[0]["params"]["latitude"] == 25.03
    bot.say(OWNER_ID, "/wx 95,121")
    assert "座標超出範圍" in bot.last(OWNER_ID)


def test_network_and_server_errors_are_plain_chinese(wbot, web):
    bot = wbot()
    web.fail["api.open-meteo.com"] = requests.ConnectionError("boom")
    bot.say(OWNER_ID, "/wx 台北")
    assert "連不上天氣資料" in bot.last(OWNER_ID) and "boom" not in bot.last(OWNER_ID)
    web.fail["api.open-meteo.com"] = Resp(400, {"error": True, "reason": "Latitude must be in range"})
    bot.say(OWNER_ID, "/wx 台北")
    assert "HTTP 400" in bot.last(OWNER_ID) and "等一下再試" in bot.last(OWNER_ID)
    web.fail["api.open-meteo.com"] = Resp(200, content=b"<html>not json</html>")
    bot.say(OWNER_ID, "/wx 台北")
    assert "回了錯誤" in bot.last(OWNER_ID)
    web.fail["api.open-meteo.com"] = Resp(200, {"current": "?"})
    bot.say(OWNER_ID, "/wx 台北")
    assert "格式看不懂" in bot.last(OWNER_ID)


# ── 穿衣建議 ─────────────────────────────────────────────────────

def test_wear_rules_rain_uv_wind_cold(wbot):
    wbot()
    m = module()
    rainy = m.wear_advice(forecast(rain=80, code=63))
    assert any("雨傘" in b for b in rainy["bring"]) and any("鞋" in n for n in rainy["notes"])
    maybe = m.wear_advice(forecast(rain=40))
    assert any("折疊傘" in b for b in maybe["bring"])
    sunny = m.wear_advice(forecast(uv=9, feels=31, fmax=34, fmin=27, tmin=27, tmax=33, code=0))
    assert "短袖" in sunny["wear"] and any("防曬乳" in b for b in sunny["bring"]) and any("太陽眼鏡" in b for b in sunny["bring"])
    windy = m.wear_advice(forecast(gust=65))
    assert any("防風" in b for b in windy["bring"]) and any("陣風" in n for n in windy["notes"])
    cold = m.wear_advice(forecast(feels=6, fmax=9, fmin=4, tmin=5, tmax=10))
    assert "羽絨" in cold["wear"] and "手套" in cold["wear"]
    swing = m.wear_advice(forecast(feels=22, fmax=27, fmin=14, tmin=13, tmax=26))
    assert any("溫差" in n for n in swing["notes"]) and any("外套" in b for b in swing["bring"])


def test_wear_feel_shifts_thresholds_and_activity_changes_advice(wbot):
    wbot()
    m = module()
    mild = forecast(feels=19, fmax=19, fmin=17, tmin=17, tmax=19)
    normal, cold, hot = (m.wear_advice(mild, f) for f in ("普通", "怕冷", "怕熱"))
    assert "薄外套" in normal["wear"] and "毛衣" in cold["wear"] and "短袖" in hot["wear"]
    assert "怕冷" in cold["basis"] and "往下" in cold["basis"]
    work = m.wear_advice(forecast(feels=28, fmax=30, fmin=26, tmin=26, tmax=30), activity="上班")
    assert any("冷氣" in b for b in work["bring"])
    sport = m.wear_advice(forecast(feels=31, fmax=33, fmin=27, tmin=27, tmax=32), activity="運動")
    assert "排汗" in sport["wear"] and any("替換" in b for b in sport["bring"]) and any("中暑" in n for n in sport["notes"])
    outdoor = m.wear_advice(forecast(uv=4), activity="戶外")
    assert any("防蚊" in b for b in outdoor["bring"]) and any("防曬" in b for b in outdoor["bring"])
    travel = m.wear_advice(forecast(), activity="旅遊")
    assert any("好走的鞋" in b for b in travel["bring"])


def test_wear_command_reads_activity_from_text_or_field(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx_wear 運動 台北")
    reply = bot.last(OWNER_ID)
    assert "臺北市 今天穿什麼（運動）" in reply and "穿：" in reply and "帶：" in reply
    assert web.calls[-1]["params"]["forecast_days"] == 1
    bot.say(OWNER_ID, "/wx_wear 高雄 上班")
    assert "高雄市 今天穿什麼（上班）" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx_wear 台中 activity=旅遊")
    assert "（旅遊）" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx_wear 台中 activity=潛水")
    assert "活動類型只認" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx_set 活動 戶外")
    bot.say(OWNER_ID, "/wx_wear 台中")
    assert "（戶外）" in bot.last(OWNER_ID)


# ── 空氣品質與 UV ────────────────────────────────────────────────

def test_air_quality_and_uv(wbot, web):
    bot = wbot()
    web.fkw = {"uv": 9}
    bot.say(OWNER_ID, "/wx_air 台中")
    reply = bot.last(OWNER_ID)
    assert "臺中市 空氣品質與紫外線" in reply and "AQI 65（普通）" in reply and "PM2.5 22.6" in reply
    assert "今天最高 9.0（過量）" in reply and "擦防曬" in reply and "環境部" in reply
    assert web.hosts() == ["air-quality-api.open-meteo.com", "api.open-meteo.com"]
    web.air = {"current": {"us_aqi": 160, "pm2_5": 70.1, "pm10": 90, "uv_index": 0}}
    web.fail["api.open-meteo.com"] = requests.Timeout("slow")             # UV 最高值拿不到也照樣回
    bot.say(OWNER_ID, "/wx_air 台中")
    assert "🔴 AQI 160（對所有族群不健康）" in bot.last(OWNER_ID) and "口罩" in bot.last(OWNER_ID)


def test_aqi_levels(wbot):
    wbot()
    m = module()
    assert [m.aqi_level(v)[1] for v in (10, 80, 120, 180, 250, 400, None)] == \
        ["良好", "普通", "對敏感族群不健康", "對所有族群不健康", "非常不健康", "危害", "不明"]
    assert [m.uv_level(v) for v in (1, 4, 7, 9, 12)] == ["低", "中", "高", "過量", "危險"]


# ── 停班停課 ─────────────────────────────────────────────────────

def test_parse_dgpa_closures(wbot):
    wbot()
    info = module().parse_dgpa(DGPA_CLOSED)
    assert info["date"] == "民國115年9月3日" and info["updated"] == "2026/09/03 21:05:10" and not info["none"]
    counties = [c for c, _s in info["rows"]]
    assert counties == ["臺北市", "新北市", "桃園市", "花蓮縣"]                  # script 裡的假列不算
    rows = dict(info["rows"])
    assert rows["新北市"] == "明天停止上班、停止上課。\n烏來區 今天已停止上班、停止上課。"
    none = module().parse_dgpa(DGPA_NONE)
    assert none["none"] and none["rows"] == [] and none["updated"] == "2026/11/09 09:44:15"
    assert module().parse_dgpa("<html><body>網站維護中</body></html>") is None
    assert module().parse_dgpa("") is None


def test_typhoon_all_and_one_county(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx_typhoon")
    reply = bot.last(OWNER_ID)
    assert "🔴 臺北市：明天停止上班、停止上課。" in reply and "🟢 桃園市" in reply and "🟠 花蓮縣" in reply
    assert "dgpa.gov.tw" in reply and web.hosts() == ["www.dgpa.gov.tw"]
    bot.say(OWNER_ID, "/wx_typhoon 台北")
    reply = bot.last(OWNER_ID)
    assert "臺北市：明天停止上班" in reply and "新北市" not in reply
    bot.say(OWNER_ID, "/wx_typhoon 高雄")
    reply = bot.last(OWNER_ID)
    assert "高雄市：清單裡沒有列出" in reply and "臺北市、新北市" in reply
    bot.say(OWNER_ID, "/wx_typhoon 火星")
    assert "看不出「火星」是哪個縣市" in bot.last(OWNER_ID)


def test_typhoon_uses_setting_and_all_overrides(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx_set 縣市 新北")
    assert "縣市 → 新北市" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx_typhoon")
    assert "新北市：明天停止上班" in bot.last(OWNER_ID) and "臺北市" not in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx_typhoon 全部")
    assert "臺北市" in bot.last(OWNER_ID) and "新北市" in bot.last(OWNER_ID)


def test_typhoon_none_and_broken_page(wbot, web):
    bot = wbot()
    web.dgpa = DGPA_NONE.encode("utf-8")
    bot.say(OWNER_ID, "/wx_typhoon")
    assert "目前沒有任何縣市停班停課" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx_typhoon 台北")
    assert "臺北市：目前沒有停班停課訊息" in bot.last(OWNER_ID)
    web.dgpa = "<html><body>網站改版囉</body></html>".encode("utf-8")
    bot.say(OWNER_ID, "/wx_typhoon")
    assert "格式好像改了" in bot.last(OWNER_ID) and "https://www.dgpa.gov.tw/typh/daily/nds.html" in bot.last(OWNER_ID)
    web.fail["www.dgpa.gov.tw"] = Resp(503, content=b"")
    bot.say(OWNER_ID, "/wx_typhoon")
    assert "HTTP 503" in bot.last(OWNER_ID) and "nds.html" in bot.last(OWNER_ID)
    web.fail["www.dgpa.gov.tw"] = requests.ConnectionError("down")
    bot.say(OWNER_ID, "/wx_typhoon")
    assert "連不上人事行政總處" in bot.last(OWNER_ID)


def test_dgpa_big5_page_still_parses(wbot, web):
    bot = wbot()
    web.dgpa = DGPA_CLOSED.encode("big5", errors="replace")
    bot.say(OWNER_ID, "/wx_typhoon 台北")
    assert "臺北市：明天停止上班" in bot.last(OWNER_ID)


# ── 設定 ─────────────────────────────────────────────────────────

def test_settings_show_and_change(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx_settings")
    reply = bot.last(OWNER_ID)
    assert "地點：（還沒設）" in reply and "體感：普通" in reply and "活動：一般" in reply and "天數：3" in reply
    bot.say(OWNER_ID, "/wx_set 地點 台北")
    assert "地點 → 臺北市" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx_set 體感 怕冷")
    bot.say(OWNER_ID, "/wx_set 活動 work")
    bot.say(OWNER_ID, "/wx_set 天數 5")
    bot.say(OWNER_ID, "/wx_settings")
    reply = bot.last(OWNER_ID)
    assert "地點：臺北市" in reply and "體感：怕冷" in reply and "活動：上班" in reply and "天數：5" in reply
    web.calls.clear()
    bot.say(OWNER_ID, "/wx")                                       # 用預設地點、預設天數
    assert "臺北市 現在" in bot.last(OWNER_ID) and web.calls[-1]["params"]["forecast_days"] == 5
    bot.say(OWNER_ID, "/wx_set 地點 清除")
    bot.say(OWNER_ID, "/wx_set 天數 清除")
    bot.say(OWNER_ID, "/wx_settings")
    assert "地點：（還沒設）" in bot.last(OWNER_ID) and "天數：3" in bot.last(OWNER_ID)


def test_settings_reject_bad_values_and_keep_old(wbot, web):
    bot = wbot()
    bot.say(OWNER_ID, "/wx_set 地點 高雄")
    for text, said in (("/wx_set 顏色 紅", "看不懂「顏色」"), ("/wx_set 體感 很怕", "體感只認"),
                       ("/wx_set 活動 潛水", "活動只認"), ("/wx_set 縣市 火星", "看不出「火星」"),
                       ("/wx_set 天數 30", "天數要是"), ("/wx_set 天數 abc", "天數要是"),
                       ("/wx_set 地點 不存在的地方", "找不到")):
        bot.say(OWNER_ID, text)
        assert said in bot.last(OWNER_ID), (text, bot.last(OWNER_ID))
    bot.say(OWNER_ID, "/wx_set 地點")
    assert "用法" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx_settings")
    assert "地點：高雄市" in bot.last(OWNER_ID) and "體感：普通" in bot.last(OWNER_ID)


def test_set_place_network_error_does_not_save(wbot, web):
    bot = wbot()
    web.fail["geocoding-api.open-meteo.com"] = requests.ConnectionError("down")
    bot.say(OWNER_ID, "/wx_set 地點 Taipei")
    assert "連不上地名查詢" in bot.last(OWNER_ID)
    bot.say(OWNER_ID, "/wx_settings")
    assert "地點：（還沒設）" in bot.last(OWNER_ID)


def test_panel_route_shows_settings(wbot, web):
    bot = wbot()
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "weather")
    assert pack.web_routes["settings"](OWNER_ID) == {"place": "", "feel": "普通", "activity": "一般", "county": "", "days": 3}
    bot.say(OWNER_ID, "/wx_set 地點 台南")
    bot.say(OWNER_ID, "/wx_set 縣市 台南")
    got = pack.web_routes["settings"](OWNER_ID)
    assert got["place"] == "臺南市" and got["county"] == "臺南市"


# ── 權限、AI、雙人隔離（ADR-0006 第 6 節） ──────────────────────────

def test_owner_ai_can_read_and_set_member_cannot_see(wbot):
    bot = wbot("server")
    bot.make(A, R.MEMBER, "a")
    reg = bot.app.registry
    owner, member = bot.app.users.get(OWNER_ID), bot.app.users.get(A)
    for name in ("now", "wear", "air", "typhoon", "settings", "set"):
        node = reg.node(f"wx.{name}")
        assert policy.decide(bot.app, owner, node, via=AI).allowed, name
        assert not reg.visible(member, node) and not policy.decide(bot.app, member, node, via=AI).allowed, name
    bot.say(A, "/wx 台北", chat_id=A)
    assert "未知指令" in bot.last(chat_id=A)


def _run(bot, who, leaf, text):
    """直接叫 handler（伺服器版成員本來就用不到這個工具；這裡驗的是資料層每人一份）。"""
    node = bot.app.registry.node(f"wx.{leaf}")
    node.fn(bot.api, who, bot.app.users.get(who), text.split())
    return bot.last(chat_id=who)


def test_server_mode_settings_are_per_person_and_encrypted(wbot, web):
    """S1／S2：伺服器版每個人的設定在自己的 data/users/<id>/weather.json.enc（密文）；別人看不到；data/ 根目錄沒有共用檔。"""
    bot = wbot("server")
    bot.make(A, R.MEMBER, "a")
    bot.make(B, R.MEMBER, "b")
    picks = {OWNER_ID: ("台北", "臺北市"), A: ("高雄", "高雄市"), B: ("花蓮", "花蓮縣")}
    for who, (place, _full) in picks.items():
        assert "設好了" in _run(bot, who, "set", f"地點 {place}")
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "weather")
    data = bot.app.config.data_dir
    for who, (_place, full) in picks.items():
        seen = _run(bot, who, "settings", "")
        assert f"地點：{full}" in seen
        assert not [f for w, (_p, f) in picks.items() if w != who and f in seen]
        assert pack.web_routes["settings"](who)["place"] == full
        path = os.path.join(data, "users", str(who), "weather.json.enc")
        assert os.path.isfile(path) and pack.store.of(who).file() == path
        with open(path, "rb") as f:
            raw = f.read()
        assert not [f for _p, f in picks.values() if f.encode("utf-8") in raw]
    assert not os.path.exists(os.path.join(data, "weather.json.enc"))
    assert not os.path.exists(os.path.join(data, "packs", "weather"))


def test_personal_mode_one_encrypted_file(wbot, web):
    """S5：個人版設定在 data/weather.json.enc（密文），沒有 data/users/。"""
    bot = wbot("personal")
    bot.say(OWNER_ID, "/wx_set 地點 台北")
    pack = next(p for p in bot.app.extras["synox_packs"] if p.name == "weather")
    data = bot.app.config.data_dir
    path = os.path.join(data, "weather.json.enc")
    assert os.path.isfile(path) and pack.store.of(OWNER_ID).file() == path
    with open(path, "rb") as f:
        assert "臺北市".encode("utf-8") not in f.read()
    assert not os.path.exists(os.path.join(data, "users"))
