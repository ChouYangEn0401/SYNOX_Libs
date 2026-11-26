"""🌤️ 天氣與穿衣：/wx [地點] [天數]、/wx_wear [活動] [地點]、/wx_air [地點]、/wx_typhoon [縣市]；
設定：/wx_settings（看）、/wx_set <鍵> <值>（改）。

資料來源（都免金鑰、只用 requests、只讀）：
  - 天氣與預報：Open-Meteo Forecast API（https://api.open-meteo.com/v1/forecast）
  - 地名 → 經緯度：先查內建的台灣 22 縣市表，查不到再問 Open-Meteo Geocoding API（它的中文地名常常查不到，
    所以會多試「加市／加縣」「台／臺」幾種寫法，台灣的結果優先）；也收「25.03,121.56」這種座標
  - 空氣品質：Open-Meteo Air Quality API（模式估計值，不是環境部測站的實測值）
  - 停班停課：行政院人事行政總處「天然災害停止上班及上課情形」網頁（https://www.dgpa.gov.tw/typh/daily/nds.html）；
    網頁格式可能變，解析刻意寫得寬鬆，看不懂時給人話並附原網址
設定存 pack.store.of(user)（保管箱，密文；伺服器版每人一份）：
  {"place": {"name", "lat", "lon"} 或 None, "feel": 怕冷／普通／怕熱, "activity": 一般／上班／戶外／運動／旅遊,
   "county": 停班停課預設縣市, "days": 預報天數}
測試換掉 new_session()（假的 requests.Session），不連網。
"""

import re
from datetime import date

import requests

from isdtelekit.core.registry import READ, WRITE, Field

from synox.packs import kit

FORECAST_API = "https://api.open-meteo.com/v1/forecast"
GEOCODE_API = "https://geocoding-api.open-meteo.com/v1/search"
AIR_API = "https://air-quality-api.open-meteo.com/v1/air-quality"
DGPA_URL = "https://www.dgpa.gov.tw/typh/daily/nds.html"
TIMEOUT = 15                     # 秒；AI 的工具呼叫會等它
TZ = "Asia/Taipei"
MAX_DAYS = 16                    # Open-Meteo 最多 16 天
DEFAULT_DAYS = 3
MAX_PLACE = 60
USER_AGENT = "SYNOX-weather-pack/0.1"

# 台灣 22 縣市（縣市政府附近的經緯度；Open-Meteo 的中文地名查詢常常查不到「台北」「花蓮」，先查這張表）
COUNTIES = (
    ("臺北市", 25.0375, 121.5637), ("新北市", 25.0120, 121.4657), ("桃園市", 24.9936, 121.3010),
    ("臺中市", 24.1477, 120.6736), ("臺南市", 22.9999, 120.2270), ("高雄市", 22.6273, 120.3014),
    ("基隆市", 25.1276, 121.7392), ("新竹市", 24.8138, 120.9675), ("嘉義市", 23.4801, 120.4491),
    ("新竹縣", 24.8387, 121.0177), ("苗栗縣", 24.5602, 120.8214), ("彰化縣", 24.0518, 120.5161),
    ("南投縣", 23.9157, 120.6639), ("雲林縣", 23.7092, 120.4313), ("嘉義縣", 23.4518, 120.2555),
    ("屏東縣", 22.6727, 120.4880), ("宜蘭縣", 24.7570, 121.7533), ("花蓮縣", 23.9872, 121.6015),
    ("臺東縣", 22.7583, 121.1444), ("澎湖縣", 23.5711, 119.5793), ("金門縣", 24.4493, 118.3767),
    ("連江縣", 26.1505, 119.9499),
)
# 只寫兩個字時優先當「市」（新竹→新竹市、嘉義→嘉義市）；表裡的順序就是這個意思
COUNTY_ALIASES = (("馬祖", "連江縣"), ("北市", "臺北市"), ("雙北", "臺北市"), ("中市", "臺中市"), ("南市", "臺南市"))

# WMO 天氣代碼（Open-Meteo 的 weather_code）
WEATHER_CODES = (
    (0, "☀️", "晴"), (1, "🌤️", "大致晴朗"), (2, "⛅", "局部多雲"), (3, "☁️", "陰"),
    (45, "🌫️", "霧"), (48, "🌫️", "霧（結霜）"),
    (51, "🌦️", "毛毛雨"), (53, "🌦️", "毛毛雨"), (55, "🌧️", "較大的毛毛雨"),
    (56, "🌧️", "凍毛毛雨"), (57, "🌧️", "凍毛毛雨"),
    (61, "🌧️", "小雨"), (63, "🌧️", "中雨"), (65, "🌧️", "大雨"),
    (66, "🌧️", "凍雨"), (67, "🌧️", "凍雨"),
    (71, "🌨️", "小雪"), (73, "🌨️", "中雪"), (75, "❄️", "大雪"), (77, "🌨️", "冰霰"),
    (80, "🌦️", "短暫陣雨"), (81, "🌧️", "陣雨"), (82, "⛈️", "強陣雨"),
    (85, "🌨️", "陣雪"), (86, "❄️", "強陣雪"),
    (95, "⛈️", "雷雨"), (96, "⛈️", "雷雨夾冰雹"), (99, "⛈️", "強雷雨夾冰雹"),
)
WEEK = "一二三四五六日"

# 活動類型：(正式名稱, 認得的寫法)
ACTIVITIES = (
    ("一般", ("一般", "普通", "日常", "general", "normal")),
    ("上班", ("上班", "通勤", "上學", "工作", "work", "office", "commute")),
    ("戶外", ("戶外", "爬山", "登山", "野餐", "露營", "outdoor", "hiking")),
    ("運動", ("運動", "跑步", "慢跑", "騎車", "單車", "健身", "sport", "sports", "run", "running")),
    ("旅遊", ("旅遊", "旅行", "出遊", "出去玩", "travel", "trip")),
)
# 溫度偏好：(正式名稱, 認得的寫法, 體感要加減幾度再挑衣服)
FEELS = (
    ("怕冷", ("怕冷", "冷底", "cold"), -3),
    ("普通", ("普通", "一般", "正常", "normal"), 0),
    ("怕熱", ("怕熱", "熱底", "hot"), 3),
)
# /wx_set 的鍵：(正式名稱, 認得的寫法, 說明)
SETTING_KEYS = (
    ("地點", ("地點", "place", "location", "城市"), "預設查哪裡（縣市、地名或「緯度,經度」）"),
    ("體感", ("體感", "溫度偏好", "偏好", "feel", "怕冷怕熱"), "怕冷／普通／怕熱（調整穿衣門檻）"),
    ("活動", ("活動", "活動類型", "activity"), "一般／上班／戶外／運動／旅遊"),
    ("縣市", ("縣市", "停班停課", "county"), "停班停課預設看哪個縣市"),
    ("天數", ("天數", "預報天數", "days"), f"預報幾天（1～{MAX_DAYS}）"),
)
CLEAR_WORDS = ("清除", "清掉", "取消", "無", "不要", "預設", "clear", "none", "reset")

COORDS_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,，]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
TRAILING_DAYS_RE = re.compile(r"^(.*?)\s*(\d{1,2})\s*天?\s*$")
TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<\s*br\s*/?\s*>|</\s*p\s*>|</\s*div\s*>", re.IGNORECASE)
DROP_RE = re.compile(r"<script\b.*?</script\s*>|<style\b.*?</style\s*>|<!--.*?-->", re.IGNORECASE | re.DOTALL)
ROW_SPLIT_RE = re.compile(r"<tr\b[^>]*>", re.IGNORECASE)
CELL_SPLIT_RE = re.compile(r"<t[dh]\b[^>]*>", re.IGNORECASE)
ROW_END_RE = re.compile(r"</tr\s*>|</tbody\s*>|</table\s*>", re.IGNORECASE)
ENTITY_RE = re.compile(r"&(#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[a-zA-Z]{2,8});")
ENTITIES = (("nbsp", " "), ("amp", "&"), ("lt", "<"), ("gt", ">"), ("quot", '"'), ("apos", "'"), ("#39", "'"))
ROC_DATE_RE = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
UPDATED_RE = re.compile(r"更新時間\s*[：:]\s*([0-9]{4}[/\-][0-9]{1,2}[/\-][0-9]{1,2}(?:\s+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?)")
NO_CLOSURE = ("無停班停課訊息", "無停止上班上課訊息", "無停止上班及上課")
OFF_WORDS = ("停止上班", "停止上課", "停班", "停課")
ON_WORDS = ("照常上班", "照常上課", "正常上班", "正常上課", "尚未列入")


class WeatherError(Exception):
    """給使用者看的一句話（已經是人話）。"""


def new_session():
    """HTTP 傳輸（每次查詢一個新的，不共用）。測試把這個換成假的（不連網）。"""
    return requests.Session()


def empty():
    """一個人的設定（每次回新的一份）。"""
    return {"place": None, "feel": "普通", "activity": "一般", "county": "", "days": DEFAULT_DAYS}


def settings_of(store) -> dict:
    data = store.load(None)
    out = empty()
    if isinstance(data, dict):
        for key in out:
            if key in data and data[key] is not None:
                out[key] = data[key]
    return out


# ── 名字正規化 ─────────────────────────────────────────────────────────

def tw(text: str) -> str:
    """台→臺、去空白（比對縣市名用）。"""
    return re.sub(r"\s+", "", str(text or "")).replace("台", "臺")


def county_of(text: str) -> str:
    """「台北」「臺北市」「台北市信義區」「北市」「馬祖」→ 正式縣市名；看不出來回 ""。"""
    t = tw(text)
    if not t:
        return ""
    for alias, name in COUNTY_ALIASES:
        if t == alias:
            return name
    for name, _lat, _lon in COUNTIES:
        if t == name or t == name[:-1]:
            return name
    for name, _lat, _lon in COUNTIES:                 # 前面就是縣市全名：臺北市信義區
        if t.startswith(name):
            return name
    return ""


def county_coords(name: str):
    for county, lat, lon in COUNTIES:
        if county == name:
            return lat, lon
    return None


def pick(words, text):
    """在 (正式名稱, 寫法…) 表裡找 text；找到回正式名稱，沒有回 ""。"""
    t = str(text or "").strip().lower()
    for row in words:
        if t == row[0].lower() or t in tuple(w.lower() for w in row[1]):
            return row[0]
    return ""


def feel_offset(feel: str) -> int:
    for name, _words, offset in FEELS:
        if name == feel:
            return offset
    return 0


def describe_code(code):
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "🌡️", "天氣不明"
    for c, icon, text in WEATHER_CODES:
        if c == code:
            return icon, text
    return "🌡️", f"天氣代碼 {code}"


def num(value, digits=0):
    """數字 → 字串（None／怪東西 → "?"）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "?"
    if digits == 0:
        return str(int(round(v)))
    return f"{v:.{digits}f}"


def fnum(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── HTTP ─────────────────────────────────────────────────────────────

def fetch_json(session, url: str, params: dict, what: str) -> dict:
    try:
        resp = session.get(url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        raise WeatherError(f"連不上{what}（網路問題或對方沒回應），等一下再試。")
    status = getattr(resp, "status_code", 0)
    try:
        data = resp.json()
    except ValueError:
        data = None
    if status != 200 or not isinstance(data, dict) or data.get("error"):
        reason = data.get("reason") if isinstance(data, dict) else ""
        extra = f"：{str(reason)[:80]}" if reason else ""
        raise WeatherError(f"{what}回了錯誤（HTTP {status}{extra}），等一下再試。")
    return data


# ── 地點 ─────────────────────────────────────────────────────────────

def _variants(text: str):
    t = re.sub(r"\s+", "", text)
    out = []
    for v in (t, t.replace("臺", "台"), t.replace("台", "臺")):
        for w in (v, v + "市", v + "縣"):
            if w not in out:
                out.append(w)
    if re.search(r"[A-Za-z]", text) and text.strip() not in out:
        out.insert(0, text.strip())
    return out[:6]


def geocode(session, text: str):
    """Open-Meteo Geocoding：試幾種寫法，台灣的結果優先；找不到回 None。"""
    best = None
    for name in _variants(text):
        data = fetch_json(session, GEOCODE_API, {"name": name, "count": 5, "language": "zh", "format": "json"},
                          "地名查詢（Open-Meteo）")
        results = [r for r in (data.get("results") or []) if isinstance(r, dict)
                   and fnum(r.get("latitude")) is not None and fnum(r.get("longitude")) is not None]
        if not results:
            continue
        tw_first = [r for r in results if r.get("country_code") == "TW"]
        found = (tw_first or results)[0]
        if tw_first:
            best = found
            break
        if best is None:
            best = found
    if best is None:
        return None
    parts = [str(best.get("name") or text)]
    for key in ("admin2", "admin1", "country"):
        value = best.get(key)
        if isinstance(value, str) and value and tw(value) != tw(parts[0]) and " or " not in value:
            parts.append(value)
            break
    label = "・".join(parts[:2])
    return {"name": label[:MAX_PLACE], "lat": round(float(best["latitude"]), 4), "lon": round(float(best["longitude"]), 4)}


def resolve_place(session, text: str) -> dict:
    """地點文字 → {"name", "lat", "lon"}；找不到丟 WeatherError（人話）。"""
    text = str(text or "").strip()[:MAX_PLACE]
    m = COORDS_RE.match(text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise WeatherError("座標超出範圍：緯度要在 -90～90、經度在 -180～180（例：25.03,121.56）。")
        return {"name": f"{lat:.2f},{lon:.2f}", "lat": lat, "lon": lon}
    county = county_of(text)
    short = tw(text) in (county, county[:-1]) or tw(text) in tuple(a for a, _n in COUNTY_ALIASES)
    if county and short:
        lat, lon = county_coords(county)
        return {"name": county, "lat": lat, "lon": lon}
    found = geocode(session, text)
    if found:
        return found
    if county:                                          # 「臺北市某某里」查不到細的，就用縣市
        lat, lon = county_coords(county)
        return {"name": county, "lat": lat, "lon": lon, "note": f"找不到「{text}」，先用{county}的天氣"}
    raise WeatherError(f"找不到「{text}」這個地方。可以試：縣市名（台北、高雄、花蓮）、英文地名（Taipei）、"
                       "或座標（25.03,121.56）。")


def place_for(session, user_store, text: str):
    """這次要查的地點：有寫就查寫的，沒寫用設定的預設地點；都沒有丟 WeatherError。"""
    text = str(text or "").strip()
    if text:
        return resolve_place(session, text)
    place = settings_of(user_store)["place"]
    if isinstance(place, dict) and fnum(place.get("lat")) is not None and fnum(place.get("lon")) is not None:
        return place
    raise WeatherError("要查哪裡？在指令後面寫地點，例如 /wx 台北；或先設預設地點：/wx_set 地點 台北")


# ── Open-Meteo ───────────────────────────────────────────────────────

def get_forecast(session, place: dict, days: int) -> dict:
    params = {
        "latitude": place["lat"], "longitude": place["lon"], "timezone": TZ, "forecast_days": max(1, min(days, MAX_DAYS)),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,"
                   "wind_speed_10m,wind_gusts_10m,uv_index,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,"
                 "precipitation_probability_max,precipitation_sum,uv_index_max,wind_speed_10m_max,wind_gusts_10m_max",
    }
    data = fetch_json(session, FORECAST_API, params, "天氣資料（Open-Meteo）")
    if not isinstance(data.get("current"), dict) or not isinstance(data.get("daily"), dict):
        raise WeatherError("天氣資料的格式看不懂（Open-Meteo 可能改了格式），等一下再試。")
    return data


def get_air(session, place: dict) -> dict:
    params = {"latitude": place["lat"], "longitude": place["lon"], "timezone": TZ,
              "current": "us_aqi,pm2_5,pm10,uv_index"}
    data = fetch_json(session, AIR_API, params, "空氣品質資料（Open-Meteo）")
    if not isinstance(data.get("current"), dict):
        raise WeatherError("空氣品質資料的格式看不懂（Open-Meteo 可能改了格式），等一下再試。")
    return data


def daily_rows(data: dict) -> list:
    """daily 的平行陣列 → 一天一個 dict。"""
    d = data.get("daily") or {}
    times = d.get("time") or []
    rows = []
    for i, t in enumerate(times):
        row = {"date": t}
        for key, values in d.items():
            if key != "time" and isinstance(values, list) and i < len(values):
                row[key] = values[i]
        rows.append(row)
    return rows


def day_label(iso: str, index: int) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    head = {0: "今天", 1: "明天", 2: "後天"}.get(index, "")
    text = f"{d.month}/{d.day}（{WEEK[d.weekday()]}）"
    return f"{head} {text}" if head else text


def uv_level(uv) -> str:
    v = fnum(uv)
    if v is None:
        return "不明"
    if v >= 11:
        return "危險"
    if v >= 8:
        return "過量"
    if v >= 6:
        return "高"
    if v >= 3:
        return "中"
    return "低"


def aqi_level(aqi):
    """(燈號, 等級, 建議)：照美國 EPA AQI 的分級（台灣環境部 AQI 也是同一套級距）。"""
    v = fnum(aqi)
    if v is None:
        return "⚪", "不明", ""
    if v <= 50:
        return "🟢", "良好", "適合戶外活動。"
    if v <= 100:
        return "🟡", "普通", "極少數特別敏感的人減少長時間戶外劇烈活動。"
    if v <= 150:
        return "🟠", "對敏感族群不健康", "有氣喘、心肺疾病的人、孩子、長輩減少戶外活動，出門可戴口罩。"
    if v <= 200:
        return "🔴", "對所有族群不健康", "大家都減少戶外活動，出門建議戴口罩。"
    if v <= 300:
        return "🟣", "非常不健康", "避免戶外活動，門窗關好。"
    return "🟤", "危害", "留在室內，避免任何戶外活動。"


# ── 穿衣建議（自己寫的規則） ─────────────────────────────────────────

def layers_for(t: float) -> str:
    if t >= 30:
        return "短袖＋透氣排汗的短褲或薄長褲，選淺色、寬鬆的"
    if t >= 26:
        return "短袖＋薄長褲或裙子"
    if t >= 22:
        return "短袖或薄長袖，早晚可以加一件薄外套"
    if t >= 18:
        return "長袖上衣＋一件薄外套（襯衫、薄夾克）"
    if t >= 14:
        return "長袖＋毛衣或針織衫，外面再一件夾克"
    if t >= 10:
        return "保暖內搭＋毛衣＋厚外套，可加圍巾"
    return "發熱衣＋毛衣＋羽絨外套或大衣，圍巾、手套、毛帽都帶"


def wear_advice(data: dict, feel: str = "普通", activity: str = "一般") -> dict:
    """依體感溫度、降雨機率、風、UV、日夜溫差、活動類型，回 {"wear": 穿什麼, "bring": [要帶的], "notes": [提醒], "basis": 依據}。"""
    cur = data.get("current") or {}
    days = daily_rows(data)
    today = days[0] if days else {}
    offset = feel_offset(feel)
    feels_now = fnum(cur.get("apparent_temperature"))
    if feels_now is None:
        feels_now = fnum(cur.get("temperature_2m"))
    fmin = fnum(today.get("apparent_temperature_min"))
    fmax = fnum(today.get("apparent_temperature_max"))
    tmin = fnum(today.get("temperature_2m_min"))
    tmax = fnum(today.get("temperature_2m_max"))
    rain = fnum(today.get("precipitation_probability_max"))
    rain_mm = fnum(today.get("precipitation_sum"))
    uv = fnum(today.get("uv_index_max"))
    if uv is None:
        uv = fnum(cur.get("uv_index"))
    gust = fnum(today.get("wind_gusts_10m_max"))
    if gust is None:
        gust = fnum(cur.get("wind_gusts_10m"))
    wind = fnum(today.get("wind_speed_10m_max"))
    if wind is None:
        wind = fnum(cur.get("wind_speed_10m"))
    humid = fnum(cur.get("relative_humidity_2m"))
    code = cur.get("weather_code")

    # 白天穿多少看「現在體感」與「今天最高體感」的平均；怕冷／怕熱加減幾度
    base_candidates = [v for v in (feels_now, fmax) if v is not None]
    if not base_candidates:
        raise WeatherError("這次的天氣資料缺溫度，沒辦法給穿衣建議。")
    base = sum(base_candidates) / len(base_candidates) + offset
    low = (fmin if fmin is not None else base - offset) + offset
    wear = layers_for(base)
    bring, notes = [], []

    swing = (tmax - tmin) if tmax is not None and tmin is not None else 0
    if swing >= 8 or base - low >= 6:
        notes.append(f"日夜溫差大（約 {num(swing)} 度）：洋蔥式穿法；早晚體感約 {num(fmin)}°C，那時要{layers_for(low)}。")
        bring.append("外套（早晚用）")
    elif low < 20 <= base:
        bring.append("薄外套（早晚涼）")

    rainy_code = isinstance(code, (int, float)) and int(code) in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99)
    if (rain is not None and rain >= 60) or (rain_mm is not None and rain_mm >= 5) or rainy_code:
        bring.append("雨傘（降雨機率高）")
        notes.append("穿不怕濕的鞋，褲管別太長；包包裡放一雙備用襪子。")
    elif rain is not None and rain >= 30:
        bring.append("折疊傘（可能會下雨）")

    if gust is not None and gust >= 50:
        notes.append(f"陣風很強（約 {num(gust)} km/h）：大傘容易開花，改穿有帽子的防風防水外套。")
        bring.append("防風外套")
    elif wind is not None and wind >= 30:
        notes.append(f"風偏大（約 {num(wind)} km/h）：體感會更涼，穿件防風的外層。")

    if uv is not None and uv >= 8:
        bring += ["防曬乳（SPF30 以上，出門前 15 分鐘擦）", "帽子或陽傘", "太陽眼鏡"]
        notes.append(f"UV 指數 {num(uv, 1)}（{uv_level(uv)}）：中午 10～14 點少曬太陽。")
    elif uv is not None and uv >= 6:
        bring += ["防曬乳", "帽子或陽傘"]
    elif uv is not None and uv >= 3 and activity in ("戶外", "運動", "旅遊"):
        bring.append("防曬乳（待在戶外比較久）")

    if humid is not None and humid >= 85 and base >= 26:
        notes.append("又濕又熱：選吸濕排汗、快乾的材質，多帶一條毛巾。")

    if activity == "上班":
        if base >= 26:
            bring.append("薄外套或針織衫（辦公室冷氣）")
        notes.append("通勤：穿好走的鞋；正式場合把外套帶著就好，到了再穿。")
    elif activity == "戶外":
        bring += ["水（每小時約 250～500ml）", "防蚊液"]
        notes.append("戶外：多帶一層衣服，山上每升高 100 公尺約降 0.6 度；選有防潑水的外層。")
    elif activity == "運動":
        wear = ("排汗運動衣＋運動褲；" + ("會冷的話先穿薄的長袖排汗衣或防風外套，熱身後再脫" if base < 18 else "選淺色透氣的"))
        bring += ["水或運動飲料", "一套乾的替換衣服（運動完容易著涼）"]
        if base >= 30:
            notes.append("很熱：避開中午運動，改清晨或傍晚，注意中暑。")
    elif activity == "旅遊":
        bring += ["好走的鞋", "輕便雨衣"]
        notes.append("旅遊：洋蔥式穿法最好調整；行程有室內有戶外的話多帶一件薄外套。")

    seen, unique = set(), []
    for item in bring:
        key = item.split("（")[0]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    basis = (f"體感 {num(feels_now)}°C、今天體感 {num(fmin)}～{num(fmax)}°C、降雨 {num(rain)}%、"
             f"UV {num(uv, 1)}、陣風 {num(gust)} km/h")
    if offset:
        basis += f"；你設定「{feel}」，門檻{'往下' if offset < 0 else '往上'}調 {abs(offset)} 度"
    return {"wear": wear, "bring": unique, "notes": notes, "basis": basis}


# ── 停班停課（人事總處網頁） ─────────────────────────────────────────

def unescape(text: str) -> str:
    def one(m):
        ent = m.group(1)
        if ent.startswith("#"):
            try:
                n = int(ent[2:], 16) if ent[1:2] in ("x", "X") else int(ent[1:])
                return chr(n) if 0 < n < 0x110000 else ""
            except ValueError:
                return m.group(0)
        for name, value in ENTITIES:
            if name == ent.lower():
                return value
        return m.group(0)
    return ENTITY_RE.sub(one, text)


def cell_text(fragment: str) -> str:
    text = BR_RE.sub("\n", fragment)
    text = TAG_RE.sub("", text)
    text = unescape(text).replace("　", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def parse_dgpa(page: str):
    """人事總處的停班停課頁 → {"date", "updated", "none", "rows": [(縣市, 狀態)]}；看不懂回 None。
    寬鬆：不靠 class／id，只找表格的列，第一格認得出是縣市的就算一列；整頁有「無停班停課訊息」就是沒有。"""
    if not isinstance(page, str) or not page.strip():
        return None
    body = DROP_RE.sub("", page)
    flat = cell_text(body)
    m = ROC_DATE_RE.search(flat)
    day = f"民國{m.group(1)}年{int(m.group(2))}月{int(m.group(3))}日" if m else ""
    m = UPDATED_RE.search(flat)
    updated = m.group(1).strip() if m else ""
    rows = []
    for chunk in ROW_SPLIT_RE.split(body)[1:]:
        end = ROW_END_RE.search(chunk)
        if end:
            chunk = chunk[:end.start()]
        cells = [cell_text(c) for c in CELL_SPLIT_RE.split(chunk)[1:]]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        county = county_of(cells[0].split("\n")[0])
        if not county:
            continue
        status = "\n".join(cells[1:]).strip()
        if status:
            rows.append((county, status[:600]))
    none = any(word in flat for word in NO_CLOSURE)
    if not rows and not none:
        return None
    if not day and not updated and "停止上班" not in flat and "停班停課" not in flat:
        return None
    return {"date": day, "updated": updated, "none": none and not rows, "rows": rows}


def fetch_dgpa(session) -> str:
    try:
        resp = session.get(DGPA_URL, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    except requests.RequestException:
        raise WeatherError(f"連不上人事行政總處的網站，等一下再試，或直接看 {DGPA_URL}")
    if getattr(resp, "status_code", 0) != 200:
        raise WeatherError(f"人事行政總處的網站回了錯誤（HTTP {getattr(resp, 'status_code', '?')}），"
                           f"請直接看 {DGPA_URL}")
    raw = getattr(resp, "content", b"") or b""
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("big5")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def status_icon(status: str) -> str:
    """🔴 停班或停課、🟠 部分地區或部分時段（同時有停止與照常）、🟢 照常、⚪ 看不出來。"""
    off = any(w in status for w in OFF_WORDS)
    if off and "照常" not in status:
        return "🔴"
    if off:
        return "🟠"
    if any(w in status for w in ON_WORDS):
        return "🟢"
    return "⚪"


def format_closures(info: dict, county: str = "") -> str:
    head = "🌀 天然災害停止上班及上課情形"
    when = "、".join(x for x in (info.get("date"), f"更新 {info['updated']}" if info.get("updated") else "") if x)
    lines = [head + (f"（{when}）" if when else "")]
    rows = info.get("rows") or []
    if county:
        mine = [(c, s) for c, s in rows if c == county]
        if mine:
            for c, s in mine:
                lines.append(f"{status_icon(s)} {c}：{s}")
        elif info.get("none"):
            lines.append(f"🟢 {county}：目前沒有停班停課訊息。")
        else:
            lines.append(f"🟢 {county}：清單裡沒有列出，表示目前照常上班上課（以縣市政府公告為準）。")
            others = [c for i, (c, s) in enumerate(rows) if any(w in s for w in OFF_WORDS)
                      and c not in [x for x, _y in rows[:i]]]           # 照網頁上的順序、不重複
            if others:
                lines.append("有停班或停課消息的縣市：" + "、".join(others))
    elif info.get("none") or not rows:
        lines.append("🟢 目前沒有任何縣市停班停課。")
    else:
        for c, s in rows:
            lines.append(f"{status_icon(s)} {c}：{s}")
    lines.append(f"資料：行政院人事行政總處 {DGPA_URL}（各縣市政府通報，以官方公告為準）")
    return "\n".join(lines)


# ── 回覆的樣子 ───────────────────────────────────────────────────────

def format_weather(place: dict, data: dict, days: int, feel: str, activity: str) -> str:
    cur = data.get("current") or {}
    icon, text = describe_code(cur.get("weather_code"))
    lines = []
    if place.get("note"):
        lines.append(f"ℹ️ {place['note']}")
    lines.append(f"{icon} {place['name']} 現在 {num(cur.get('temperature_2m'), 1)}°C"
                 f"（體感 {num(cur.get('apparent_temperature'), 1)}°C）{text}")
    lines.append(f"濕度 {num(cur.get('relative_humidity_2m'))}%・風 {num(cur.get('wind_speed_10m'))} km/h"
                 f"（陣風 {num(cur.get('wind_gusts_10m'))}）・UV {num(cur.get('uv_index'), 1)}")
    rows = daily_rows(data)[:days]
    if rows:
        lines.append("")
        lines.append(f"📅 未來 {len(rows)} 天")
        for i, row in enumerate(rows):
            d_icon, d_text = describe_code(row.get("weather_code"))
            lines.append(f"{day_label(row.get('date'), i)} {d_icon} {d_text} "
                         f"{num(row.get('temperature_2m_min'))}～{num(row.get('temperature_2m_max'))}°C"
                         f"・降雨 {num(row.get('precipitation_probability_max'))}%"
                         f"・UV {num(row.get('uv_index_max'), 1)}")
    try:
        advice = wear_advice(data, feel, activity)
        tip = f"👕 {advice['wear']}"
        if advice["bring"]:
            tip += "；帶：" + "、".join(b.split("（")[0] for b in advice["bring"][:4])
        lines += ["", tip, "（詳細：/wx_wear）"]
    except WeatherError:
        pass
    lines.append("資料：Open-Meteo（模式預報，僅供參考）")
    return "\n".join(lines)


def format_wear(place: dict, data: dict, feel: str, activity: str) -> str:
    advice = wear_advice(data, feel, activity)
    cur = data.get("current") or {}
    icon, text = describe_code(cur.get("weather_code"))
    lines = []
    if place.get("note"):
        lines.append(f"ℹ️ {place['note']}")
    lines.append(f"👕 {place['name']} 今天穿什麼（{activity}）")
    lines.append(f"{icon} 現在 {text}；{advice['basis']}")
    lines.append("")
    lines.append(f"穿：{advice['wear']}")
    if advice["bring"]:
        lines.append("帶：" + "、".join(advice["bring"]))
    for note in advice["notes"]:
        lines.append(f"・{note}")
    lines.append("")
    lines.append("（規則是自己寫的，alpha：門檻不合你就 /wx_set 體感 怕冷 或 怕熱）")
    return "\n".join(lines)


def format_air(place: dict, air: dict, forecast) -> str:
    cur = air.get("current") or {}
    light, level, tip = aqi_level(cur.get("us_aqi"))
    lines = [f"🌬️ {place['name']} 空氣品質與紫外線"]
    if place.get("note"):
        lines.insert(0, f"ℹ️ {place['note']}")
    lines.append(f"{light} AQI {num(cur.get('us_aqi'))}（{level}）・PM2.5 {num(cur.get('pm2_5'), 1)} μg/m³"
                 f"・PM10 {num(cur.get('pm10'), 1)} μg/m³")
    if tip:
        lines.append(f"・{tip}")
    uv_now = cur.get("uv_index")
    uv_max = None
    if isinstance(forecast, dict):
        rows = daily_rows(forecast)
        if rows:
            uv_max = rows[0].get("uv_index_max")
        if uv_now is None:
            uv_now = (forecast.get("current") or {}).get("uv_index")
    line = f"☀️ UV 現在 {num(uv_now, 1)}（{uv_level(uv_now)}）"
    if uv_max is not None:
        line += f"・今天最高 {num(uv_max, 1)}（{uv_level(uv_max)}）"
    lines.append(line)
    peak = fnum(uv_max if uv_max is not None else uv_now)
    if peak is not None and peak >= 8:
        lines.append("・紫外線過量以上：擦防曬、戴帽子和太陽眼鏡，10～14 點少曬。")
    elif peak is not None and peak >= 6:
        lines.append("・紫外線高：出門擦防曬、撐陽傘或戴帽子。")
    lines.append("資料：Open-Meteo Air Quality（模式估計值，不是環境部測站實測；以環境部為準）")
    return "\n".join(lines)


def format_settings(s: dict) -> str:
    place = s["place"]
    place_text = place["name"] if isinstance(place, dict) and place.get("name") else "（還沒設）"
    lines = ["⚙️ 天氣與穿衣：目前設定",
             f"・地點：{place_text}",
             f"・體感：{s['feel']}",
             f"・活動：{s['activity']}",
             f"・縣市：{s['county'] or '（還沒設：停班停課會列全部縣市）'}",
             f"・天數：{s['days']}",
             "",
             "改設定：/wx_set <鍵> <值>，例如"]
    lines += ["  /wx_set 地點 台北", "  /wx_set 體感 怕冷", "  /wx_set 活動 上班", "  /wx_set 縣市 高雄", "  /wx_set 天數 5",
              "清掉一項：/wx_set 地點 清除"]
    return "\n".join(lines)


def split_days(text: str, default: int):
    """「台北 5」「台北 5天」→ ("台北", 5)；沒寫天數回 (原字, default)。"""
    text = str(text or "").strip()
    m = TRAILING_DAYS_RE.match(text)
    if m and (not m.group(1) or not m.group(1).strip()[-1:].isdigit()):
        n = int(m.group(2))
        if 1 <= n <= MAX_DAYS and not COORDS_RE.match(text):
            return m.group(1).strip(), n
    return text, default


def split_activity(text: str):
    """「運動 台北」「台北 上班」→ ("運動", "台北")；沒有活動字回 ("", 原字)。"""
    tokens = str(text or "").split()
    for i, tok in enumerate(tokens):
        name = pick(ACTIVITIES, tok)
        if name:
            return name, " ".join(tokens[:i] + tokens[i + 1:])
    return "", str(text or "").strip()


# ── 指令 ─────────────────────────────────────────────────────────────

def install(app, pack):
    g = pack.group

    def store(user):
        return pack.store.of(user)

    def reply_error(api, chat_id, err):
        api.send_message(chat_id, f"⚠️ {err}")

    def now_cmd(api, chat_id, user, values):
        s = settings_of(store(user))
        text, days = split_days(values.get("place", ""), int(s["days"] or DEFAULT_DAYS))
        if values.get("days"):
            try:
                days = max(1, min(int(str(values["days"]).strip().rstrip("天")), MAX_DAYS))
            except ValueError:
                api.send_message(chat_id, f"天數要是 1～{MAX_DAYS} 的數字。")
                return
        session = new_session()
        try:
            place = place_for(session, store(user), text)
            data = get_forecast(session, place, days)
        except WeatherError as err:
            reply_error(api, chat_id, err)
            return
        api.send_message(chat_id, format_weather(place, data, days, s["feel"], s["activity"]))

    def wear_cmd(api, chat_id, user, values):
        s = settings_of(store(user))
        activity, text = split_activity(values.get("place", ""))
        if values.get("activity"):
            activity = pick(ACTIVITIES, values["activity"])
            if not activity:
                api.send_message(chat_id, "活動類型只認：一般、上班、戶外、運動、旅遊。")
                return
        activity = activity or s["activity"] or "一般"
        session = new_session()
        try:
            place = place_for(session, store(user), text)
            data = get_forecast(session, place, 1)
            msg = format_wear(place, data, s["feel"], activity)
        except WeatherError as err:
            reply_error(api, chat_id, err)
            return
        api.send_message(chat_id, msg)

    def air_cmd(api, chat_id, user, values):
        session = new_session()
        try:
            place = place_for(session, store(user), values.get("place", ""))
            air = get_air(session, place)
        except WeatherError as err:
            reply_error(api, chat_id, err)
            return
        try:
            forecast = get_forecast(session, place, 1)
        except WeatherError:
            forecast = None                              # UV 最高值拿不到也照樣回空氣品質
        api.send_message(chat_id, format_air(place, air, forecast))

    def typhoon_cmd(api, chat_id, user, values):
        asked = str(values.get("county", "") or "").strip()
        if asked in ("全部", "all", "全台", "全臺"):
            county = ""
        elif asked:
            county = county_of(asked)
            if not county:
                api.send_message(chat_id, f"看不出「{asked}」是哪個縣市。請寫縣市名，例如 台北、新北、高雄、花蓮；"
                                          "或打「全部」看全部縣市。")
                return
        else:
            county = settings_of(store(user))["county"]
        try:
            page = fetch_dgpa(new_session())
        except WeatherError as err:
            reply_error(api, chat_id, err)
            return
        info = parse_dgpa(page)
        if info is None:
            api.send_message(chat_id, "⚠️ 人事行政總處的網頁格式好像改了，這次看不懂（工具還是 alpha）。"
                                      f"請直接看：{DGPA_URL}")
            return
        api.send_message(chat_id, format_closures(info, county))

    def settings_cmd(api, chat_id, user, values):
        api.send_message(chat_id, format_settings(settings_of(store(user))))

    def set_cmd(api, chat_id, user, values):
        key = pick(SETTING_KEYS, values.get("key", ""))
        value = str(values.get("value", "") or "").strip()
        if not key:
            keys = "\n".join(f"・{name}：{desc}" for name, _w, desc in SETTING_KEYS)
            api.send_message(chat_id, f"看不懂「{values.get('key', '')}」這個設定。可以改的有：\n{keys}\n"
                                      "例：/wx_set 地點 台北")
            return
        clear = value.lower() in CLEAR_WORDS
        if key == "地點":
            if clear:
                new = None
            else:
                try:
                    new = resolve_place(new_session(), value)
                except WeatherError as err:
                    reply_error(api, chat_id, err)
                    return
                new = {"name": new["name"], "lat": new["lat"], "lon": new["lon"]}
            field = "place"
        elif key == "體感":
            new = "普通" if clear else pick(FEELS, value)
            if not new:
                api.send_message(chat_id, "體感只認：怕冷、普通、怕熱。")
                return
            field = "feel"
        elif key == "活動":
            new = "一般" if clear else pick(ACTIVITIES, value)
            if not new:
                api.send_message(chat_id, "活動只認：一般、上班、戶外、運動、旅遊。")
                return
            field = "activity"
        elif key == "縣市":
            new = "" if clear else county_of(value)
            if new == "" and not clear:
                api.send_message(chat_id, f"看不出「{value}」是哪個縣市。例：台北、新北、桃園、高雄、花蓮、金門。")
                return
            field = "county"
        else:
            if clear:
                new = DEFAULT_DAYS
            else:
                try:
                    new = int(value.rstrip("天"))
                except ValueError:
                    new = 0
                if not 1 <= new <= MAX_DAYS:
                    api.send_message(chat_id, f"天數要是 1～{MAX_DAYS} 的數字。")
                    return
            field = "days"

        def write(d):
            if not isinstance(d, dict):
                d = empty()
            d[field] = new
            return d

        store(user).update(write, empty())
        shown = (new["name"] if isinstance(new, dict) else new) if new not in (None, "") else "（清掉了）"
        api.send_message(chat_id, f"✅ 設好了：{key} → {shown}\n看全部設定：/wx_settings")

    def settings_route(user):
        s = settings_of(store(user))
        place = s["place"]
        return {"place": place.get("name") if isinstance(place, dict) else "",
                "feel": s["feel"], "activity": s["activity"], "county": s["county"], "days": s["days"]}

    kit.command(g, "now", now_cmd, "現在天氣＋未來幾天預報（不寫地點＝預設地點）", default=True, effect=READ,
                fields=[Field("place", "地點（縣市、地名或「緯度,經度」；不寫＝預設地點）"),
                        Field("days", f"預報天數（1～{MAX_DAYS}；不寫＝設定的天數）")], positional=1)
    kit.command(g, "wear", wear_cmd, "穿衣建議：依體感、降雨、風、UV、溫差和活動類型（上班／戶外／運動／旅遊）", effect=READ,
                fields=[Field("place", "地點（可以在前面加活動：運動 台北；不寫＝預設地點）"),
                        Field("activity", "活動類型：一般／上班／戶外／運動／旅遊（不寫＝設定的活動）")], positional=1)
    kit.command(g, "air", air_cmd, "空氣品質（AQI、PM2.5）和紫外線", effect=READ,
                fields=[Field("place", "地點（不寫＝預設地點）")])
    kit.command(g, "typhoon", typhoon_cmd, "颱風假：各縣市停班停課（人事行政總處公告）", effect=READ,
                fields=[Field("county", "縣市（不寫＝設定的縣市；寫「全部」看全部）")])
    kit.command(g, "settings", settings_cmd, "看目前設定（預設地點、體感、活動、縣市、天數）", effect=READ)
    kit.command(g, "set", set_cmd, "改一項設定：地點／體感／活動／縣市／天數", effect=WRITE,
                fields=[Field("key", "要改哪一項（地點、體感、活動、縣市、天數）", required=True),
                        Field("value", "新的值（寫「清除」＝回到預設）", required=True)])
    pack.web_route("settings", settings_route)
