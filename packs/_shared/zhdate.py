"""
zhdate：把「明天 14:00 看牙醫」「下週一下午三點半 開會」「11/12 繳費」拆成 日期／時間／剩下的字。

這裡是**正本**。SYNOX 的 loader 只執行 pack 資料夾裡的 pack.py（不把資料夾放進 sys.path），
pack 之間也不能互相 import，所以每個用到它的 pack.py 裡都有一份一模一樣的副本，夾在
`# >>> zhdate` 與 `# <<< zhdate` 兩行之間。改這裡之後跑 `python scripts/sync_shared.py`，
`tests/test_shared_sync.py` 會檢查每份副本都跟正本一樣。

純函式：不碰時鐘（today 由呼叫的人給）、不碰檔案、不連網。
"""

# >>> zhdate（正本 packs/_shared/zhdate.py；不要直接改這段，改正本再跑 scripts/sync_shared.py）
import re as _re
from dataclasses import dataclass as _dataclass
from datetime import date as _date, timedelta as _timedelta

WEEK_NAMES = "一二三四五六日"
_CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5,
       "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"(?:\d{1,2}|[零〇一二兩两三四五六七八九十]{1,3})"
_WD = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
       "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}
_PERIOD = r"(早上|上午|中午|下午|晚上|傍晚|凌晨|半夜|早|晚)?"
_DATE_PATTERNS = (
    ("ymd", _re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")),
    ("md", _re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")),
    ("md_cn", _re.compile(r"(" + _NUM + r")月(" + _NUM + r")[日號号]?")),
    ("rel", _re.compile(r"(大後天|大后天|後天|后天|明天|明日|明晚|明早|今天|今日|今晚|今早)")),
    ("week", _re.compile(r"(下下|下|這|这|本)?(?:個|个)?(?:週|周|星期|禮拜|礼拜)([一二三四五六日天1-7])")),
    ("after", _re.compile(r"(" + _NUM + r")\s*(天|週|周|個禮拜|个礼拜)[後后]")),
    ("day", _re.compile(r"(?<![\d月/])(\d{1,2})[號号]")),
)
_TIME_HM = _re.compile(_PERIOD + r"\s*(\d{1,2}):(\d{2})(?!\d)")
_TIME_CN = _re.compile(_PERIOD + r"\s*(" + _NUM + r")\s*[點点](?![點点])\s*(半|(" + _NUM + r")\s*分?)?")
_TIME_AMPM = _re.compile(r"(?<![\dA-Za-z:])(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?(?![a-z])", _re.IGNORECASE)
_NOON = _re.compile(r"中午")
_RANGE_SEP = _re.compile(r"^\s*(?:-|~|～|－|–|到|至)\s*")
_EVENING_WORDS = ("今晚", "明晚")


def cn_int(s: str) -> int:
    """'14'→14、'三'→3、'十二'→12、'二十三'→23。看不懂丟 ValueError。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        if (a and a not in _CN) or (b and b not in _CN):
            raise ValueError(s)
        return (_CN[a] if a else 1) * 10 + (_CN[b] if b else 0)
    if len(s) == 1 and s in _CN:
        return _CN[s]
    raise ValueError(s)


@_dataclass
class When:
    """parse_when 的結果。day：日期（沒看到＝None）；start／end：'HH:MM'（沒看到＝None）；
    rest：拿掉日期、時間之後剩下的字；error：看到了但不是真的日期／時間。"""
    day: object = None
    start: object = None
    end: object = None
    rest: str = ""
    error: str = ""

    @property
    def all_day(self) -> bool:
        return self.start is None


def _safe_date(y: int, m: int, d: int):
    try:
        return _date(y, m, d)
    except ValueError:
        return None


def _roll_year(today, m: int, d: int):
    """月／日沒寫年：今年的那一天已經過了就算明年的。"""
    found = _safe_date(today.year, m, d)
    if found is None:
        return None
    if found < today:
        found = _safe_date(today.year + 1, m, d)
    return found


def _date_from(kind: str, m, today):
    """一個日期 match → date；不是真的日期回 None。"""
    g = m.groups()
    if kind == "ymd":
        return _safe_date(int(g[0]), int(g[1]), int(g[2]))
    if kind in ("md", "md_cn"):
        try:
            return _roll_year(today, cn_int(g[0]), cn_int(g[1]))
        except ValueError:
            return None
    if kind == "rel":
        word = g[0]
        offset = 3 if word.startswith("大") else 2 if word[0] in "後后" else 1 if word[0] == "明" else 0
        return today + _timedelta(days=offset)
    if kind == "week":
        prefix, wd = g[0] or "", _WD[g[1]]
        monday = today - _timedelta(days=today.weekday())
        if prefix == "下下":
            return monday + _timedelta(days=14 + wd)
        if prefix == "下":
            return monday + _timedelta(days=7 + wd)
        if prefix:                                   # 這週、本週
            return monday + _timedelta(days=wd)
        return today + _timedelta(days=(wd - today.weekday()) % 7)   # 沒寫：最近的那一天（今天也算）
    if kind == "after":
        try:
            n = cn_int(g[0])
        except ValueError:
            return None
        return today + _timedelta(days=n if g[1] == "天" else 7 * n)
    if kind == "day":
        d = int(g[0])
        found = _safe_date(today.year, today.month, d)
        if found is not None and found >= today:
            return found
        y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return _safe_date(y, mo, d)
    return None


def _standalone(text: str, m) -> bool:
    """前後是空白或句子頭尾（『今天的事 今天』裡後面那個才是日期）。"""
    return (m.start() == 0 or text[m.start() - 1].isspace()) and (m.end() == len(text) or text[m.end()].isspace())


def _first_date(text: str, today):
    """日期寫法 → (kind, match)：前後有空白的優先，其次最前面的；同一個位置取最長的。"""
    best = None
    for kind, pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            key = (0 if _standalone(text, m) else 1, m.start(), -(m.end() - m.start()))
            if best is None or key < best[0]:
                best = (key, kind, m)
    return (best[1], best[2]) if best else (None, None)


def _apply_period(period: str, hour: int, evening: bool) -> int:
    if period in ("下午", "晚上", "傍晚", "晚") or (evening and not period):
        return hour + 12 if hour < 12 else hour
    if period == "中午":
        return hour + 12 if hour < 3 else hour
    if period in ("凌晨", "半夜") and hour == 12:
        return 0
    return hour


def _match_time(text: str, evening: bool, pos_only: bool = False):
    """第一個時間寫法 → (start, end, (開始, 結束) 位置) 或 None。pos_only：只認從開頭開始的（區間的後半）。"""
    found = []
    for pattern, kind in ((_TIME_HM, "hm"), (_TIME_CN, "cn"), (_TIME_AMPM, "ampm")):
        m = pattern.match(text) if pos_only else pattern.search(text)
        if m:
            found.append((m.start(), kind, m))
    if not found:
        return None
    found.sort(key=lambda t: (t[0], -(t[2].end() - t[2].start())))     # 同一個位置取最長的（2:30pm 不是 2:30）
    _pos, kind, m = found[0]
    try:
        if kind == "hm":
            period, hour, minute = m.group(1) or "", int(m.group(2)), int(m.group(3))
            hour = _apply_period(period, hour, evening)
        elif kind == "cn":
            period, hour = m.group(1) or "", cn_int(m.group(2))
            half, mins = m.group(3), m.group(4)
            minute = 30 if half == "半" else (cn_int(mins) if mins else 0)
            hour = _apply_period(period, hour, evening)
        else:
            hour, minute = int(m.group(1)), int(m.group(2) or 0)
            if hour > 12:
                return "bad", None, (m.start(), m.end())
            hour = (hour % 12) + (12 if m.group(3).lower() == "p" else 0)
    except ValueError:
        return "bad", None, (m.start(), m.end())
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return "bad", None, (m.start(), m.end())
    return f"{hour:02d}:{minute:02d}", m.group(1) if kind != "ampm" else "", (m.start(), m.end())


def _cut(text: str, start: int, end: int) -> str:
    return text[:start] + " " + text[end:]


def _tidy(text: str) -> str:
    text = _re.sub(r"\s+", " ", text).strip(" ，,、：:")
    if text.startswith("的"):                     # 「明天的會議」拿掉明天 → 「的會議」
        text = text[1:]
    return text.strip(" ，,、：:")


def parse_when(text: str, today) -> When:
    """在一句話裡找一個日期、一個時間（或時間區間），拿掉之後剩下的當標題。

    日期：2026-11-12、2026/11/12、11/12、11月12日、今天、明天、後天、大後天、今晚、明早、
         週五／星期五／禮拜五（最近的那一天，今天也算）、這週五、下週一、下下週三、3天後、2週後、15號。
    時間：14:00、14:00-15:30、下午2點、下午兩點半、晚上8點15分、9am、2:30pm、中午（12:00）。
    沒寫年的日期已經過了就算明年；『15號』過了就算下個月。"""
    w = When(rest=text or "")
    rest = w.rest
    kind, m = _first_date(rest, today)
    evening = False
    if m is not None:
        day = _date_from(kind, m, today)
        if day is None:
            w.error = f"「{m.group(0)}」不是真的日期"
            return w
        w.day = day
        evening = m.group(0) in _EVENING_WORDS
        rest = _cut(rest, m.start(), m.end())
    hit = _match_time(rest, evening)
    if hit is not None:
        start, period, (a, b) = hit
        if start == "bad":
            w.error = f"「{rest[a:b].strip()}」不是真的時間"
            return w
        w.start = start
        tail = rest[b:]
        sep = _RANGE_SEP.match(tail)
        if sep:
            second = _match_time(tail[sep.end():], evening or period in ("下午", "晚上", "傍晚", "晚"), pos_only=True)
            if second is not None and second[0] != "bad":
                end = second[0]
                if end <= start and int(end[:2]) < 12:            # 「下午2點到4點」：後半沒寫下午
                    end = f"{int(end[:2]) + 12:02d}{end[2:]}"
                if end > start:
                    w.end = end
                    b = b + sep.end() + second[2][1]
        rest = _cut(rest, a, b)
    elif _NOON.search(rest):
        noon = _NOON.search(rest)
        w.start = "12:00"
        rest = _cut(rest, noon.start(), noon.end())
    w.rest = _tidy(rest)
    return w


def check_ymd(value: str):
    """Field(check=…)：YYYY-MM-DD。回錯誤訊息或 None。"""
    m = _re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value or "")
    if not m or _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3))) is None:
        return "要是 YYYY-MM-DD（例如 2026-11-12）"
    return None


def day_label(day, today) -> str:
    """'今天 11/09（一）'、'明天 11/10（二）'、'11/14（六）'；不同年加年份。"""
    names = {0: "今天", 1: "明天", 2: "後天", -1: "昨天"}
    diff = (day - today).days
    base = f"{day.month:02d}/{day.day:02d}（{WEEK_NAMES[day.weekday()]}）"
    if day.year != today.year:
        base = f"{day.year}/" + base
    return f"{names[diff]} {base}" if diff in names else base
# <<< zhdate
