"""行事曆的服務層：指令（Telegram／AI）與網頁畫面共用的核心。每個函式拿一個 Cal（這一次是誰、現在幾點）和已經檢查過的值，
回 dict（指令那一層再把它排成字，畫面那一層直接給 JSON）。錯誤一律是 CalError（訊息就是要給人看的那一句）。

資料只經 Cal.read()／Cal.write()：pack.store.of(user)（一個人一份、保管箱加密）。這個檔沒有模組層級的狀態。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from ..engine import events, links, monthview, tasks
from ..engine.errors import CalError
from ..engine.store import empty, migrate

TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})(?:\s*[-~～]\s*(\d{1,2}):(\d{2}))?$")
_EID = re.compile(r"^\d+(@\d{4}-\d{2}-\d{2})?$")


def check_eid(value: str):
    """Field(check=…)：行程編號 3，或重複行程的某一次 3@2026-11-12。"""
    return None if _EID.match(value.strip()) else "要是編號（3），或重複行程的某一次（3@2026-11-12）"


def check_time(value: str):
    """Field(check=…)：HH:MM 或 HH:MM-HH:MM；- ＝整天（只有 /cal_edit 用得到）。"""
    if value.strip() == "-":
        return None
    m = TIME_RANGE.match(value.strip())
    if not m:
        return "要是 HH:MM 或 HH:MM-HH:MM（例如 14:00-15:30）"
    nums = [int(x) for x in m.groups() if x is not None]
    if any(h > 23 for h in nums[0::2]) or any(mm > 59 for mm in nums[1::2]):
        return "不是真的時間"
    if len(nums) == 4 and (nums[2], nums[3]) == (nums[0], nums[1]):
        return "結束不能跟開始同一刻"
    return None


def check_month(value: str):
    return None if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value.strip()) else "要是 YYYY-MM（例如 2026-11）"


class Cal:
    """這一次呼叫的脈絡：哪個工具、誰、現在幾點。"""

    def __init__(self, pack, user, clock):
        self.pack, self.user, self.clock = pack, user, clock

    def now(self) -> datetime:
        return self.clock()

    def today(self) -> date:
        return self.clock().date()

    def read(self) -> dict:
        """讀（舊版資料先升級成 v2；只在記憶體裡，下一次寫入才存）。"""
        return migrate(self.pack.store.of(self.user).load(None))

    def write(self, fn):
        """fn(data) 改 data 並回結果；順便把沒做完的卡片順延到今天。"""
        today = self.today()

        def run(data):
            migrate(data)
            tasks.carry_over(data, today)
            return fn(data)
        return self.pack.store.of(self.user).update(run, empty())


def split_time(value) -> dict:
    """'14:00-15:30' → {start, end}；'-' ＝整天；空＝不動。"""
    s = str(value or "").strip()
    if not s:
        return {}
    if s == "-":
        return {"start": "-", "end": "-"}
    m = TIME_RANGE.match(s)
    out = {"start": f"{int(m.group(1)):02d}:{m.group(2)}"}
    if m.group(3):
        out["end"] = f"{int(m.group(3)):02d}:{m.group(4)}"
    return out


# ── 行程 ──────────────────────────────────────────────────────────────

def add_event(cal: Cal, values: dict, parse_when) -> dict:
    """values：text（有 date 時只放標題）、date、time、end_date、location、note、repeat、until。
    parse_when(text, today) → 有 day／start／end／rest／error 的東西（SYNOX 的中文日期解析，由 pack.py 傳進來）。"""
    today = cal.today()
    text = values["text"].strip()
    if values.get("date"):
        payload = {"day": values["date"], **split_time(values.get("time")), "title": text}
        payload.setdefault("start", "")
    else:
        w = parse_when(text, today)
        if w.error:
            raise CalError(w.error)
        if w.day is None and w.start is None:
            raise CalError("看不出是哪一天。例：/cal 明天 14:00 看牙醫、/cal 11/20 整天 員工旅遊")
        title = w.rest if w.start else w.rest.replace("整天", "").strip()
        payload = {"day": (w.day or today).isoformat(), "start": w.start or "", "end": w.end or "", "title": title}
    if not payload["title"]:
        raise CalError("要有標題。例：/cal 明天 14:00 看牙醫")
    for key in ("end_date", "location", "note", "repeat", "until"):
        if values.get(key):
            payload["end_day" if key == "end_date" else key] = values[key].strip()
    if payload.get("start") == "-" or not payload.get("start"):
        payload["start"] = payload["end"] = ""

    def go(data):
        ev = events.add(data, payload, int(cal.now().timestamp()))
        rec = next(e for e in data["events"] if str(e["id"]) == ev["id"])
        clash = events.conflicts(data, rec, today)
        return {"event": events.card(ev), "past": _first_day(ev) < today,
                "clashes": [events.card(c) for c in clash]}
    return cal.write(go)


def _first_day(ev) -> date:
    return datetime.fromisoformat(ev["start"][:10]).date()


def edit_event(cal: Cal, values: dict) -> dict:
    """values：n（編號或 編號@日期）、title、date、time、end_date、location、note、repeat、until。沒給的不動；- ＝清掉。"""
    patch = {}
    for key, name in (("title", "title"), ("date", "day"), ("end_date", "end_day"), ("location", "location"),
                      ("note", "note"), ("repeat", "repeat"), ("until", "until")):
        if key in values:
            v = values[key].strip()
            patch[name] = "" if v == "-" and name in ("location", "note") else v
    patch.update(split_time(values.get("time")))
    if not patch:
        raise CalError("要改什麼？（title、date、time、end_date、location、note、repeat、until）")
    today = cal.today()

    def go(data):
        ev = events.update(data, values["n"], patch, int(cal.now().timestamp()))
        rec = next((e for e in data["events"] if str(e["id"]) == ev["id"]), None)
        clash = events.conflicts(data, rec, today) if rec else []
        return {"event": events.card(ev), "clashes": [events.card(c) for c in clash]}
    return cal.write(go)


def set_status(cal: Cal, n: str, state: str, note: str = "") -> dict:
    patch = {"status": state.strip().lower()}
    if note:
        patch["status_note"] = note
    return cal.write(lambda data: {"event": events.card(events.update(data, n, patch, int(cal.now().timestamp())))})


def remove_event(cal: Cal, n: str) -> dict:
    return cal.write(lambda data: {"event": events.card(events.delete(data, n))})


def keep_clash(cal: Cal, a: str, b: str) -> dict:
    return cal.write(lambda data: {"clash": events.ack_clash(data, a, b)})


# ── 今日清單 ──────────────────────────────────────────────────────────

def add_task(cal: Cal, values: dict) -> dict:
    day = values.get("date") or cal.today().isoformat()
    return cal.write(lambda data: {"task": tasks.add(data, values["text"], day, values.get("detail", ""),
                                                      values.get("priority") or 0, int(cal.now().timestamp()))})


def edit_task(cal: Cal, values: dict) -> dict:
    patch = {k: values[k] for k in ("title", "detail", "priority", "date") if k in values}
    if not patch:
        raise CalError("要改什麼？（title、detail、priority、date）")
    return cal.write(lambda data: {"task": tasks.update(data, values["n"], patch)})


def toggle_task(cal: Cal, values: dict) -> dict:
    done = {"yes": True, "no": False}.get(str(values.get("done", "")).strip().lower())
    today = cal.today()
    return cal.write(lambda data: {"task": tasks.toggle(data, values["n"], today, done)})


def remove_task(cal: Cal, n: str) -> dict:
    return cal.write(lambda data: {"task": tasks.remove(data, n)})


def set_focus(cal: Cal, values: dict) -> dict:
    day = values.get("date") or cal.today().isoformat()

    def go(data):
        tid = tasks.set_focus(data, values.get("n", ""), day)
        return {"focus": tid, "date": day, "task": tasks.get(data, tid) if tid else None}
    return cal.write(go)


# ── 讀 ────────────────────────────────────────────────────────────────

def read_month(cal: Cal, params: dict) -> dict:
    today = cal.today()
    year, mon = today.year, today.month
    if params.get("month"):
        try:
            year, mon = (int(x) for x in params["month"].split("-"))
        except ValueError:
            raise CalError("月份要是 YYYY-MM") from None
    first = date(year, mon, 1)
    start, end = monthview.grid_range(first, date(year + (mon == 12), mon % 12 + 1, 1) - timedelta(days=1))
    extra = links.collect(getattr(cal.pack, "link_sources", None), cal.user, start, end)
    out = monthview.month(cal.read(), year, mon, today, extra)
    out["month_key"] = f"{year:04d}-{mon:02d}"
    return out


def read_day(cal: Cal, params: dict) -> dict:
    today = cal.today()
    day = _parse_day(params.get("date"), today)
    data = cal.read()
    cards = [events.card(e) for e in events.between(data, day, day, include_cancelled=True)]
    cards.sort(key=lambda c: (not c["all_day"], c["start"]))
    clash = [c for c in events.clashes(data, day, 1, include_ok=True)]
    return {"date": day.isoformat(), "events": cards, "tasks": tasks.view(data, day, today), "clashes": clash}


def read_today(cal: Cal, params: dict = None) -> dict:
    today = cal.today()
    data = cal.read()
    ag = events.agenda(data, today, 7)
    return {"today": today.isoformat(), "tasks": tasks.view(data, today, today), "agenda": ag["days"],
            "conflicts": ag["conflicts"], "event_count": sum(len(d["items"]) for d in ag["days"][:1])}


def read_clashes(cal: Cal, params: dict) -> dict:
    days = int(params.get("days") or 60)
    return {"clashes": events.clashes(cal.read(), cal.today(), days, include_ok=bool(params.get("all")))}


def read_agenda(cal: Cal, params: dict) -> dict:
    today = cal.today()
    return events.agenda(cal.read(), _parse_day(params.get("from"), today), int(params.get("days") or 21))


def _parse_day(value, today: date) -> date:
    if not value:
        return today
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise CalError(f"日期要是 YYYY-MM-DD：{value!r}") from None


def read_upcoming(cal: Cal, n: int = 10, horizon: int = 366) -> list:
    """今天起（還沒結束的）最近 n 個行程的卡片，依開始時間排；重複行程每一次各算一個。取消的不列。"""
    today = cal.today()
    rows = events.between(cal.read(), today, today + timedelta(days=horizon))
    rows.sort(key=events.sort_key)
    return [events.card(e) for e in rows[:max(1, min(int(n), 100))]]


def all_events(cal: Cal) -> list:
    """存著的整串紀錄（重複的一串只有一筆；給 .ics 匯出用）。"""
    return list(cal.read()["events"])
