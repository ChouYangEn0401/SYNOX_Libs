# -*- coding: utf-8 -*-
"""路況（packs/road）：全部用假的 HTTP（記憶體裡的高公局開放資料、TDX、Google），不連網、不開埠。
真的 SYNOX（host_bot／synox_bot）：保管箱加密、pack.secret、權限、伺服器版每人一份（ADR-0006 第 6 節）。"""

import gzip
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from urllib.parse import unquote

import pytest
import requests

from isdtelekit.testing import OWNER_ID

from synox.packs import scan
from synox.packs.loader import discover
from synox.packs.manifest import read

from conftest import OTHER_ID, PACKS

NOW = datetime(2026, 11, 9, 8, 0)
GOOGLE_KEY = "fake-google-key-0123456789abcdef0000"
TDX_ID = "fake-client-id-0000"
TDX_SECRET = "fake-client-secret-0123456789abcdef"
TDX_TOKEN = "fake-access-token-abcdefabcdefabcdef"
A = 77

NS = 'xmlns="http://traffic.transportdata.tw/standard/traffic/schema/"'


def section(sid, road, d, start, end, length, skm, ekm):
    return (f"<Section><SectionID>{sid}</SectionID><SectionName>{road}({start}到{end})</SectionName>"
            f"<RoadName>{road}</RoadName><RoadDirection>{d}</RoadDirection>"
            f"<RoadSection><Start>{start}</Start><End>{end}</End></RoadSection><SectionLength>{length}</SectionLength>"
            f"<SectionMile><StartKM>{skm}</StartKM><EndKM>{ekm}</EndKM></SectionMile><SpeedLimit>100</SpeedLimit></Section>")


SECTIONS = ("<?xml version='1.0' ?>"
            f"<SectionList {NS}><UpdateTime>2026-11-09T07:00:00</UpdateTime><Sections>"
            + section("S1", "國道1號", "S", "五股", "泰山轉接", 7.0, "26K+000", "33K+000")
            + section("S2", "國道1號", "S", "泰山轉接", "林口", 8.5, "33K+000", "41K+500")
            + section("S3", "國道1號", "S", "林口", "桃園", 7.5, "41K+500", "49K+000")
            + section("N1", "國道1號", "N", "桃園", "林口", 7.5, "49K+000", "41K+500")
            + section("N2", "國道1號", "N", "林口", "泰山轉接", 8.5, "41K+500", "33K+000")
            + section("N3", "國道1號", "N", "泰山轉接", "五股", 7.0, "33K+000", "26K+000")
            + section("T1", "國道3號", "S", "大溪", "龍潭", 4.2, "63K+550", "67K+750")
            + "</Sections></SectionList>")


def live(sid, tt, speed, level):
    return (f"<LiveTraffic><SectionID>{sid}</SectionID><TravelTime>{tt}</TravelTime><TravelSpeed>{speed}</TravelSpeed>"
            f"<CongestionLevelID>A</CongestionLevelID><CongestionLevel>{level}</CongestionLevel></LiveTraffic>")


LIVE = ("<?xml version='1.0' encoding='utf-8'?>"
        f"<LiveTrafficList {NS}><UpdateTime>2026-11-09T07:59:00+08:00</UpdateTime><LiveTraffics>"
        + live("S1", 265, 95, 1) + live("S2", 322, 95, 1) + live("S3", 284, 95, 1)
        + live("N1", 300, 90, 1) + live("N2", 874, 35, 4) + live("N3", 360, 70, 2)
        + live("T1", -99, -99, -99)
        + "</LiveTraffics></LiveTrafficList>")


def event(title, desc, road, d, skm, ekm, impact="無影響", at="2026-11-09T07:30:00+08:00"):
    return (f"<LiveEvent><EventTitle>{title}</EventTitle><Description>{desc}</Description>"
            f"<EffectiveTime>{at}</EffectiveTime><Location><FreeExpressHighway><Road>{road}</Road>"
            f"<Direction>{d}</Direction><StartKM>{skm}</StartKM><EndKM>{ekm}</EndKM></FreeExpressHighway></Location>"
            f"<Impact><Description>{impact}</Description></Impact></LiveEvent>")


EVENTS = ("<LiveEventList><UpdateTime>2026-11-09T07:59:00+08:00</UpdateTime><LiveEvents>"
          + event("交通事故", "國道一號 北向 38K+000 交通事故-小客車追撞", "國道一號", "北向", "38K+000", "38K+000",
                  impact="外側車道阻塞")
          + event("施工事件", "國道三號 南向 64K+000 施工事件-道路施工", "國道三號", "南向", "64K+000", "65K+000")
          + event("壅塞事件", "國道一號 北向 30K+000 壅塞事件-出口壅塞", "國道一號", "北向", "30K+000", "30K+000")
          + "</LiveEvents></LiveEventList>")


class Resp:
    def __init__(self, status=200, body=b"", data=None):
        self.status_code = status
        self.content = json.dumps(data).encode("utf-8") if data is not None else body
        self.text = self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class FakeNet:
    """requests.Session 的替身：高公局開放資料、TDX、Google 都在記憶體裡。"""

    def __init__(self):
        self.calls = []
        self.fail = {}                         # 網址片段 → Resp 或 Exception
        self.files = {"Section.xml": SECTIONS.encode("utf-8"),
                      "LiveTraffic.xml": gzip.compress(LIVE.encode("utf-8")),     # 壓縮過的也要讀得懂
                      "LiveEvents.xml": EVENTS.encode("utf-8")}
        self.google = {"routes": [{"duration": "1500s", "staticDuration": "1080s", "distanceMeters": 42100}]}

    def request(self, method, url, params=None, json=None, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": dict(params or {}), "json": json,
                           "data": dict(data or {}), "headers": dict(headers or {})})
        assert timeout, "每個請求都要有 timeout"
        for part, fail in self.fail.items():
            if part in url:
                if isinstance(fail, Exception):
                    raise fail
                return fail
        if url.startswith("https://tisvcloud.freeway.gov.tw/history/motc20/"):
            name = url.rsplit("/", 1)[-1]
            return Resp(200, self.files[name]) if name in self.files else Resp(404, b"no")
        if url.startswith("https://routes.googleapis.com/"):
            if (headers or {}).get("X-Goog-Api-Key") != GOOGLE_KEY:
                return Resp(403, data={"error": {"message": f"API key not valid: {(headers or {}).get('X-Goog-Api-Key')}"}})
            assert headers["X-Goog-FieldMask"] and json["travelMode"] == "DRIVE"
            return Resp(200, data=self.google)
        if url.startswith("https://tdx.transportdata.tw/auth/"):
            if data.get("client_id") != TDX_ID or data.get("client_secret") != TDX_SECRET:
                return Resp(401, data={"error": "invalid_client"})
            return Resp(200, data={"access_token": TDX_TOKEN, "expires_in": 86400})
        if url.startswith("https://tdx.transportdata.tw/api/"):
            if (headers or {}).get("Authorization") != f"Bearer {TDX_TOKEN}":
                return Resp(401, data={"message": "Valid API Key Required"})
            if "/GeoCode/" in url:
                place = unquote(url.rsplit("/", 1)[-1])
                points = {"台北車站": "POINT (121.517 25.047)", "桃園機場": "POINT (121.233 25.077)"}
                return Resp(200, data=[{"Address": place, "Geometry": points[place]}] if place in points else [])
            if url.endswith("/maas/routing"):
                return Resp(200, data={"result": "success", "data": {"routes": [
                    {"travel_time": 2400, "sections": [{"travel_summary": {"duration": 2400, "length": 45000}}]}]}})
        return Resp(404, data={"error": "no such endpoint"})

    def urls(self, part):
        return [c for c in self.calls if part in c["url"]]


def _pack(bot):
    return next(p for p in bot.app.extras["synox_packs"] if p.name == "road")


def set_keys(bot, user=None, **values):
    pack = _pack(bot)
    pack.secrets.update("road", pack.secret_fields, values, user)


@pytest.fixture
def road(host_bot):
    """(bot, net, module)：真的 SYNOX 裝好 packs/road、HTTP 換成 FakeNet、現在固定。"""
    def factory(script=None, **kw):
        bot = host_bot(script, **kw)
        mod = sys.modules["synox_pack_road"]
        net = FakeNet()
        mod.new_session = lambda: net
        mod.now = lambda: NOW
        return bot, net, mod
    return factory


# ── 安全關卡 ─────────────────────────────────────────────────────────────

def test_manifest_and_static_scan_pass():
    folder = next(f for f in discover([PACKS]) if os.path.basename(f) == "road")
    m = read(folder, strict=True)
    assert m.kind == "python" and m.capabilities == ["network"] and m.storage == "vault" and m.access == "owner"
    report = scan.scan(m)
    assert report.problems(m) == []
    assert report.required_capabilities == ["network"]


def test_auth_fields_are_optional_objects():
    from synox import connect
    folder = next(f for f in discover([PACKS]) if os.path.basename(f) == "road")
    fields = connect.auth_fields(read(folder, strict=True))
    assert [f["name"] for f in fields] == ["tdx_client_id", "tdx_client_secret", "google_api_key"]
    assert [f["secret"] for f in fields] == [False, True, True] and not any(f["required"] for f in fields)


# ── 國道路況（不用金鑰）────────────────────────────────────────────────

def test_no_default_road_says_how_without_network(road, owner):
    bot, net, _ = road()
    bot.say(owner, "/road")
    assert "要查哪一條國道" in bot.last(owner) and "/road_set road" in bot.last(owner)
    assert net.calls == []


def test_section_speed_time_and_events(road, owner):
    bot, net, _ = road()
    bot.say(owner, "/road 國1 北 林口 五股")
    text = bot.last(owner)
    assert text.splitlines()[0] == "🚗 國道1號 北向 林口→五股（2 段，15.5 公里）"
    assert "約 20 分鐘" in text and "🔴 有壅塞" in text                       # 874 + 360 秒
    assert "🔴 林口→泰山轉接 35 km/h・14 分鐘" in text
    assert "🟡 泰山轉接→五股 70 km/h・6 分鐘" in text
    assert "💥 國道一號 北向 38K+000 交通事故-小客車追撞（外側車道阻塞）" in text      # 落在這一段的事故
    assert "出口壅塞" not in text and "國道三號" not in text                   # 壅塞事件、別條路不列
    assert "資料時間 07:59" in text
    assert "桃園" not in text.split("\n", 2)[2]                               # 只列林口到五股


@pytest.mark.parametrize("query", ["國道一號 北上 林口 五股", "1 北向 林口交流道 五股", "中山高 n 林口 五股"])
def test_road_and_direction_spellings(road, owner, query):
    bot, _, _ = road()
    bot.say(owner, f"/road {query}")
    assert "國道1號 北向 林口→五股" in bot.last(owner)


def test_direction_inferred_and_both_directions(road, owner):
    bot, _, _ = road()
    bot.say(owner, "/road 國1 五股 林口")                                       # 五股→林口＝南向
    assert "國道1號 南向 五股→林口" in bot.last(owner) and "🟢 順暢" in bot.last(owner)
    bot.say(owner, "/road 國1")
    assert "國道1號 北向 桃園→五股" in bot.last(owner) and "國道1號 南向 五股→桃園" in bot.last(owner)


def test_missing_data_is_estimated_and_marked(road, owner):
    bot, _, _ = road()
    bot.say(owner, "/road 國3 南")
    assert "⚪ 沒資料" in bot.last(owner) and "1 段沒資料" in bot.last(owner) and "⚪ 大溪→龍潭 —" in bot.last(owner)


def test_unknown_place_and_road_list_choices(road, owner):
    bot, _, _ = road()
    bot.say(owner, "/road 國1 北 台中 五股")
    assert "找不到" in bot.last(owner) and "林口" in bot.last(owner) and "泰山轉接" in bot.last(owner)
    bot.say(owner, "/road 國9")
    assert "找不到「國道9號」" in bot.last(owner) and "國道1號" in bot.last(owner)
    bot.say(owner, "/road 國3 北")
    assert "沒有北向" in bot.last(owner)


def test_sections_are_cached_for_a_day(road, owner):
    bot, net, mod = road()
    bot.say(owner, "/road 國1 北")
    bot.say(owner, "/road 國1 南")
    assert len(net.urls("Section.xml")) == 1 and len(net.urls("LiveTraffic.xml")) == 2
    mod.now = lambda: NOW + timedelta(hours=25)
    bot.say(owner, "/road 國1 北")
    assert len(net.urls("Section.xml")) == 2


def test_lines_limit_shows_slowest(road, owner):
    bot, _, _ = road()
    bot.say(owner, "/road_set lines 3")
    bot.say(owner, "/road 國1 北")
    assert "只列最慢的 3 段" not in bot.last(owner)                              # 剛好 3 段：全部列
    bot.say(owner, "/road_set lines 2")
    assert "行數要是 3 到 40" in bot.last(owner)


def test_unit_miles(road, owner):
    bot, _, _ = road()
    bot.say(owner, "/road_set 單位 mi")
    assert "英里" in bot.last(owner)
    bot.say(owner, "/road 國1 北 林口 五股")
    assert "9.6 英里" in bot.last(owner) and "22 mph" in bot.last(owner)


def test_events_off(road, owner):
    bot, net, _ = road()
    bot.say(owner, "/road_set events 關")
    bot.say(owner, "/road 國1 北 林口 五股")
    assert "交通事故" not in bot.last(owner) and not net.urls("LiveEvents.xml")


def test_freeway_errors_become_one_sentence(road, owner):
    bot, net, _ = road()
    net.fail["LiveTraffic.xml"] = requests.Timeout()
    bot.say(owner, "/road 國1 北")
    assert "逾時" in bot.last(owner)
    net.fail["LiveTraffic.xml"] = Resp(503, b"busy")
    bot.say(owner, "/road 國1 北")
    assert "出錯了（503）" in bot.last(owner)
    net.fail["LiveTraffic.xml"] = Resp(200, b"<not xml")
    bot.say(owner, "/road 國1 北")
    assert "看不懂" in bot.last(owner)
    net.fail["LiveTraffic.xml"] = requests.ConnectionError()
    bot.say(owner, "/road 國1 北")
    assert "連不上" in bot.last(owner)


def test_events_failure_does_not_break_traffic(road, owner):
    bot, net, _ = road()
    net.fail["LiveEvents.xml"] = Resp(500, b"x")
    bot.say(owner, "/road 國1 北 林口 五股")
    assert "約 20 分鐘" in bot.last(owner)


# ── 讀資料：XML／JSON／gzip ─────────────────────────────────────────────

def test_parse_doc_handles_json_gzip_and_rejects_bad(road):
    _, _, mod = road()
    doc = {"Sections": [{"SectionID": "J1", "RoadName": "國道5號", "RoadDirection": "S",
                         "RoadSection": {"Start": "南港系統", "End": "石碇"}, "SectionLength": 6.2,
                         "SectionMile": {"StartKM": "0K+000", "EndKM": "6K+200"}}]}
    rows = mod.section_rows(mod.parse_doc(gzip.compress(json.dumps(doc).encode("utf-8"))))
    assert rows == [["J1", "5", "S", "南港系統", "石碇", 6.2, 0.0, 6.2]]
    assert mod.section_rows(mod.parse_doc(SECTIONS.encode("utf-8")))[0][:5] == ["S1", "1", "S", "五股", "泰山轉接"]
    with pytest.raises(mod.RoadError):
        mod.parse_doc(b'<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
    with pytest.raises(mod.RoadError):
        mod.parse_doc(b"\x1f\x8bnot really gzip")
    mod_max = mod.MAX_BYTES
    try:
        mod.MAX_BYTES = 1000
        with pytest.raises(mod.RoadError, match="太大"):
            mod.parse_doc(gzip.compress(b" " * 5000 + b"{}"))
    finally:
        mod.MAX_BYTES = mod_max


def test_road_keys(road):
    _, _, mod = road()
    assert [mod.road_key(x) for x in ("國1", "國道1號", "國道一號", "1", "國3甲", "國道3甲", "五楊", "臺76線",
                                      "國道十號", "南港聯絡道")] == \
        ["1", "1", "1", "1", "3甲", "3甲", "1高架", "台76", "10", "南港聯絡道"]
    assert mod.road_key("國道1號汐止五股高架道路") == "1高架"


# ── 事件 ────────────────────────────────────────────────────────────────

def test_events_filters(road, owner):
    bot, _, _ = road()
    bot.say(owner, "/road_events")
    text = bot.last(owner)
    assert "3 則" in text and text.index("交通事故") < text.index("施工")          # 事故排前面
    bot.say(owner, "/road_events 國1")
    assert "小客車追撞" in bot.last(owner) and "道路施工" not in bot.last(owner)
    bot.say(owner, "/road_events 國3 南 施工")
    assert "道路施工" in bot.last(owner) and "1 則" in bot.last(owner)
    bot.say(owner, "/road_events 國5")
    assert "沒有事故" in bot.last(owner)


# ── 起點到終點（要金鑰）─────────────────────────────────────────────────

def test_eta_without_keys_explains_how(road, owner):
    bot, net, _ = road()
    bot.say(owner, "/road_eta 台北車站 到 桃園機場")
    text = bot.last(owner)
    assert "TDX" in text and "Google" in text and "連接" in text and "python -m synox connect road" in text
    assert net.calls == []
    bot.say(owner, "/road_set provider google")
    bot.say(owner, "/road_eta 台北車站 到 桃園機場")
    assert "還沒設定 Google 地圖的金鑰" in bot.last(owner) and "Routes API" in bot.last(owner)
    bot.say(owner, "/road_eta 台北車站")
    assert "用法" in bot.last(owner)


def test_eta_google(road, owner, caplog):
    bot, net, _ = road()
    set_keys(bot, google_api_key=GOOGLE_KEY)
    with caplog.at_level(logging.DEBUG):
        bot.say(owner, "/road_eta 台北車站 → 桃園機場")
    text = bot.last(owner)
    assert text == "🚗 台北車站 → 桃園機場：約 25 分鐘（42.1 公里；不塞車約 18 分鐘，現在多 7 分鐘）・Google 地圖"
    call = net.urls("routes.googleapis.com")[0]
    assert call["headers"]["X-Goog-Api-Key"] == GOOGLE_KEY
    assert call["json"]["origin"] == {"address": "台北車站"} and call["json"]["destination"] == {"address": "桃園機場"}
    assert GOOGLE_KEY not in "\n".join(r.getMessage() for r in caplog.records)
    raw = json.dumps(_pack(bot).store.of(owner).load({}), ensure_ascii=False)
    assert GOOGLE_KEY not in raw                                                 # 金鑰不進工具自己的資料


def test_eta_google_error_masks_key(road, owner):
    bot, net, _ = road()
    set_keys(bot, google_api_key="fake-WRONG-google-key-0123456789ab")
    net.fail["routes.googleapis.com"] = Resp(400, data={"error": {"message": "API key not valid: fake-WRONG-google-key-0123456789ab"}})
    bot.say(owner, "/road_eta 甲地 到 乙地")
    assert "回錯誤（400）" in bot.last(owner) and "WRONG" not in bot.last(owner) and "***" in bot.last(owner)
    net.fail.clear()
    bot.say(owner, "/road_eta 甲地 到 乙地")                                    # 假 Google 回 403
    assert "不接受金鑰（403）" in bot.last(owner) and "WRONG" not in bot.last(owner)
    net.google = {"routes": []}
    set_keys(bot, google_api_key=GOOGLE_KEY)
    bot.say(owner, "/road_eta 甲地 到 乙地")
    assert "找不到" in bot.last(owner)


def test_eta_tdx(road, owner):
    bot, net, _ = road()
    set_keys(bot, tdx_client_id=TDX_ID, tdx_client_secret=TDX_SECRET)
    bot.say(owner, "/road_eta 台北車站 到 桃園機場")
    assert bot.last(owner) == "🚗 台北車站 → 桃園機場：約 40 分鐘（45.0 公里）・TDX"
    token = net.urls("openid-connect/token")[0]
    assert token["method"] == "POST" and token["data"]["grant_type"] == "client_credentials"
    routing = net.urls("/maas/routing")[0]
    assert routing["params"]["origin"] == "25.047,121.517" and routing["params"]["destination"] == "25.077,121.233"
    assert routing["headers"]["Authorization"] == f"Bearer {TDX_TOKEN}"
    assert TDX_SECRET not in bot.last(owner) and TDX_TOKEN not in bot.last(owner)
    bot.say(owner, "/road_eta 25.0,121.5 到 台北車站")                           # 座標不用查地址
    assert net.urls("/maas/routing")[-1]["params"]["origin"] == "25.0,121.5"
    bot.say(owner, "/road_eta 不存在的地方 到 台北車站")
    assert "TDX 找不到「不存在的地方」" in bot.last(owner)


def test_eta_tdx_bad_secret(road, owner):
    bot, net, _ = road()
    set_keys(bot, tdx_client_id=TDX_ID, tdx_client_secret="wrong-secret-wrong-secret")
    bot.say(owner, "/road_eta 台北車站 到 桃園機場")
    assert "TDX不接受金鑰（401）" in bot.last(owner) and "wrong-secret" not in bot.last(owner)


def test_provider_switch(road, owner):
    bot, net, _ = road()
    set_keys(bot, tdx_client_id=TDX_ID, tdx_client_secret=TDX_SECRET, google_api_key=GOOGLE_KEY)
    bot.say(owner, "/road_eta 台北車站 到 桃園機場")
    assert bot.last(owner).endswith("TDX")                                       # auto：TDX 優先
    bot.say(owner, "/road_set 供應商 google")
    assert "Google 地圖" in bot.last(owner)
    bot.say(owner, "/road_eta 台北車站 到 桃園機場")
    assert bot.last(owner).endswith("Google 地圖")
    bot.say(owner, "/road_set provider 亂寫")
    assert "只能是 auto、tdx、google" in bot.last(owner)


# ── 常用路線 ────────────────────────────────────────────────────────────

def test_saved_routes(road, owner):
    bot, net, _ = road()
    bot.say(owner, "/road_routes")
    assert "還沒有常用路線" in bot.last(owner)
    bot.say(owner, "/road_save 上班 國1 北 林口 五股")
    assert "存好了：上班" in bot.last(owner)
    bot.say(owner, "/road_save 回家 台北車站 到 桃園機場")
    assert "起點到終點" in bot.last(owner)
    bot.say(owner, "/road_save 亂寫 這不是 路線 也不是 國道")
    assert "❌" in bot.last(owner)
    net.calls.clear()
    bot.say(owner, "/road_routes")
    text = bot.last(owner)
    assert text.splitlines()[0] == "🚗 常用路線（2 條）"
    assert "• 上班（國1 北 林口 五股）：北向 約 20 分鐘・" in text and "🔴 有壅塞" in text
    assert "• 回家（台北車站 到 桃園機場）：❌ 查起點到終點的行車時間要一組地圖金鑰" in text
    set_keys(bot, google_api_key=GOOGLE_KEY)
    bot.say(owner, "/road_routes")
    assert "• 回家：台北車站 → 桃園機場：約 25 分鐘" in bot.last(owner)
    bot.say(owner, "/road_settings")
    assert "上班：國1 北 林口 五股" in bot.last(owner)
    bot.say(owner, "/road_rm 上班")
    assert "刪掉了：上班" in bot.last(owner)
    bot.say(owner, "/road_rm 上班")
    assert "沒有叫「上班」" in bot.last(owner)


def test_route_limit(road, owner):
    bot, _, mod = road()
    for i in range(mod.MAX_ROUTES):
        bot.say(owner, f"/road_save r{i} 國1 北")
    bot.say(owner, "/road_save 多的 國1 南")
    assert f"最多 {mod.MAX_ROUTES} 條" in bot.last(owner)


# ── 設定 ────────────────────────────────────────────────────────────────

def test_settings_show_and_change(road, owner):
    bot, net, _ = road()
    bot.say(owner, "/road_settings")
    text = bot.last(owner)
    assert "⚙️ 路況的設定" in text and "provider" in text and "TDX：— 沒設" in text and "/road_set" in text
    bot.say(owner, "/road_set road 國道一號")
    assert "預設國道）：國道1號" in bot.last(owner)
    bot.say(owner, "/road_set 方向 北上")
    bot.say(owner, "/road")
    assert "國道1號 北向 桃園→五股" in bot.last(owner) and "南向" not in bot.last(owner)
    bot.say(owner, "/road 林口 五股")                                             # 用預設國道
    assert "國道1號 北向 林口→五股" in bot.last(owner)
    bot.say(owner, "/road_set dir 預設")
    assert "全部" in bot.last(owner)
    bot.say(owner, "/road_set color red")
    assert "沒有這個設定" in bot.last(owner)
    set_keys(bot, google_api_key=GOOGLE_KEY)
    bot.say(owner, "/road_settings")
    assert "Google 地圖：✅ 已設定" in bot.last(owner) and GOOGLE_KEY not in bot.last(owner)


def test_settings_panel_route(road, owner):
    bot, _, _ = road()
    bot.say(owner, "/road_save 上班 國1 北 林口 五股")
    pack = _pack(bot)
    data = pack.web_routes["settings"](bot.app.users.get(owner))
    assert data["routes"] == [{"name": "上班", "spec": "國1 北 林口 五股"}]
    assert data["keys"] == {"tdx": False, "google": False} and any("provider" in s for s in data["settings"])


def test_ai_tools(road, owner):
    bot, _, _ = road(["好"])
    bot.talk(owner, "國道塞嗎")
    _messages, tools = bot.app.extras["synox_agent"].adapter.calls[0]
    names = {t.name for t in tools}
    assert {"road__now", "road__events", "road__eta", "road__routes", "road__settings"} <= names


def test_stranger_cannot_see_it(road, owner):
    bot, net, _ = road()
    bot.say(OTHER_ID, "/road 國1")
    assert "國道" not in bot.last(OTHER_ID) and net.calls == []


# ── 伺服器版：每人一份（ADR-0006 第 6 節）───────────────────────────────

def test_server_mode_isolation_and_ciphertext(synox_bot):
    bot = synox_bot("road", mode="server")
    mod = sys.modules["synox_pack_road"]
    net = FakeNet()
    mod.new_session = lambda: net
    mod.now = lambda: NOW
    pack = _pack(bot)
    bot.say(OWNER_ID, "/road_save 擁有者祕密路線 國1 北 林口 五股", chat_id=OWNER_ID)
    bot.say(OWNER_ID, "/road_set road 國3", chat_id=OWNER_ID)
    pack.secrets.update("road", pack.secret_fields, {"google_api_key": GOOGLE_KEY}, OWNER_ID)
    # 成員：工具只給擁有者，打了跟不存在一樣，也不連網
    calls = len(net.calls)
    bot.say(A, "/road_settings", chat_id=A)
    assert "擁有者祕密路線" not in (bot.last(chat_id=A) or "") and len(net.calls) == calls
    # 資料與金鑰每人一份
    assert pack.store.of(A).load(None) is None
    assert pack.secret("google_api_key", A) is None and pack.secret("google_api_key", OWNER_ID) == GOOGLE_KEY
    assert pack.web_routes["settings"](A)["routes"] == []
    assert pack.web_routes["settings"](OWNER_ID)["routes"][0]["name"] == "擁有者祕密路線"
    # 落地是密文，在擁有者自己的資料夾，data/ 根目錄沒有共用檔
    path = pack.store.of(OWNER_ID).file()
    assert os.sep + "users" + os.sep + str(OWNER_ID) + os.sep in path
    raw = open(path, "rb").read()
    assert "擁有者祕密路線".encode("utf-8") not in raw and b"routes" not in raw
    data_dir = str(bot.app.config.data_dir)
    assert not any(n.startswith("road") for n in os.listdir(data_dir))
