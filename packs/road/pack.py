"""🚗 路況（台灣）：/road [國道] [方向] [起點] [終點]、/road_events、/road_eta <起點> 到 <終點>、
/road_routes、/road_save <名字> <內容>、/road_rm <名字>；設定：/road_settings、/road_set <鍵> <值>。

資料從哪裡來：
  1. 國道路況（不用金鑰）：交通部高速公路局的開放資料 tisvcloud.freeway.gov.tw/history/motc20/
     （Section.xml 路段基本資料、LiveTraffic.xml 每分鐘的速度／旅行時間／壅塞程度、LiveEvents.xml 1968 的即時事件）。
     XML、JSON、gzip 壓縮過的都讀得懂（parse_doc）。路段基本資料一天才變一次：存一份精簡版在這個人自己的
     pack.store（key="sections"），一天內不重抓。
  2. 起點到終點的行車時間（要金鑰，設定裡選供應商）：
     - TDX（交通部運輸資料流通服務）：client_id／client_secret 換 token → 地址轉座標 → 路徑規劃（alpha：TDX 的路徑規劃
       以大眾運輸為主，開車的參數寫在 TDX_DRIVE，對不上時只要改那一處）；
     - Google Maps Routes API（API key）。
     金鑰用 pack.json 的 auth.fields（SYNOX 收、存在保管箱），程式只用 pack.secret(名字, user) 讀，
     不存進 pack.store、不放進回覆、不寫 log；錯誤訊息裡萬一出現金鑰也先遮掉（_mask）。

這個人的資料（pack.store.of(user)，保管箱、密文；伺服器版每人一份）：
  預設那一份：{"settings": {...}, "routes": {"名字": {"kind": "freeway"|"eta", "spec": "原話"}}}
  key="sections"：{"at": 抓的時間, "rows": [[id, 國道, 方向, 起點, 終點, 長度, 起點里程, 終點里程], ...]}

測試換掉 new_session（假的 requests.Session）與 now()，不連網。
"""

import json
import re
import time
import zlib
from datetime import datetime
from urllib.parse import quote
from xml.etree import ElementTree

import requests

from isdtelekit.core.registry import WRITE, Field

from synox.packs import kit

FREEWAY = "https://tisvcloud.freeway.gov.tw/history/motc20/"
SECTION_URL = FREEWAY + "Section.xml"
LIVE_URL = FREEWAY + "LiveTraffic.xml"
EVENTS_URL = FREEWAY + "LiveEvents.xml"
TDX_TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
TDX_GEOCODE_URL = "https://tdx.transportdata.tw/api/advanced/V3/Map/GeoCode/Coordinate/Address/"
TDX_ROUTING_URL = "https://tdx.transportdata.tw/api/maas/routing"
# TDX 路徑規劃「開車」用的參數（alpha：照 TDX MaaS 路徑規劃的說明寫，沒拿真的金鑰驗過；不對就改這裡）
TDX_DRIVE = {"gc": "1.0", "top": "1", "transit": "", "first_mile_mode": "2", "first_mile_time": "60",
             "last_mile_mode": "2", "last_mile_time": "60"}
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GOOGLE_FIELDS = "routes.duration,routes.staticDuration,routes.distanceMeters"

TIMEOUT = 20                     # 秒
MAX_BYTES = 16 * 1024 * 1024     # 解壓縮後最多讀多少（防壓縮炸彈）
SECTIONS_TTL = 24 * 3600         # 路段基本資料多久重抓一次
MAX_ROUTES = 20
NAME_MAX = 20
SPEC_MAX = 120

DEFAULTS = {"provider": "auto", "road": "", "dir": "", "unit": "km", "lines": 12, "events": True}
# 設定的鍵：英文或中文都收
KEY_ALIASES = {"provider": "provider", "供應商": "provider", "地圖": "provider",
               "road": "road", "國道": "road", "預設國道": "road",
               "dir": "dir", "direction": "dir", "方向": "dir", "預設方向": "dir",
               "unit": "unit", "單位": "unit",
               "lines": "lines", "行數": "lines",
               "events": "events", "事件": "events"}
PROVIDERS = {"auto": "自動（有 TDX 金鑰用 TDX，否則用 Google）", "tdx": "TDX（交通部）", "google": "Google 地圖"}
DIRS = {"N": "北向", "S": "南向", "E": "東向", "W": "西向"}
DIR_WORDS = {"北": "N", "北上": "N", "北向": "N", "北行": "N", "n": "N",
             "南": "S", "南下": "S", "南向": "S", "南行": "S", "s": "S",
             "東": "E", "東向": "E", "東行": "E", "e": "E",
             "西": "W", "西向": "W", "西行": "W", "w": "W"}
ALL_WORDS = ("全部", "雙向", "都要", "both", "all", "無", "不設", "清掉", "none")
ON_WORDS = ("開", "on", "要", "是", "true", "1", "顯示")
OFF_WORDS = ("關", "off", "不要", "否", "false", "0", "不顯示")
ROAD_ALIASES = {"中山高": "1", "一高": "1", "二高": "3", "北二高": "3", "福高": "3", "福爾摩沙": "3",
                "五楊": "1高架", "五楊高架": "1高架", "汐五高架": "1高架", "汐止五股高架": "1高架",
                "雪隧": "5", "蔣渭水": "5", "北宜": "5"}
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
LEVELS = {1: ("🟢", "順暢"), 2: ("🟡", "車多"), 3: ("🟡", "車多"), 4: ("🔴", "壅塞"), 5: ("🔴", "壅塞"),
          -1: ("⛔", "封閉")}
EVENT_ORDER = ("交通事故", "特殊管制事件", "管制事件", "天候事件", "施工事件", "其他異常告警", "壅塞事件")
EVENT_ICON = {"交通事故": "💥", "特殊管制事件": "🚧", "管制事件": "🚧", "天候事件": "🌧", "施工事件": "🦺",
              "壅塞事件": "🐢"}
SPLIT_ETA = re.compile(r"\s*(?:→|->|=>|＞|>|～|~|到|至)\s*")
KM_RE = re.compile(r"(\d+)\s*[Kk]\s*\+?\s*(\d+)?")
POINT_RE = re.compile(r"POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)")
LATLON_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def now() -> datetime:
    """現在時間（測試換掉這一個）。"""
    return datetime.now()


def new_session():
    """HTTP 傳輸（每次查詢一個新的，不共用）。測試把這個換成假的（不連網）。"""
    return requests.Session()


class RoadError(Exception):
    """要回給使用者的一句話。"""


# ── 讀資料：壓縮、XML、JSON ───────────────────────────────────────────────

def decode_body(content: bytes) -> bytes:
    """gzip（開頭 1f 8b）先解開；其他原樣。解開後太大就不讀。"""
    if content[:2] == b"\x1f\x8b":
        d = zlib.decompressobj(47)
        try:
            out = d.decompress(content, MAX_BYTES)
        except zlib.error:
            raise RoadError("資料壞掉了（解壓縮失敗），等一下再試。")
        if d.unconsumed_tail:
            raise RoadError("資料太大，不讀。")
        return out
    if len(content) > MAX_BYTES:
        raise RoadError("資料太大，不讀。")
    return content


def _xml_obj(el):
    """XML 元素 → dict／str（重複的子元素變成 list；拿掉命名空間）。"""
    kids = list(el)
    if not kids:
        return (el.text or "").strip()
    out = {}
    for kid in kids:
        name = kid.tag.split("}")[-1]
        value = _xml_obj(kid)
        if name in out:
            if not isinstance(out[name], list):
                out[name] = [out[name]]
            out[name].append(value)
        else:
            out[name] = value
    return out


def parse_doc(content: bytes):
    """XML 或 JSON（可能壓縮過）→ dict／list。"""
    raw = decode_body(content or b"")
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text:
        raise RoadError("對方回了空的資料，等一下再試。")
    if text[0] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            raise RoadError("看不懂對方回的資料（JSON 壞了）。")
    if "<!DOCTYPE" in text[:500] or "<!ENTITY" in text:
        raise RoadError("看不懂對方回的資料。")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        raise RoadError("看不懂對方回的資料（XML 壞了）。")
    return {root.tag.split("}")[-1]: _xml_obj(root)}


def _unwrap(obj):
    """XML 版多包一層根元素（{"SectionList": {...}}）：拿掉。"""
    if isinstance(obj, dict) and len(obj) == 1:
        only = next(iter(obj.values()))
        if isinstance(only, dict):
            return only
    return obj


def rows_of(obj, plural: str, singular: str) -> list:
    """XML：{"Sections": {"Section": [...]}}；JSON：{"Sections": [...]}；都轉成 list。"""
    obj = _unwrap(obj)
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    box = obj.get(plural) if isinstance(obj, dict) else None
    if isinstance(box, dict):
        box = box.get(singular, [])
    if isinstance(box, dict):
        box = [box]
    return [r for r in (box or []) if isinstance(r, dict)]


def _num(value, default=None):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def km_of(text) -> float:
    """'41K+500' → 41.5；看不懂回 -1。"""
    m = KM_RE.search(str(text or ""))
    if not m:
        return -1.0
    return int(m.group(1)) + int(m.group(2) or 0) / 1000.0


def cn_number(text: str) -> str:
    """'一'→'1'、'十'→'10'、'十四'→'14'、'二十'→'20'；數字原樣。"""
    if text.isdigit():
        return str(int(text))
    if text == "十":
        return "10"
    if "十" in text:
        a, _, b = text.partition("十")
        return str((CN_DIGITS.get(a, 1) if a else 1) * 10 + (CN_DIGITS.get(b, 0) if b else 0))
    return str(CN_DIGITS.get(text, text))


def road_key(text: str) -> str:
    """各種寫法 → 同一個鍵：國1／國道1號／國道一號／1 → '1'；國3甲 → '3甲'；五楊 → '1高架'；臺76線 → '台76'。"""
    t = re.sub(r"\s+", "", str(text or "")).replace("臺", "台")
    if not t:
        return ""
    if t in ROAD_ALIASES:
        return ROAD_ALIASES[t]
    if "高架" in t and re.match(r"^(國道|國)?(1|一)號?", t):
        return "1高架"
    m = re.match(r"^(?:國道|國|freeway)?([0-9]{1,2}|[一二三四五六七八九十]{1,3})(甲)?(?:號|线|線)?(?:高速公路)?$", t, re.I)
    if m:
        return cn_number(m.group(1)) + (m.group(2) or "")
    m = re.match(r"^台(\d{1,3})線?$", t)
    if m:
        return "台" + m.group(1)
    return t


def road_label(key: str) -> str:
    if key == "1高架":
        return "國道1號汐止五股高架"
    if re.match(r"^\d+甲$", key):
        return f"國道{key}"
    if key.isdigit():
        return f"國道{key}號"
    if key.startswith("台"):
        return f"{key}線"
    return key


def dir_key(text: str) -> str:
    return DIR_WORDS.get(str(text or "").strip().lower(), "")


def _place(text: str) -> str:
    return re.sub(r"(交流道|系統|端)$", "", str(text or "").strip()).replace("臺", "台")


def _same_place(name: str, want: str) -> bool:
    a, b = _place(name), _place(want)
    return bool(a and b) and (a == b or b in a)


# ── 國道路段 ─────────────────────────────────────────────────────────────

def section_rows(doc) -> list:
    """Section.xml／JSON → [[id, 國道鍵, 方向, 起點, 終點, 長度, 起點里程, 終點里程], ...]。"""
    out = []
    for r in rows_of(doc, "Sections", "Section"):
        rs = r.get("RoadSection") if isinstance(r.get("RoadSection"), dict) else {}
        mile = r.get("SectionMile") if isinstance(r.get("SectionMile"), dict) else {}
        sid = str(r.get("SectionID") or "").strip()
        if not sid:
            continue
        out.append([sid, road_key(r.get("RoadName") or ""), str(r.get("RoadDirection") or "").strip().upper(),
                    str(rs.get("Start") or ""), str(rs.get("End") or ""), _num(r.get("SectionLength"), 0.0) or 0.0,
                    km_of(mile.get("StartKM")), km_of(mile.get("EndKM"))])
    return out


def live_map(doc) -> tuple:
    """LiveTraffic → ({SectionID: (旅行時間秒, 速度, 壅塞等級)}, 資料時間字串)。沒資料的值是 None。"""
    out = {}
    for r in rows_of(doc, "LiveTraffics", "LiveTraffic"):
        sid = str(r.get("SectionID") or "").strip()
        tt, sp, lv = _num(r.get("TravelTime")), _num(r.get("TravelSpeed")), _num(r.get("CongestionLevel"))
        out[sid] = (tt if tt is not None and tt > 0 else None,
                    sp if sp is not None and sp >= 0 else None,
                    int(lv) if lv is not None else None)
    body = _unwrap(doc)
    stamp = str(body.get("UpdateTime") or "") if isinstance(body, dict) else ""
    return out, stamp


def ordered(rows: list, direction: str) -> list:
    """同一個方向照行車順序排（南、東向里程變大；北、西向里程變小）。"""
    mid = lambda r: (r[6] + r[7]) / 2.0
    return sorted(rows, key=mid, reverse=direction in ("N", "W"))


def pick(rows: list, road: str, direction: str, start: str, end: str) -> list:
    """[(方向, [路段...])]：照國道、方向、起點、終點選出來。"""
    mine = [r for r in rows if r[1] == road]
    if not mine:
        names = sorted({r[1] for r in rows}, key=lambda k: (not k[:1].isdigit(), len(k), k))
        raise RoadError(f"找不到「{road_label(road)}」的路段。可以查：" + "、".join(road_label(k) for k in names[:20]))
    dirs = [direction] if direction else [d for d in ("N", "S", "E", "W") if any(r[2] == d for r in mine)]
    found = []
    for d in dirs:
        line = ordered([r for r in mine if r[2] == d], d)
        if not line:
            continue
        if not start and not end:
            found.append((d, line))
            continue
        i = next((n for n, r in enumerate(line) if start and _same_place(r[3], start)), 0 if not start else None)
        if i is None:
            continue
        j = next((n for n in range(i, len(line)) if end and _same_place(line[n][4], end)),
                 len(line) - 1 if not end else None)
        if j is None:
            continue
        found.append((d, line[i:j + 1]))
    if not found:
        if direction and not any(r[2] == direction for r in mine):
            have = "、".join(DIRS.get(d, d) for d in sorted({r[2] for r in mine}))
            raise RoadError(f"{road_label(road)} 沒有{DIRS.get(direction, direction)}的路段（有：{have}）。")
        names = []
        for r in ordered(mine, mine[0][2]):
            for n in (r[3], r[4]):
                if n not in names:
                    names.append(n)
        span = f"{start or '起點'}→{end or '終點'}"
        raise RoadError(f"{road_label(road)} 找不到 {span} 這一段（方向對嗎？）。這條路的交流道："
                        + "、".join(names[:40]))
    if start and end and not direction and len(found) > 1:
        found = found[:1]
    return found


def unit_speed(kmh: float, unit: str) -> str:
    return f"{kmh * 0.621371:.0f} mph" if unit == "mi" else f"{kmh:.0f} km/h"


def unit_dist(km: float, unit: str) -> str:
    return f"{km * 0.621371:.1f} 英里" if unit == "mi" else f"{km:.1f} 公里"


def dur(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s} 秒"
    m = s // 60
    if m < 60:
        return f"{m} 分 {s % 60:02d} 秒" if m < 10 and s % 60 else f"{m} 分鐘"
    return f"{m // 60} 小時 {m % 60} 分"


def level_of(level, speed):
    """壅塞等級 → (圖示, 字)。沒有等級就照速度猜（高公局的分級：80↑順暢、40↑車多、其餘壅塞）。"""
    if level in LEVELS:
        return LEVELS[level]
    if speed is None:
        return ("⚪", "沒資料")
    return LEVELS[1] if speed >= 80 else LEVELS[2] if speed >= 40 else LEVELS[4]


def summarize(line: list, live: dict) -> dict:
    """一串路段 → 總長、總時間、平均速度、最慢的等級、有幾段沒資料。"""
    length = sum(r[5] for r in line)
    total, missing, worst = 0.0, 0, 0
    for r in line:
        tt, sp, lv = live.get(r[0], (None, None, None))
        if tt is None and sp:
            tt = r[5] / sp * 3600 if sp > 0 else None
        if tt is None:
            missing += 1
            tt = r[5] / 90.0 * 3600            # 沒資料的段落用 90 km/h 估
        total += tt
        worst = max(worst, {"順暢": 1, "車多": 2, "壅塞": 4, "封閉": 6}.get(level_of(lv, sp)[1], 0))
    avg = length / (total / 3600) if total > 0 else 0
    return {"length": length, "seconds": total, "avg": avg, "missing": missing, "worst": worst}


def _worst_label(rank: int) -> str:
    return {0: "⚪ 沒資料", 1: "🟢 順暢", 2: "🟡 車多", 3: "🟡 車多", 4: "🔴 有壅塞", 5: "🔴 有壅塞", 6: "⛔ 有封閉"}[rank]


def span_title(road: str, d: str, line: list) -> str:
    return f"{road_label(road)} {DIRS.get(d, d)} {line[0][3]}→{line[-1][4]}"


def freeway_text(road: str, found: list, live: dict, stamp: str, settings: dict, events=None) -> str:
    unit, limit = settings["unit"], int(settings["lines"])
    out = []
    for d, line in found:
        s = summarize(line, live)
        out.append(f"🚗 {span_title(road, d, line)}（{len(line)} 段，{unit_dist(s['length'], unit)}）")
        out.append(f"約 {dur(s['seconds'])}・平均 {unit_speed(s['avg'], unit)}・{_worst_label(s['worst'])}"
                   + (f"（{s['missing']} 段沒資料，用 90 km/h 估）" if s["missing"] else ""))
        shown = line
        if len(line) > limit:                      # 太多段：只列最慢的那幾段，照行車順序
            slow = sorted(line, key=lambda r: (live.get(r[0], (None, None, None))[1] or 999))[:limit]
            keep = {r[0] for r in slow}
            shown = [r for r in line if r[0] in keep]
            out.append(f"（共 {len(line)} 段，只列最慢的 {limit} 段；/road_set lines 改行數）")
        for r in shown:
            tt, sp, lv = live.get(r[0], (None, None, None))
            icon, _word = level_of(lv, sp)
            speed = unit_speed(sp, unit) if sp is not None else "—"
            out.append(f"{icon} {r[3]}→{r[4]} {speed}" + (f"・{dur(tt)}" if tt else ""))
        if events is not None:
            near = events_on(events, road, d, line)
            if near:
                out.append(f"⚠️ 這段有 {len(near)} 則事件：")
                out += [f"  {event_line(e)}" for e in near[:3]]
        out.append("")
    out.append(f"資料時間 {_clock(stamp)}（高速公路局開放資料）" if stamp else "（高速公路局開放資料）")
    return "\n".join(out).strip()


def _clock(stamp: str) -> str:
    m = re.search(r"T(\d{2}:\d{2})", stamp or "")
    return m.group(1) if m else (stamp or "")


# ── 事件（1968 即時事件）─────────────────────────────────────────────────

def event_rows(doc) -> list:
    out = []
    for r in rows_of(doc, "LiveEvents", "LiveEvent"):
        loc = r.get("Location") if isinstance(r.get("Location"), dict) else {}
        fw = loc.get("FreeExpressHighway") if isinstance(loc.get("FreeExpressHighway"), dict) else {}
        impact = r.get("Impact") if isinstance(r.get("Impact"), dict) else {}
        out.append({"title": str(r.get("EventTitle") or "事件").strip(),
                    "desc": str(r.get("Description") or "").strip(),
                    "road": road_key(fw.get("Road") or ""),
                    "dir": dir_key(str(fw.get("Direction") or "")[:1]) or ("" if not fw.get("Direction") else "B"),
                    "start": km_of(fw.get("StartKM")), "end": km_of(fw.get("EndKM")),
                    "time": str(r.get("EffectiveTime") or r.get("PublishTime") or ""),
                    "impact": str(impact.get("Description") or "").strip()})
    return out


def _rank(e) -> int:
    return EVENT_ORDER.index(e["title"]) if e["title"] in EVENT_ORDER else len(EVENT_ORDER) - 2


def event_line(e) -> str:
    when = re.search(r"T(\d{2}:\d{2})", e["time"])
    icon = EVENT_ICON.get(e["title"], "⚠️")
    impact = f"（{e['impact']}）" if e["impact"] and e["impact"] != "無影響" else ""
    return f"{icon} {e['desc'] or e['title']}{impact}" + (f" {when.group(1)}起" if when else "")


def events_on(events: list, road: str, d: str, line: list) -> list:
    """落在這串路段里程內的事件（壅塞事件不算，路況本身就看得到）。"""
    lo = min(min(r[6], r[7]) for r in line)
    hi = max(max(r[6], r[7]) for r in line)
    out = []
    for e in events:
        if e["road"] != road or e["title"] == "壅塞事件" or (e["dir"] not in ("", "B", d)):
            continue
        a, b = min(e["start"], e["end"]), max(e["start"], e["end"])
        if a < 0 or (b >= lo and a <= hi):
            out.append(e)
    return sorted(out, key=_rank)


# ── HTTP ────────────────────────────────────────────────────────────────

def _mask(text: str, secrets) -> str:
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    return text


def http(session, method: str, url: str, who: str, secrets=(), **kw):
    """送一次；錯誤一律變成一句中文（RoadError）。回 Response。"""
    try:
        resp = session.request(method, url, timeout=TIMEOUT, **kw)
    except requests.Timeout:
        raise RoadError(f"{who}逾時沒回應，等一下再試。")
    except requests.RequestException:
        raise RoadError(f"連不上{who}（網路斷了？）。")
    code = int(getattr(resp, "status_code", 0) or 0)
    if code in (401, 403):
        raise RoadError(f"{who}不接受金鑰（{code}）：到 SYNOX 網頁的工具頁「🚗 路況」→「🔌 連接」檢查一下。")
    if code == 429:
        raise RoadError(f"{who}說查太多次了，過一陣子再試。")
    if code >= 500:
        raise RoadError(f"{who}那邊出錯了（{code}），等一下再試。")
    if code >= 400:
        detail = ""
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            detail = (err.get("message") if isinstance(err, dict) else err) or body.get("message") or ""
        except Exception:
            detail = ""
        detail = _mask(str(detail), secrets)[:150]
        raise RoadError(f"{who}回錯誤（{code}）" + (f"：{detail}" if detail else "。"))
    return resp


def fetch_doc(session, url: str):
    resp = http(session, "GET", url, "高速公路局的資料服務")
    return parse_doc(resp.content)


def _dur_seconds(value):
    """Google 的 '1234s'、數字 → 秒；看不懂回 None。"""
    if isinstance(value, (int, float)):
        return float(value)
    m = re.match(r"^\s*(\d+(?:\.\d+)?)s\s*$", str(value or ""))
    return float(m.group(1)) if m else None


def google_eta(session, key: str, origin: str, dest: str) -> dict:
    body = {"origin": {"address": origin}, "destination": {"address": dest}, "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE", "languageCode": "zh-TW", "regionCode": "TW", "units": "METRIC"}
    resp = http(session, "POST", GOOGLE_ROUTES_URL, "Google 地圖", secrets=(key,), json=body,
                headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": GOOGLE_FIELDS})
    try:
        data = resp.json()
    except ValueError:
        raise RoadError("看不懂 Google 地圖回的資料。")
    routes = data.get("routes") if isinstance(data, dict) else None
    if not routes:
        raise RoadError(f"Google 地圖找不到「{origin}」到「{dest}」的開車路線（地名寫完整一點試試）。")
    r = routes[0]
    secs = _dur_seconds(r.get("duration"))
    if secs is None:
        raise RoadError("Google 地圖沒有給時間。")
    return {"seconds": secs, "free": _dur_seconds(r.get("staticDuration")),
            "meters": _num(r.get("distanceMeters")), "by": "Google 地圖"}


def tdx_token(session, cid: str, secret: str) -> str:
    resp = http(session, "POST", TDX_TOKEN_URL, "TDX", secrets=(secret,),
                data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
                headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        token = resp.json().get("access_token")
    except Exception:
        token = None
    if not token:
        raise RoadError("TDX 沒有給通行證（Client Id／Secret 對嗎？）。")
    return token


def tdx_point(session, token: str, place: str) -> str:
    """'25.04,121.51' 原樣；地址 → TDX 地理編碼 → '緯度,經度'。"""
    m = LATLON_RE.match(place)
    if m:
        return f"{float(m.group(1))},{float(m.group(2))}"
    resp = http(session, "GET", TDX_GEOCODE_URL + quote(place, safe=""), "TDX 地址查詢", secrets=(token,),
                params={"format": "JSON"}, headers={"Authorization": f"Bearer {token}"})
    try:
        data = resp.json()
    except ValueError:
        data = None
    rows = data if isinstance(data, list) else (data or {}).get("Data") if isinstance(data, dict) else None
    for row in rows or []:
        m = POINT_RE.search(str((row or {}).get("Geometry") or ""))
        if m:
            return f"{float(m.group(2))},{float(m.group(1))}"
    raise RoadError(f"TDX 找不到「{place}」在哪（寫完整地址，或直接給「緯度,經度」）。")


def _first(obj, keys):
    """在 dict 裡（含一層 travel_summary）找第一個有的數字欄位。"""
    if not isinstance(obj, dict):
        return None
    for k in keys:
        v = _num(obj.get(k))
        if v is not None:
            return v
    return None


def tdx_eta(session, cid: str, secret: str, origin: str, dest: str) -> dict:
    token = tdx_token(session, cid, secret)
    a, b = tdx_point(session, token, origin), tdx_point(session, token, dest)
    params = {"origin": a, "destination": b, **TDX_DRIVE,
              "depart": now().strftime("%Y-%m-%dT%H:%M:%S")}
    resp = http(session, "GET", TDX_ROUTING_URL, "TDX 路徑規劃", secrets=(token, secret), params=params,
                headers={"Authorization": f"Bearer {token}"})
    try:
        data = resp.json()
    except ValueError:
        raise RoadError("看不懂 TDX 路徑規劃回的資料。")
    body = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    routes = body.get("routes") if isinstance(body, dict) else None
    if not routes:
        raise RoadError(f"TDX 找不到「{origin}」到「{dest}」的路線。")
    r = routes[0]
    secs = _first(r, ("travel_time", "duration", "total_time"))
    meters = _first(r, ("distance", "length", "total_distance"))
    sections = r.get("sections") if isinstance(r.get("sections"), list) else []
    if secs is None or meters is None:
        part_t, part_m = 0.0, 0.0
        for s in sections:
            summary = s.get("travel_summary") if isinstance(s, dict) and isinstance(s.get("travel_summary"), dict) else s
            part_t += _first(summary, ("duration", "travel_time")) or 0
            part_m += _first(summary, ("length", "distance")) or 0
        secs = secs if secs is not None else (part_t or None)
        meters = meters if meters is not None else (part_m or None)
    if secs is None:
        raise RoadError("TDX 沒有給時間。")
    return {"seconds": secs, "free": None, "meters": meters, "by": "TDX"}


def eta_text(origin: str, dest: str, r: dict, unit: str) -> str:
    extra = []
    if r.get("meters"):
        extra.append(unit_dist(r["meters"] / 1000.0, unit))
    if r.get("free") and r["free"] > 0:
        diff = r["seconds"] - r["free"]
        extra.append(f"不塞車約 {dur(r['free'])}" + (f"，現在多 {dur(diff)}" if diff >= 60 else ""))
    return f"🚗 {origin} → {dest}：約 {dur(r['seconds'])}" + (f"（{'；'.join(extra)}）" if extra else "") + f"・{r['by']}"


def split_eta(text: str):
    parts = [p.strip() for p in SPLIT_ETA.split(str(text or "").strip(), maxsplit=1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def parse_freeway(text: str, settings: dict):
    """'國1 北 林口 五股' → (國道鍵, 方向, 起點, 終點)；沒寫國道用預設的。看不懂丟 RoadError。"""
    tokens = str(text or "").split()
    road = ""
    if tokens:
        key = road_key(tokens[0])
        if key and (key[0].isdigit() or key.startswith("台") or key in ROAD_ALIASES.values()
                    or tokens[0] in ROAD_ALIASES or "國" in tokens[0] or "聯絡道" in tokens[0] or "高架" in tokens[0]):
            road, tokens = key, tokens[1:]
    if not road:
        road = settings.get("road") or ""
    if not road:
        raise RoadError("要查哪一條國道？例：/road 國1 北 林口 五股，或先 /road_set road 國1 設預設。")
    direction = ""
    if tokens and dir_key(tokens[0]):
        direction, tokens = dir_key(tokens[0]), tokens[1:]
    elif not tokens or len(tokens) < 2:
        direction = settings.get("dir") or ""
    start = tokens[0] if tokens else ""
    end = tokens[1] if len(tokens) > 1 else ""
    if len(tokens) > 2:
        raise RoadError("看不懂：格式是「國道 方向 起點 終點」，例：國1 北 林口 五股。")
    return road, direction, start, end


# ── 設定 ────────────────────────────────────────────────────────────────

def clean_settings(raw) -> dict:
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for k in DEFAULTS:
            if k in raw:
                out[k] = raw[k]
    return out


def set_value(key: str, value: str):
    """(正式的鍵, 要存的值) 或丟 RoadError。value 寫「預設」＝回預設值。"""
    k = KEY_ALIASES.get(key.strip().lower()) or KEY_ALIASES.get(key.strip())
    if not k:
        raise RoadError("沒有這個設定。可以改：provider（供應商）、road（預設國道）、dir（預設方向）、"
                        "unit（單位）、lines（行數）、events（事件）。")
    v = value.strip()
    if v in ("預設", "default", "reset"):
        return k, DEFAULTS[k]
    low = v.lower()
    if k == "provider":
        if low not in PROVIDERS:
            raise RoadError("provider 只能是 auto、tdx、google。")
        return k, low
    if k == "road":
        if low in ALL_WORDS:
            return k, ""
        key = road_key(v)
        if not key:
            raise RoadError("要寫國道，例：國1、國3、國5。")
        return k, key
    if k == "dir":
        if low in ALL_WORDS:
            return k, ""
        d = dir_key(v)
        if not d:
            raise RoadError("方向只能是 北、南、東、西 或 全部。")
        return k, d
    if k == "unit":
        if low in ("km", "公里", "公制", "kmh", "km/h"):
            return k, "km"
        if low in ("mi", "mile", "miles", "英里", "mph"):
            return k, "mi"
        raise RoadError("單位只能是 km（公里）或 mi（英里）。")
    if k == "lines":
        if not v.isdigit() or not 3 <= int(v) <= 40:
            raise RoadError("行數要是 3 到 40 的數字。")
        return k, int(v)
    if low in ON_WORDS:
        return k, True
    if low in OFF_WORDS:
        return k, False
    raise RoadError("events 只能是 開 或 關。")


def settings_lines(s: dict) -> list:
    return [f"• provider（起訖行車時間用誰）：{PROVIDERS.get(s['provider'], s['provider'])}",
            f"• road（預設國道）：{road_label(s['road']) if s['road'] else '沒設'}",
            f"• dir（預設方向）：{DIRS.get(s['dir'], '全部')}",
            f"• unit（單位）：{'英里、mph' if s['unit'] == 'mi' else '公里、km/h'}",
            f"• lines（一次最多列幾段）：{s['lines']}",
            f"• events（路況後面附事故施工）：{'開' if s['events'] else '關'}"]


KEY_HINT = ("金鑰不在聊天裡設（聊天紀錄會留在 Telegram）：到 SYNOX 網頁的工具頁「🚗 路況」按「🔌 連接」填，"
            "或在電腦上執行 python -m synox connect road。")
TDX_HINT = ("TDX：到 tdx.transportdata.tw 註冊會員（免費）→ 會員中心「資料服務 → API 金鑰」拿 Client Id 與 Client Secret。")
GOOGLE_HINT = ("Google：到 Google Cloud Console 建專案、啟用「Routes API」、在「憑證」建 API 金鑰（要綁帳單，有免費額度）。")


def install(app, pack):
    g = pack.group
    cmd = "/" + pack.manifest.group

    def store(user):
        return pack.store.of(user)

    def data(user) -> dict:
        d = store(user).load({})
        return d if isinstance(d, dict) else {}

    def settings_of(user) -> dict:
        return clean_settings(data(user).get("settings"))

    def routes_of(user) -> dict:
        r = data(user).get("routes")
        return r if isinstance(r, dict) else {}

    def secret(user, name):
        try:
            value = pack.secret(name, user)
        except Exception:                       # 保管箱沒開之類：當作沒填
            return ""
        return value.strip() if isinstance(value, str) else ""

    def keys_status(user) -> dict:
        return {"tdx": bool(secret(user, "tdx_client_id") and secret(user, "tdx_client_secret")),
                "google": bool(secret(user, "google_api_key"))}

    # ── 國道資料 ───────────────────────────────────────────────────────
    def sections(user, session) -> list:
        mine = store(user)
        cached = mine.load(None, key="sections")
        stamp = time.mktime(now().timetuple())
        if isinstance(cached, dict) and isinstance(cached.get("rows"), list) and cached["rows"] \
                and 0 <= stamp - float(cached.get("at") or 0) < SECTIONS_TTL:
            return cached["rows"]
        rows = section_rows(fetch_doc(session, SECTION_URL))
        if not rows:
            raise RoadError("高速公路局的路段資料是空的，等一下再試。")
        mine.save({"at": stamp, "rows": rows}, key="sections")
        return rows

    def events_list(session) -> list:
        return event_rows(fetch_doc(session, EVENTS_URL))

    def freeway_report(user, session, spec_text: str, s: dict) -> str:
        road, direction, start, end = parse_freeway(spec_text, s)
        rows = sections(user, session)
        found = pick(rows, road, direction, start, end)
        live, stamp = live_map(fetch_doc(session, LIVE_URL))
        events = None
        if s["events"]:
            try:
                events = events_list(session)
            except RoadError:
                events = None                   # 事件抓不到不影響路況
        return freeway_text(road, found, live, stamp, s, events)

    def eta(user, session, origin: str, dest: str, s: dict) -> str:
        provider = s["provider"]
        have = keys_status(user)
        if provider == "auto":
            provider = "tdx" if have["tdx"] else "google" if have["google"] else ""
        if provider == "tdx" and not have["tdx"]:
            raise RoadError("還沒設定 TDX 的金鑰。" + TDX_HINT + KEY_HINT)
        if provider == "google" and not have["google"]:
            raise RoadError("還沒設定 Google 地圖的金鑰。" + GOOGLE_HINT + KEY_HINT)
        if not provider:
            raise RoadError("查起點到終點的行車時間要一組地圖金鑰（TDX 或 Google 擇一）。\n" + TDX_HINT + "\n"
                            + GOOGLE_HINT + "\n" + KEY_HINT + f"\n設好後可以用 {cmd}_set provider tdx 或 google 指定。")
        if provider == "tdx":
            r = tdx_eta(session, secret(user, "tdx_client_id"), secret(user, "tdx_client_secret"), origin, dest)
        else:
            r = google_eta(session, secret(user, "google_api_key"), origin, dest)
        return eta_text(origin, dest, r, s["unit"])

    def run(api, chat_id, work):
        try:
            text = work(new_session())
        except RoadError as e:
            text = f"❌ {e}"
        api.send_message(chat_id, text)

    # ── /road [國道] [方向] [起點] [終點] ─────────────────────────────
    def now_(api, chat_id, user, values):
        s = settings_of(user)
        text = (values.get("query") or "").strip()
        if not text and not s["road"]:
            api.send_message(chat_id, f"🚗 要查哪一條國道？例：{cmd} 國1 北 林口 五股、{cmd} 國3 南、{cmd} 國5。\n"
                                      f"設預設國道之後只打 {cmd} 就好：{cmd}_set road 國1")
            return
        run(api, chat_id, lambda session: freeway_report(user, session, text, s))

    # ── /road_events [國道] [方向] [關鍵字] ───────────────────────────
    def events_(api, chat_id, user, values):
        s = settings_of(user)
        tokens = (values.get("filter") or "").split()
        road = direction = ""
        if tokens:
            key = road_key(tokens[0])
            if key and (key[0].isdigit() or "國" in tokens[0] or tokens[0] in ROAD_ALIASES or key.startswith("台")):
                road, tokens = key, tokens[1:]
        if tokens and dir_key(tokens[0]):
            direction, tokens = dir_key(tokens[0]), tokens[1:]
        word = " ".join(tokens)

        def work(session):
            rows = events_list(session)
            if road:
                rows = [e for e in rows if e["road"] == road]
            if direction:
                rows = [e for e in rows if e["dir"] in ("", "B", direction)]
            if word:
                rows = [e for e in rows if word in e["title"] or word in e["desc"]]
            where = " ".join(x for x in (road_label(road) if road else "國道", DIRS.get(direction, ""), word) if x)
            if not rows:
                return f"✅ {where}現在沒有事故、施工或管制事件。"
            rows = sorted(rows, key=lambda e: (_rank(e), e["time"]))
            counts = {}
            for e in rows:
                counts[e["title"]] = counts.get(e["title"], 0) + 1
            head = "、".join(f"{t} {n}" for t, n in sorted(counts.items(), key=lambda kv: _rank({"title": kv[0]})))
            out = [f"⚠️ {where}現在的事件（{len(rows)} 則：{head}）"]
            out += [event_line(e) for e in rows[:int(s["lines"])]]
            if len(rows) > int(s["lines"]):
                out.append(f"…還有 {len(rows) - int(s['lines'])} 則（加國道、方向或關鍵字縮小範圍）")
            out.append("（高速公路局 1968 即時事件）")
            return "\n".join(out)

        run(api, chat_id, work)

    # ── /road_eta <起點> 到 <終點> ─────────────────────────────────────
    def eta_(api, chat_id, user, values):
        pair = split_eta(values.get("trip") or "")
        if not pair:
            api.send_message(chat_id, f"用法：{cmd}_eta <起點> 到 <終點>（例：{cmd}_eta 台北車站 到 桃園機場）")
            return
        s = settings_of(user)
        run(api, chat_id, lambda session: eta(user, session, pair[0], pair[1], s))

    # ── 常用路線 ───────────────────────────────────────────────────────
    def save_(api, chat_id, user, values):
        name = (values.get("name") or "").strip()
        spec = (values.get("spec") or "").strip()
        if not name or not spec:
            api.send_message(chat_id, f"用法：{cmd}_save <名字> <國道路段 或 起點 到 終點>\n"
                                      f"例：{cmd}_save 上班 國1 北 林口 五股、{cmd}_save 回家 公司 到 家")
            return
        if len(name) > NAME_MAX or len(spec) > SPEC_MAX:
            api.send_message(chat_id, f"❌ 名字最多 {NAME_MAX} 字、內容最多 {SPEC_MAX} 字。")
            return
        s = settings_of(user)
        if split_eta(spec):
            kind = "eta"
        else:
            try:
                parse_freeway(spec, {**s, "road": ""})
            except RoadError as e:
                api.send_message(chat_id, f"❌ {e}")
                return
            kind = "freeway"

        def put(d):
            routes = d.setdefault("routes", {})
            if name not in routes and len(routes) >= MAX_ROUTES:
                return False
            routes[name] = {"kind": kind, "spec": spec}
            return True

        if not store(user).update(put, {}):
            api.send_message(chat_id, f"❌ 常用路線最多 {MAX_ROUTES} 條，先用 {cmd}_rm 刪一條。")
            return
        what = "起點到終點（要地圖金鑰）" if kind == "eta" else "國道路段"
        api.send_message(chat_id, f"✅ 存好了：{name}（{what}：{spec}）。用 {cmd}_routes 一次查全部常用路線。")

    def rm_(api, chat_id, user, values):
        name = (values.get("name") or "").strip()
        gone = store(user).update(lambda d: d.setdefault("routes", {}).pop(name, None), {})
        api.send_message(chat_id, f"🗑 刪掉了：{name}" if gone else f"沒有叫「{name}」的常用路線（{cmd}_settings 看有哪些）。")

    def routes_(api, chat_id, user, values):
        routes = routes_of(user)
        if not routes:
            api.send_message(chat_id, f"還沒有常用路線。存一條：{cmd}_save 上班 國1 北 林口 五股，"
                                      f"或 {cmd}_save 回家 公司 到 家")
            return
        s = settings_of(user)
        session = new_session()
        out = [f"🚗 常用路線（{len(routes)} 條）"]
        cache = {}

        def freeway_line(spec):
            road, direction, start, end = parse_freeway(spec, {**s, "road": ""})
            if "rows" not in cache:
                cache["rows"] = sections(user, session)
                cache["live"], cache["stamp"] = live_map(fetch_doc(session, LIVE_URL))
            found = pick(cache["rows"], road, direction, start, end)
            parts = []
            for d, line in found:
                sm = summarize(line, cache["live"])
                parts.append(f"{DIRS.get(d, d)} 約 {dur(sm['seconds'])}・{unit_speed(sm['avg'], s['unit'])} "
                             f"{_worst_label(sm['worst'])}")
            return "；".join(parts)

        for name, r in routes.items():
            spec = str((r or {}).get("spec") or "")
            try:
                if (r or {}).get("kind") == "eta":
                    pair = split_eta(spec)
                    if not pair:
                        raise RoadError("存的內容看不懂，重新存一次。")
                    body = eta(user, session, pair[0], pair[1], s).replace("🚗 ", "", 1)
                    out.append(f"• {name}：{body}")
                else:
                    out.append(f"• {name}（{spec}）：{freeway_line(spec)}")
            except RoadError as e:
                first = str(e).splitlines()[0]
                out.append(f"• {name}（{spec}）：❌ {first}")
        if cache.get("stamp"):
            out.append(f"國道資料時間 {_clock(cache['stamp'])}（高速公路局開放資料）")
        api.send_message(chat_id, "\n".join(out))

    # ── 設定 ───────────────────────────────────────────────────────────
    def settings_(api, chat_id, user, values):
        s = settings_of(user)
        have = keys_status(user)
        routes = routes_of(user)
        out = ["⚙️ 路況的設定"] + settings_lines(s)
        out.append(f"改：{cmd}_set <鍵> <值>（例：{cmd}_set road 國1、{cmd}_set provider google；值寫「預設」回到預設）")
        out.append("")
        out.append("🔑 地圖金鑰（查起點到終點用；國道路況不用金鑰）")
        out.append(f"• TDX：{'✅ 已設定' if have['tdx'] else '— 沒設'}")
        out.append(f"• Google 地圖：{'✅ 已設定' if have['google'] else '— 沒設'}")
        if not (have["tdx"] and have["google"]):
            out.append(KEY_HINT)
        out.append("")
        if routes:
            out.append(f"📌 常用路線（{len(routes)} 條；{cmd}_routes 一次查）")
            out += [f"• {n}：{(r or {}).get('spec', '')}" for n, r in routes.items()]
        else:
            out.append(f"📌 還沒有常用路線（{cmd}_save 上班 國1 北 林口 五股）")
        api.send_message(chat_id, "\n".join(out))

    def set_(api, chat_id, user, values):
        key = (values.get("key") or "").strip()
        value = (values.get("value") or "").strip()
        try:
            k, v = set_value(key, value)
        except RoadError as e:
            api.send_message(chat_id, f"❌ {e}")
            return
        store(user).update(lambda d: d.setdefault("settings", {}).__setitem__(k, v), {})
        line = next(x for x in settings_lines(settings_of(user)) if x.startswith(f"• {k}（"))
        api.send_message(chat_id, f"✅ 改好了 {line[2:]}")

    # ── 面板（唯讀）────────────────────────────────────────────────────
    def panel_settings(user):
        s = settings_of(user)
        return {"settings": [x[2:] for x in settings_lines(s)],
                "keys": keys_status(user),
                "routes": [{"name": n, "spec": str((r or {}).get("spec") or "")} for n, r in routes_of(user).items()]}

    pack.web_route("settings", panel_settings)

    kit.command(g, "now", now_, "看國道路段現在的車速、旅行時間、壅塞（不用金鑰）", default=True,
                positional=1, usage="[國道] [方向] [起點] [終點]（例：國1 北 林口 五股）",
                fields=[Field("query", "國道、方向、起點、終點（例：國1 北 林口 五股；不寫用預設國道）")])
    kit.command(g, "events", events_, "看國道現在的事故、施工、管制（1968 即時事件）",
                positional=1, usage="[國道] [方向] [關鍵字]（例：國3 南 施工）",
                fields=[Field("filter", "國道、方向、關鍵字（都可以不寫）")])
    kit.command(g, "eta", eta_, "查起點到終點開車要多久（要 TDX 或 Google 地圖金鑰）",
                positional=1, usage="<起點> 到 <終點>",
                fields=[Field("trip", "起點 到 終點（例：台北車站 到 桃園機場）", required=True)])
    kit.command(g, "routes", routes_, "一次查全部常用路線")
    kit.command(g, "save", save_, "存一條常用路線（國道路段，或 起點 到 終點）", effect=WRITE,
                positional=2, usage="<名字> <國道路段 或 起點 到 終點>",
                fields=[Field("name", "路線的名字（例：上班）", required=True),
                        Field("spec", "國道路段（國1 北 林口 五股）或 起點 到 終點", required=True)])
    kit.command(g, "rm", rm_, "刪掉一條常用路線", effect=WRITE,
                fields=[Field("name", "路線的名字", required=True)])
    kit.command(g, "settings", settings_, "看路況的設定、金鑰有沒有設、常用路線")
    kit.command(g, "set", set_, "改一項設定（provider、road、dir、unit、lines、events）", effect=WRITE,
                positional=2, usage="<鍵> <值>（例：road 國1、provider google、unit km、值寫「預設」回到預設）",
                fields=[Field("key", "設定的鍵（provider／road／dir／unit／lines／events）", required=True),
                        Field("value", "新的值", required=True)])
