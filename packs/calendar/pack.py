"""📅 行事曆：/cal <時間 標題>、/cal_today、/cal_week、/cal_list [n]、/cal_rm <編號>、/cal_ics。

資料經 pack.store.of(user)（保管箱，密文；個人版 data/calendar.json.enc，伺服器版每人一份）：
    {"events": [{id, title, day: "YYYY-MM-DD", start: "HH:MM"|"", end: "HH:MM"|"", all_day, location, note, at}],
     "next_id": 1}
編號（id）是固定的：/cal_today、/cal_week、/cal_list 看到的 [3] 就是 /cal_rm 3。

不連網、不開別的程式。唯一碰檔案的是 /cal_ics：在 pack.path()（data/packs/calendar/）寫一個暫存的 .ics、
送出去之後馬上刪掉（行事曆內容不留明文在硬碟上）；所以 pack.json 宣告了 filesystem:own。
提醒：SYNOX 沒有給 pack 用的排程 API，加行程時回一行 /remind_add 給使用者自己決定要不要設。
"""

import os
import time
from datetime import date, datetime, timedelta

from isdtelekit.core.registry import WRITE, Field

from synox.packs import kit


def now() -> datetime:
    """現在時間（測試換掉這一個）。"""
    return datetime.now()


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

def empty() -> dict:
    """新的空資料（每次都是新的 dict 和 list：拿模組層級的常數做 dict(...) 只是淺拷貝，第一次寫入會改到共用的 list，
    伺服器版就會把前一個人的第一筆帶進下一個人的檔案）。"""
    return {"events": [], "next_id": 1}
DEFAULT_MINUTES = 60
_TIME_RANGE = _re.compile(r"^(\d{1,2}):(\d{2})(?:\s*[-~～]\s*(\d{1,2}):(\d{2}))?$")


def check_time(value: str):
    """Field(check=…)：HH:MM 或 HH:MM-HH:MM。"""
    m = _TIME_RANGE.match(value.strip())
    if not m:
        return "要是 HH:MM 或 HH:MM-HH:MM（例如 14:00-15:30）"
    nums = [int(x) for x in m.groups() if x is not None]
    if any(h > 23 for h in nums[0::2]) or any(mm > 59 for mm in nums[1::2]):
        return "不是真的時間"
    if len(nums) == 4 and (nums[2], nums[3]) <= (nums[0], nums[1]):
        return "結束要比開始晚"
    return None


def plus_minutes(hhmm: str, minutes: int) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:])
    total = min(h * 60 + m + minutes, 23 * 60 + 59)
    return f"{total // 60:02d}:{total % 60:02d}"


def sort_key(e: dict):
    return (e["day"], e.get("start") or "", e["id"])


def line(e: dict, today: date, with_day: bool = True) -> str:
    """'[3] 明天 11/10（二） 14:00–15:00 看牙醫 @診所'"""
    parts = [f"[{e['id']}]"]
    if with_day:
        parts.append(day_label(date.fromisoformat(e["day"]), today))
    if e.get("all_day") or not e.get("start"):
        parts.append("整天")
    else:
        parts.append(e["start"] + (f"–{e['end']}" if e.get("end") else ""))
    parts.append(e["title"])
    if e.get("location"):
        parts.append(f"@{e['location']}")
    return " ".join(parts)


# ── iCalendar（RFC 5545）──────────────────────────────────────────────

def ics_escape(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def ics_fold(content_line: str) -> str:
    """一行超過 75 個位元組就折行（續行開頭一個空白），不把一個中文字切成兩半。"""
    out, current, size = [], "", 0
    for ch in content_line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74
        if size + n > limit:
            out.append(current)
            current, size = "", 0
        current += ch
        size += n
    out.append(current)
    return "\r\n ".join(out)


def to_ics(events, stamp: datetime) -> str:
    """整本行事曆 → .ics 文字。時間用「浮動時間」（沒有時區＝照匯入那一邊的當地時間）。"""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SYNOX//calendar pack 0.1//ZH-TW", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:SYNOX 行事曆"]
    dtstamp = stamp.strftime("%Y%m%dT%H%M%S")
    for e in sorted(events, key=sort_key):
        day = date.fromisoformat(e["day"])
        lines += ["BEGIN:VEVENT", f"UID:synox-cal-{e['id']}-{e.get('at', 0)}@synox.local", f"DTSTAMP:{dtstamp}"]
        if e.get("all_day") or not e.get("start"):
            lines.append(f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}")
        else:
            start = e["start"].replace(":", "")
            end = (e.get("end") or plus_minutes(e["start"], DEFAULT_MINUTES)).replace(":", "")
            lines.append(f"DTSTART:{day.strftime('%Y%m%d')}T{start}00")
            lines.append(f"DTEND:{day.strftime('%Y%m%d')}T{end}00")
        lines.append("SUMMARY:" + ics_escape(e["title"]))
        if e.get("location"):
            lines.append("LOCATION:" + ics_escape(e["location"]))
        if e.get("note"):
            lines.append("DESCRIPTION:" + ics_escape(e["note"]))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(ics_fold(x) + "\r\n" for x in lines)


def _write_own(path: str, text: str) -> None:
    """pack 唯一寫檔的地方：path 一定是 pack.path(...) 給的（data/packs/calendar/ 底下）。"""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def install(app, pack):
    g = pack.group

    def events(user):
        return list((pack.store.of(user).load(None) or {}).get("events", []))

    def add(api, chat_id, user, values):
        today = now().date()
        text = values["text"].strip()
        if values.get("date"):
            day = date.fromisoformat(values["date"])
            start = end = None
            if values.get("time"):
                m = _TIME_RANGE.match(values["time"].strip())
                start = f"{int(m.group(1)):02d}:{m.group(2)}"
                end = f"{int(m.group(3)):02d}:{m.group(4)}" if m.group(3) else None
            title = text
        else:
            w = parse_when(text, today)
            if w.error:
                api.send_message(chat_id, f"❌ {w.error}。")
                return
            if w.day is None and w.start is None:
                api.send_message(chat_id, "❌ 看不出是哪一天。例：/cal 明天 14:00 看牙醫、/cal 11/20 整天 員工旅遊")
                return
            day, start, end, title = w.day or today, w.start, w.end, w.rest
        title = title.replace("整天", "").strip() if start is None else title
        if not title:
            api.send_message(chat_id, "❌ 要有標題。例：/cal 明天 14:00 看牙醫")
            return
        if start and not end:
            end = plus_minutes(start, DEFAULT_MINUTES)
        event = {"title": title, "day": day.isoformat(), "start": start or "", "end": end or "",
                 "all_day": start is None, "location": values.get("location", "").strip(),
                 "note": values.get("note", "").strip(), "at": int(time.time())}

        def append(d):
            d.setdefault("events", [])
            event["id"] = int(d.get("next_id") or 1)
            d["next_id"] = event["id"] + 1
            d["events"].append(event)
            return event["id"]

        pack.store.of(user).update(append, empty())
        lines = ["📅 加好了：" + line(event, today)]
        if day < today:
            lines.append("（這一天已經過了，當作紀錄。）")
        elif start:
            lines.append(f"要提醒的話：/remind_add {plus_minutes(start, -30) if start >= '00:30' else start} "
                         f"{title} date={day.isoformat()}")
        api.send_message(chat_id, "\n".join(lines))

    def show(api, chat_id, header, rows, today, empty, with_day=True):
        if not rows:
            api.send_message(chat_id, f"{header}\n{empty}")
            return
        api.send_message(chat_id, "\n".join([header] + [line(e, today, with_day) for e in rows]))

    def today_(api, chat_id, user, values):
        today = now().date()
        rows = sorted((e for e in events(user) if e["day"] == today.isoformat()), key=sort_key)
        show(api, chat_id, f"📅 {day_label(today, today)}", rows, today, "今天沒有行程。", with_day=False)

    def week(api, chat_id, user, values):
        today = now().date()
        last = today + timedelta(days=6)
        rows = sorted((e for e in events(user) if today.isoformat() <= e["day"] <= last.isoformat()), key=sort_key)
        show(api, chat_id, f"📅 這 7 天（{today.month}/{today.day}–{last.month}/{last.day}）", rows, today,
             "這 7 天沒有行程。")

    def list_(api, chat_id, user, values):
        today = now().date()
        n = int(values.get("n") or 10)
        rows = sorted((e for e in events(user) if e["day"] >= today.isoformat()), key=sort_key)[:n]
        show(api, chat_id, f"📅 接下來 {len(rows)} 個行程", rows, today, "接下來沒有行程。用 /cal 明天 14:00 看牙醫 加一個。")

    def rm(api, chat_id, user, values):
        n = int(values["n"])

        def drop(d):
            lst = d.setdefault("events", [])
            for i, e in enumerate(lst):
                if e["id"] == n:
                    return lst.pop(i)
            return None

        gone = pack.store.of(user).update(drop, empty())
        if gone is None:
            api.send_message(chat_id, f"❌ 沒有 [{n}] 這個行程（/cal_list 看編號）。")
            return
        api.send_message(chat_id, "🗑 刪掉了：" + line(gone, now().date()))

    def ics(api, chat_id, user, values):
        rows = events(user)
        if not rows:
            api.send_message(chat_id, "📅 行事曆是空的，沒有東西可以匯出。")
            return
        tg_id = getattr(user, "tg_id", None)
        path = pack.path(f"export-{tg_id if isinstance(tg_id, int) else 'me'}.ics")
        _write_own(path, to_ics(rows, now()))
        try:
            api.send_document(chat_id, path, caption=f"📅 {len(rows)} 個行程。在 Google 日曆（設定 → 匯入）或 "
                                                     f"iPhone（點開檔案 → 加入行事曆）匯入。")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    kit.command(g, "add", add, "加一個行程（明天 14:00 看牙醫）", default=True, effect=WRITE, positional=1,
                usage="<時間 標題>（例：明天 14:00 看牙醫） [location=…] [note=…]",
                fields=[Field("text", "時間和標題（AI 給了 date 就只放標題）", required=True),
                        Field("date", "日期 YYYY-MM-DD（AI 用；人直接寫在句子裡）", check=check_ymd),
                        Field("time", "時間 HH:MM 或 HH:MM-HH:MM（沒有＝整天）", check=check_time),
                        Field("location", "地點"),
                        Field("note", "備註")])
    kit.command(g, "today", today_, "今天的行程")
    kit.command(g, "week", week, "這 7 天的行程")
    kit.command(g, "list", list_, "接下來的行程（預設 10 個）", fields=[Field("n", "幾個", check=kit.positive_int)])
    kit.command(g, "rm", rm, "刪掉一個行程（編號看 /cal_list）", effect=WRITE,
                fields=[Field("n", "編號", required=True, check=kit.positive_int)])
    kit.command(g, "ics", ics, "整本行事曆匯出成 .ics 檔（給 Google／Apple 日曆匯入）")

    def today_route(user):
        """Web UI 面板（panel.html）：今天與接下來 7 天。只回這個人自己的。"""
        today = now().date()
        last = today + timedelta(days=6)
        rows = sorted((e for e in events(user) if today.isoformat() <= e["day"] <= last.isoformat()), key=sort_key)
        return {"today": today.isoformat(), "label": day_label(today, today),
                "events": [{"id": e["id"], "day": e["day"], "label": day_label(date.fromisoformat(e["day"]), today),
                            "time": "整天" if e.get("all_day") or not e.get("start")
                            else e["start"] + (f"–{e['end']}" if e.get("end") else ""),
                            "title": e["title"], "location": e.get("location", "")} for e in rows]}

    if hasattr(pack, "web_route"):
        pack.web_route("today", today_route)
