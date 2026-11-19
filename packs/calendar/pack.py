"""📅 行事曆（0.2.0）：行程（有開始與結束、每週重複、撞時間提醒）、月曆總覽、今日清單與「今天最重要的一件事」，附完整網頁畫面。

指令：/cal <時間 標題>、/cal_today、/cal_week、/cal_list [n]、/cal_month [YYYY-MM]、/cal_edit、/cal_status、/cal_keep、/cal_clash、
/cal_rm <編號>、/cal_ics，今日清單 /cal_task、/cal_tasks、/cal_task_edit、/cal_done、/cal_task_rm、/cal_focus。

    指令      kit.command(group, name, …)                 人打字、AI 走這裡
    畫面動作  pack.app_action(name, …)                    同名、同危險程度；欄位檢查、權限、稽核照那個指令
    畫面讀    pack.app_read(name, …)
    匯出      pack.app_export("ics", …)
    小面板    pack.web_route("today", …)                  （pack.json web.api）

這個檔要以套件身分載入（pack.json python.package=true）：engine/（純邏輯）、bridge/（服務層、文字）用相對 import。
資料經 pack.store.of(user)（保管箱，密文；個人版 data/calendar.json.enc，伺服器版每人一份），形狀見 engine/store.py；
0.1.2 的資料（行程只有 day／start／end）每次讀都先升級（只在記憶體），下一次寫入才存成新格式，編號不變。
唯一碰檔案的是 /cal_ics（Telegram）：在 pack.path_of(user, …)（這個人自己的資料夾）寫一個暫存的 .ics、送出去就刪掉
（行事曆內容不留明文在硬碟上），所以 pack.json 宣告了 filesystem:own。畫面的匯出不經檔案（直接回位元組）。
檔名每次不一樣（export-<時間>-<亂數>.ics）；每次匯出前先清掉這個人資料夾裡留下的舊匯出檔（10 分鐘內的不動）。
提醒：SYNOX 沒有給 pack 用的排程 API，加行程時回一行 /remind_add 給使用者自己決定要不要設。
月曆上顯示別的工具的東西（習慣、旅程…）：只有 `pack.link_sources`（見 engine/links.py）；現在的 SYNOX 沒有這個機制，所以沒有。
"""

import os
import re
import secrets
from datetime import date, datetime, timedelta

from isdtelekit.core.registry import WRITE, Field

from synox.packs import kit

from .bridge import service, text
from .engine import events as ev_mod
from .engine import ics as ics_mod
from .engine.errors import CalError


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

EXPORT_NAME = re.compile(r"^export(?:-(\d{14})-[0-9a-f]+)?\.ics$")   # 0.1.2 以前是固定的 export.ics
STALE_EXPORT_SECONDS = 10 * 60


def export_name(when: datetime) -> str:
    """這次匯出的檔名：時間＋亂數，同一個人同時匯出兩次也不會撞名。"""
    return f"export-{when:%Y%m%d%H%M%S}-{secrets.token_hex(4)}.ics"


def stale_exports(names, when: datetime) -> list:
    """資料夾裡要清掉的舊匯出檔：固定檔名 export.ics（舊版留下的）一律清；新的檔名比 when 早 10 分鐘以上才清
    （正在送的那一份不動）。不是匯出檔的不碰。"""
    out = []
    for name in names:
        found = EXPORT_NAME.match(name)
        if not found:
            continue
        if found.group(1) is None:
            out.append(name)
            continue
        try:
            at = datetime.strptime(found.group(1), "%Y%m%d%H%M%S")
        except ValueError:
            out.append(name)
            continue
        if (when - at).total_seconds() > STALE_EXPORT_SECONDS:
            out.append(name)
    return out


def _write_own(path: str, text: str) -> None:
    """pack 唯一寫檔的地方：path 一定是 pack.path_of(user, ...) 給的（這個人自己的資料夾底下）。"""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)



# ── 欄位（指令與畫面動作共用同一份）────────────────────────────────

def _check_priority(value: str):
    return None if value.strip() in ("0", "1", "2", "3") else "重要程度要是 0–3（0 沒標、1 低、2 中、3 高）"


def _check_yes_no(value: str):
    return None if value.strip().lower() in ("yes", "no") else "要是 yes 或 no"


_N = Field("n", "編號（3，或重複行程的某一次 3@2026-11-12）", required=True, check=service.check_eid)
_TASK_N = Field("n", "卡片編號", required=True, check=kit.positive_int)
EVENT_FIELDS = (
    Field("date", "日期 YYYY-MM-DD（AI／畫面用；人直接寫在句子裡）", check=check_ymd),
    Field("time", "時間 HH:MM 或 HH:MM-HH:MM（沒有＝整天）", check=service.check_time),
    Field("end_date", "跨日行程的結束那天 YYYY-MM-DD", check=check_ymd),
    Field("location", "地點"),
    Field("note", "備註"),
    Field("repeat", "每週重複哪幾天（二四、週末、一到五；none＝不重複）"),
    Field("until", "重複到哪天 YYYY-MM-DD"),
)
FIELDS = {
    "add": (Field("text", "時間和標題（AI 給了 date 就只放標題）", required=True), *EVENT_FIELDS),
    "edit": (_N, Field("title", "新標題"), *EVENT_FIELDS),
    "status": (_N, Field("state", "狀態（planned、done、not_done、cancelled；後面可接原因）", required=True),
               Field("note", "沒做的原因")),
    "keep": (Field("a", "編號", required=True, check=service.check_eid),
             Field("b", "另一個編號", required=True, check=service.check_eid)),
    "rm": (_N,),
    "task": (Field("text", "要做的事", required=True), Field("date", "排在哪天 YYYY-MM-DD（預設今天）", check=check_ymd),
             Field("detail", "細節"), Field("priority", "重要程度 0–3", check=_check_priority)),
    "task_edit": (_TASK_N, Field("title", "新標題"), Field("detail", "細節（- ＝清掉）"),
                  Field("priority", "重要程度 0–3", check=_check_priority),
                  Field("date", "改排到哪天 YYYY-MM-DD", check=check_ymd)),
    "done": (_TASK_N, Field("done", "yes／no（不給＝反過來）", check=_check_yes_no)),
    "task_rm": (_TASK_N,),
    "focus": (Field("n", "卡片編號（- ＝清掉）", required=True), Field("date", "哪一天（預設今天）", check=check_ymd)),
}


def _minus30(hhmm: str) -> str:
    total = int(hhmm[:2]) * 60 + int(hhmm[3:])
    total = total - 30 if total >= 30 else total
    return f"{total // 60:02d}:{total % 60:02d}"


def install(app, pack):
    g = pack.group

    def cal(user):
        return service.Cal(pack, user, lambda: now())

    def guarded(fn):
        """服務層的錯（CalError）→ 「❌ …」；其他的錯交給 SYNOX。"""
        def handler(api, chat_id, user, values):
            try:
                reply = fn(cal(user), values)
            except CalError as e:
                api.send_message(chat_id, f"❌ {e}")
                return
            if reply:
                api.send_message(chat_id, reply)
        return handler

    def lines_of(cards, today, with_day=True):
        return [text.line(c, today, day_label, with_day) for c in cards]

    # ── 指令的文字 ────────────────────────────────────────────────────

    def r_add(c, values):
        out = service.add_event(c, values, parse_when)
        card, today = out["event"], c.today()
        rows = ["📅 加好了：" + text.line(card, today, day_label)]
        if out["past"]:
            rows.append("（這一天已經過了，當作紀錄。）")
        elif not card["all_day"] and not card["series_id"] and not card["repeat"]:
            rows.append(f"要提醒的話：/remind_add {_minus30(card['start'][11:16])} {card['title']} date={card['day']}")
        return "\n".join(rows + text.clash_lines(out["clashes"]))

    def r_edit(c, values):
        out = service.edit_event(c, values)
        return "\n".join(["📅 改好了：" + text.line(out["event"], c.today(), day_label)] + text.clash_lines(out["clashes"]))

    def r_status(c, values):
        state, _, rest = values["state"].strip().partition(" ")
        out = service.set_status(c, values["n"], state, (values.get("note") or rest).strip())
        return "📅 " + text.line(out["event"], c.today(), day_label)

    def r_rm(c, values):
        return "🗑 刪掉了：" + text.line(service.remove_event(c, values["n"])["event"], c.today(), day_label)

    def r_keep(c, values):
        return "👌 兩個都留：" + service.keep_clash(c, values["a"], values["b"])["clash"]["text"]

    def r_clash(c, values):
        rows = service.read_clashes(c, {"days": values.get("days") or 60})["clashes"]
        if not rows:
            return "📅 接下來沒有撞時間的行程。"
        return "\n".join([f"⚠ {len(rows)} 組撞時間："] + [f"  · {x['text']}" for x in rows])

    def r_today(c, values):
        today = c.today()
        day = service.read_day(c, {})
        rows = lines_of(day["events"], today, with_day=False)
        body = [f"📅 {day_label(today, today)}"] + (rows or ["今天沒有行程。"])
        return "\n".join(body + text.tasks_block(day["tasks"]) + text.clash_lines(day["clashes"]))

    def r_week(c, values):
        today = c.today()
        out = service.read_agenda(c, {"days": 7})
        last = today + timedelta(days=6)
        seen, rows = set(), []
        for d in out["days"]:
            for card in d["items"]:
                if card["id"] not in seen:
                    seen.add(card["id"])
                    rows.append(card)
        head = f"📅 這 7 天（{today.month}/{today.day}–{last.month}/{last.day}）"
        return "\n".join([head] + (lines_of(rows, today) or ["這 7 天沒有行程。"]) + text.clash_lines(out["conflicts"]))

    def r_list(c, values):
        cards = service.read_upcoming(c, int(values.get("n") or 10))
        head = f"📅 接下來 {len(cards)} 個行程"
        return "\n".join([head] + (lines_of(cards, c.today()) or ["接下來沒有行程。用 /cal 明天 14:00 看牙醫 加一個。"]))

    def r_month(c, values):
        m = service.read_month(c, {"month": values.get("month") or ""})
        rows = [f"📅 {m['label']}：{m['counts']['events']} 個行程、{m['counts']['tasks']} 張卡片"]
        for d in m["days"]:
            if not d["in_month"] or not d["items"]:
                continue
            names = "、".join((x["at"] + " " if x.get("at") else "") + x["text"] for x in d["items"][:3])
            more = f" …還有 {len(d['items']) - 3} 件" if len(d["items"]) > 3 else ""
            rows.append(f"{int(d['date'][5:7])}/{int(d['date'][8:])}（{WEEK_NAMES[d['weekday']]}）{names}{more}")
        return "\n".join(rows if len(rows) > 1 else rows + ["這個月沒有東西。"])

    def r_task(c, values):
        t = service.add_task(c, values)["task"]
        return f"✅ 加進清單：#{t['id']} {t['title']}（{t['date']}）"

    def r_task_edit(c, values):
        t = service.edit_task(c, values)["task"]
        return f"✅ 改好了：#{t['id']} {t['title']}（{t['date']}）"

    def r_done(c, values):
        t = service.toggle_task(c, values)["task"]
        return ("☑ 做完了：" if t["done"] else "☐ 還沒做完：") + f"#{t['id']} {t['title']}"

    def r_task_rm(c, values):
        return f"🗑 刪掉了：#{service.remove_task(c, values['n'])['task']['id']}"

    def r_focus(c, values):
        out = service.set_focus(c, values)
        if not out["focus"]:
            return f"🎯 {out['date']} 沒有指定最重要的事了。"
        return f"🎯 {out['date']} 最重要的一件事：#{out['task']['id']} {out['task']['title']}"

    def r_tasks(c, values):
        rows = text.tasks_block(service.read_day(c, {"date": values.get("date") or ""})["tasks"])
        return "\n".join(rows) if rows else "✅ 這一天的清單是空的（/cal_task 買牛奶）。"

    # ── 指令 ──────────────────────────────────────────────────────────

    def command(name, fn, summary, **kw):
        kit.command(g, name, guarded(fn), summary, fields=FIELDS.get(name, ()), **kw)

    command("add", r_add, "加一個行程（明天 14:00 看牙醫）", default=True, effect=WRITE, positional=1,
            usage="<時間 標題>（例：明天 14:00 看牙醫） [location=…] [note=…] [repeat=二四 until=2026-12-31]")
    command("edit", r_edit, "改一個行程（編號看 /cal_list；沒給的不動，- ＝清掉）", effect=WRITE, positional=1)
    command("status", r_status, "標記完成了／這次沒做／取消了（planned、done、not_done、cancelled）", effect=WRITE, positional=2)
    command("keep", r_keep, "撞時間的兩個行程「兩個都留」", effect=WRITE, positional=2)
    kit.command(g, "clash", guarded(r_clash), "看哪些行程撞時間", fields=[Field("days", "往後看幾天", check=kit.positive_int)])
    command("today", r_today, "今天的行程與今日清單")
    command("week", r_week, "這 7 天的行程")
    kit.command(g, "list", guarded(r_list), "接下來的行程（預設 10 個）", fields=[Field("n", "幾個", check=kit.positive_int)])
    kit.command(g, "month", guarded(r_month), "這個月（或指定月份）有什麼",
                fields=[Field("month", "YYYY-MM", check=service.check_month)])
    command("rm", r_rm, "刪掉一個行程（編號看 /cal_list；重複行程的 3@日期＝只跳過那一次）", effect=WRITE, positional=1)
    command("task", r_task, "加一張今日清單的卡片", effect=WRITE, positional=1)
    kit.command(g, "tasks", guarded(r_tasks), "今日清單", fields=[Field("date", "哪一天 YYYY-MM-DD（預設今天）", check=check_ymd)])
    command("task_edit", r_task_edit, "改一張卡片（沒給的不動）", effect=WRITE, positional=1)
    command("done", r_done, "勾起來／取消勾（done=yes／no；不給＝反過來）", effect=WRITE, positional=1)
    command("task_rm", r_task_rm, "刪掉一張卡片", effect=WRITE, positional=1)
    command("focus", r_focus, "挑今天最重要的一件事（卡片編號；- ＝清掉）", effect=WRITE, positional=1)

    def ics(api, chat_id, user, values):
        when = now()
        for name in stale_exports(os.listdir(pack.path_of(user)), when):     # 上次沒刪掉的（只看這個人自己的資料夾）
            try:
                os.remove(pack.path_of(user, name))
            except OSError:
                pass
        rows = service.all_events(cal(user))
        if not rows:
            api.send_message(chat_id, "📅 行事曆是空的，沒有東西可以匯出。")
            return
        path = pack.path_of(user, export_name(when))  # data: user：每個人自己的資料夾（不是全部人共用的 pack.path）
        _write_own(path, ics_mod.to_ics(rows, when))
        try:
            api.send_document(chat_id, path, caption=f"📅 {len(rows)} 個行程。在 Google 日曆（設定 → 匯入）或 "
                                                     f"iPhone（點開檔案 → 加入行事曆）匯入。")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    kit.command(g, "ics", ics, "整本行事曆匯出成 .ics 檔（給 Google／Apple 日曆匯入）")

    # ── 小面板（panel.html）──────────────────────────────────────────

    def today_route(user):
        """面板：今天與接下來 7 天的行程、今天的清單。只回這個人自己的。"""
        c = cal(user)
        today = c.today()
        data = service.read_today(c)
        rows = []
        for d in data["agenda"]:
            for card in d["items"]:
                rows.append({"id": card["id"], "day": d["date"], "label": day_label(date.fromisoformat(d["date"]), today),
                             "time": "整天" if card["all_day"] else (card["range"] or card["time"]),
                             "title": card["title"], "location": card["location"]})
        return {"today": today.isoformat(), "label": day_label(today, today), "events": rows,
                "tasks": [{"id": t["id"], "title": t["title"], "done": bool(t.get("done")), "focus": t["is_focus"]}
                          for t in data["tasks"]["tasks"]]}

    pack.web_route("today", today_route)

    # ── 完整畫面（web.app）──────────────────────────────────────────

    if not hasattr(pack, "app_read"):               # 舊版 SYNOX 沒有工具服務：照樣能用指令
        return

    def refuse(e):
        return kit.AppError(str(e))

    def read(fn):
        def handler(user, params):
            try:
                return fn(cal(user), params)
            except CalError as e:
                raise refuse(e) from None
        return handler

    for name, fn in (("month", service.read_month), ("day", service.read_day), ("today", service.read_today),
                     ("agenda", service.read_agenda), ("clashes", service.read_clashes)):
        pack.app_read(name, read(fn))

    def action(name, fn):
        fields = FIELDS[name]

        def handler(user, values):
            clean, missing, errors = kit.check_values(fields, values)
            if missing or errors:
                raise kit.AppError("；".join(errors) or "少了：" + "、".join(f.ask for f in missing))
            try:
                return fn(cal(user), clean)
            except CalError as e:
                raise refuse(e) from None
        return handler

    def status_values(c, v):
        state, _, rest = v["state"].strip().partition(" ")
        return service.set_status(c, v["n"], state, (v.get("note") or rest).strip())

    for name, fn in (("add", lambda c, v: service.add_event(c, v, parse_when)), ("edit", service.edit_event),
                     ("status", status_values), ("rm", lambda c, v: service.remove_event(c, v["n"])),
                     ("keep", lambda c, v: service.keep_clash(c, v["a"], v["b"])), ("task", service.add_task),
                     ("task_edit", service.edit_task), ("done", service.toggle_task),
                     ("task_rm", lambda c, v: service.remove_task(c, v["n"])), ("focus", service.set_focus)):
        pack.app_action(name, action(name, fn))

    def export_ics(user, params):
        c = cal(user)
        return kit.Download(f"calendar-{c.today().isoformat()}.ics", "text/calendar; charset=utf-8",
                            ics_mod.to_ics(service.all_events(c), c.now()).encode("utf-8"))

    pack.app_export("ics", export_ics)
