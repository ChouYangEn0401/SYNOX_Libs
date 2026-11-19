"""行程：某個時間開始、某個時間結束的事（從一個本機行事曆程式移植，邏輯改成「資料進、資料出」）。

護欄（從舊版帶過來，改這個檔之前先讀）
1. 你可以排自己的時段；**不做跟別人有關的那一半**（沒有邀請、不寄信、不改別人的時間）。
2. **重複只做最小版**：每週哪幾天＋到哪天（until）。資料裡一串只有一筆，讀的時候才展開；
   只動某一次＝把那天加進 skip、另外建一筆一般的行程（detached_from 記從哪一次拆出來）。
3. **撞時間不擋，只報告**：整天的事不算撞、取消的不算；說「兩個都留」（ack_clash）之後那一組不再算沒處理。

行程長這樣（存的時候）：
    {"id": 3, "title": "吉他課", "start": "2026-11-10T20:00", "end": "2026-11-10T21:00", "all_day": false,
     "location": "", "note": "", "status": "planned", "at": 1700000000,
     "repeat": {"weekdays": [2, 4], "until": "2026-12-31", "skip": ["2026-11-12"]}, "clash_ok": ["5"]}
整天的 start／end 只有日期（"2026-11-20"，end 含當天）。展開出來的某一次：id＝"3@2026-11-12"，多了 series_id／occurrence。
讀出來的 id 一律是字串（"3" 或 "3@日期"）；存起來的整串紀錄 id 是整數。
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, time, timedelta

from .errors import CalError
from .store import DEFAULT_MINUTES
from .weekdays import WEEKDAY_LABEL, parse_until, parse_weekdays, repeat_text

MAX_SPAN_DAYS = 30           # 一筆行程最長幾天（出差、連假這種跨日的要容得下）
MAX_TITLE = 120
MAX_SKIP = 400
MAX_CLASH_OK = 200
REPEAT_HORIZON_DAYS = 92     # 沒有 until 的重複行程，「整串跟誰撞」最多往後看幾天
STATUS_NOTE_MAX = 200

#: not_done＝時間過了、這次沒做（事後照實記，可以寫為什麼）；cancelled＝事前說不去了
STATUSES = (("planned", "🗓", "排著"), ("done", "✔", "完成了"), ("not_done", "🙅", "這次沒做"), ("cancelled", "🚫", "取消了"))
STATUS_IDS = tuple(s[0] for s in STATUSES)
STATUS_LABEL = {s[0]: f"{s[1]} {s[2]}" for s in STATUSES}

_MISSING = object()
_CLEAR = ("", "-")


# ── 小工具 ────────────────────────────────────────────────────────────

def _dt(value) -> datetime:
    s = str(value)
    return datetime.strptime(s, "%Y-%m-%dT%H:%M") if "T" in s else datetime.strptime(s, "%Y-%m-%d")


def _iso(dt: datetime, all_day: bool) -> str:
    return dt.strftime("%Y-%m-%d") if all_day else dt.strftime("%Y-%m-%dT%H:%M")


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _need_day(value, what="日期") -> date:
    d = _day(value) if value not in (None, "") else None
    if d is None:
        raise CalError(f"{what}要是 YYYY-MM-DD：{value!r}")
    return d


def _clock(value):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise CalError(f"時間要是 HH:MM（00:00–23:59）：{value!r}")
    return int(m.group(1)), int(m.group(2))


def _bool(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1", "yes", "y", "on", "是")


def _md(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}（{WEEKDAY_LABEL[d.weekday()]}）"


def split_id(eid):
    """'3@2026-11-12' → ('3', '2026-11-12')；沒有 @ 的日子是空字串。"""
    sid, _, day = str(eid or "").strip().partition("@")
    return sid, day


def sort_key(ev: dict):
    return (str(ev.get("start", "")), str(ev.get("title", "")), str(ev.get("id", "")))


def is_series(ev: dict) -> bool:
    """這是一整串的那筆紀錄（不是展開出來的某一次）。"""
    return bool(ev.get("repeat")) and not ev.get("occurrence")


# ── 校正一筆輸入 ──────────────────────────────────────────────────────

def _rule_of(p: dict, base: dict, start_day: date):
    """這一筆的重複規則：payload 有講就照 payload，沒講就沿用 base 的（skip 一律沿用）。"""
    old = base.get("repeat") or None
    until_raw = p["until"] if "until" in p else _MISSING
    if "repeat" in p:
        wd = parse_weekdays(p["repeat"])
        rule = None if not wd else {"weekdays": wd, "until": (old or {}).get("until") or "",
                                    "skip": list((old or {}).get("skip") or [])}
    elif old:
        rule = {"weekdays": parse_weekdays(old.get("weekdays")), "until": old.get("until") or "",
                "skip": list(old.get("skip") or [])}
    else:
        rule = None
    if until_raw is not _MISSING:
        u = parse_until(until_raw, start_day)
        if rule is None:
            if u is None:
                return None
            raise CalError("「重複到哪天」只有重複的行程才用得到——同時給每週哪幾天（例：repeat=二四）")
        rule["until"] = u.isoformat() if u else ""
    return rule


def clean(payload: dict, base: dict = None) -> dict:
    """把一筆（可能很髒的）輸入校成可以存的樣子。**不寫資料。**同一支函式服務「新建」與「修改」：沒給的欄位沿用 base。

    payload 的鍵（都是字串；都可選，新建時 title 與 day 必填）：
      title、day（YYYY-MM-DD）、start（HH:MM；空或 - ＝整天）、end（HH:MM）、end_day（跨日的結束那天；- ＝同一天）、
      all_day、location、note、repeat（每週哪幾天；none ＝不重複）、until（重複到哪天）、status、status_note。
    只給 day（拖到別天）＝時間與長度不變；只給 start ＝整段平移。
    """
    base = base or {}
    p = {k: v for k, v in (payload or {}).items() if v is not None}

    title = str(p.get("title", base.get("title", ""))).strip()[:MAX_TITLE]
    if not title:
        raise CalError("行程要有名字")

    bs = _dt(base["start"]) if base.get("start") else None
    be = _dt(base["end"]) if base.get("end") else None
    base_all = bool(base.get("all_day"))
    if "day" in p:
        day = _need_day(p["day"])
    elif bs:
        day = bs.date()
    else:
        raise CalError("行程要有日期")

    if "all_day" in p:
        all_day = _bool(p["all_day"])
    elif "start" in p:
        all_day = str(p["start"]).strip() in _CLEAR
    else:
        all_day = base_all

    span_days = (be.date() - bs.date()).days if bs and be else 0
    if "end_day" in p:
        end_day = day if str(p["end_day"]).strip() in _CLEAR else _need_day(p["end_day"], "結束那天")
    else:
        end_day = day + timedelta(days=span_days)

    if all_day:
        start, end = datetime.combine(day, time(0)), datetime.combine(end_day, time(0))
        if end < start:
            raise CalError("結束的日子比開始早")
    else:
        if "start" in p and str(p["start"]).strip() not in _CLEAR:
            sclock = _clock(p["start"])
        elif bs and not base_all:
            sclock = (bs.hour, bs.minute)
        else:
            raise CalError("要有開始時間（HH:MM）；不填時間就是整天")
        start = datetime.combine(day, time(*sclock))
        if "end" in p and str(p["end"]).strip() not in _CLEAR:
            eclock = _clock(p["end"])
            end = datetime.combine(end_day, time(*eclock))
            if end <= start:
                if "end_day" not in p and eclock < sclock:        # 20:00 到 00:30：跨過午夜到隔天
                    end += timedelta(days=1)
                else:
                    raise CalError("結束時間要在開始時間之後")
        elif "end_day" in p and str(p["end_day"]).strip() not in _CLEAR:
            end = datetime.combine(end_day, time(be.hour, be.minute) if be and not base_all else time(*sclock))
            if end <= start:
                end = start + timedelta(minutes=DEFAULT_MINUTES)
        elif be and not base_all and "end" not in p:
            end = start + (be - bs)                               # 長度不變（整段平移）
        else:
            end = start + timedelta(minutes=DEFAULT_MINUTES)
    if (end.date() - start.date()).days > MAX_SPAN_DAYS:
        raise CalError(f"一筆行程最長 {MAX_SPAN_DAYS} 天")

    rule = _rule_of(p, base, start.date())
    if rule:
        if all_day and end.date() != start.date():
            raise CalError("重複的整天行程一次只能是一天")
        if not all_day and end - start > timedelta(hours=24):
            raise CalError("重複的行程一次最長 24 小時")
        # 自己的 start/end 就是第一次：開始那天不是指定的星期幾 → 往後找第一個對的日子
        shift = next(i for i in range(7) if (start.date() + timedelta(days=i)).isoweekday() in rule["weekdays"])
        if shift:
            start, end = start + timedelta(days=shift), end + timedelta(days=shift)
        first = start.date().isoformat()
        if rule["until"] and rule["until"] < first:
            raise CalError(f'重複到 {rule["until"]}，比第一次（{first}）還早')
        rule["skip"] = sorted({str(d) for d in rule["skip"]
                               if first <= str(d) and (not rule["until"] or str(d) <= rule["until"])})[-MAX_SKIP:]
        rule = {k: v for k, v in rule.items() if v}               # 空的 until／skip 不寫進資料

    status = str(p.get("status", base.get("status", "planned")))
    if status not in STATUS_IDS:
        raise CalError(f'不知道這個狀態：{status}（只有 {"/".join(STATUS_IDS)}）')

    out = {"id": base.get("id"), "title": title, "start": _iso(start, all_day), "end": _iso(end, all_day),
           "all_day": all_day, "location": str(p.get("location", base.get("location", "")) or "").strip()[:120],
           "note": str(p.get("note", base.get("note", "")) or "").strip()[:1000],
           "status": status, "at": base.get("at") or 0}
    if rule:
        out["repeat"] = rule
    ok = [str(x) for x in (base.get("clash_ok") or []) if x][-MAX_CLASH_OK:]
    if ok:
        out["clash_ok"] = ok
    if base.get("detached_from"):
        out["detached_from"] = str(base["detached_from"])
    note = str(p.get("status_note", base.get("status_note", "")) or "").strip()[:STATUS_NOTE_MAX]
    if note and status == "not_done":                              # 原因只跟著「這次沒做」走
        out["status_note"] = note
    return out


# ── 重複行程：讀的時候才展開 ───────────────────────────────────────────

def _span(ev: dict):
    start = _dt(ev["start"])
    end = _dt(ev.get("end") or ev["start"])
    if ev.get("all_day"):
        end = end.replace(hour=23, minute=59)
    return start, end


def _expand(rec: dict, lo: date, hi: date) -> list:
    """一串在 [lo, hi] 這幾天（含）裡的每一次。跨午夜的那一次，前一天開始的也算進來。"""
    rule = rec.get("repeat") or {}
    wd = set(rule.get("weekdays") or [])
    if not wd:
        return []
    start, end = _dt(rec["start"]), _dt(rec.get("end") or rec["start"])
    all_day = bool(rec.get("all_day"))
    dur = end - start
    spill = (end.date() - start.date()).days
    until = _day(rule.get("until"))
    skip = set(rule.get("skip") or [])
    d = max(start.date(), lo - timedelta(days=spill))
    last = hi if until is None else min(hi, until)
    out = []
    while d <= last:
        iso = d.isoformat()
        if d.isoweekday() in wd and iso not in skip:
            s = datetime.combine(d, start.time())
            out.append({**rec, "id": f'{rec["id"]}@{iso}', "series_id": str(rec["id"]), "occurrence": iso,
                        "start": _iso(s, all_day), "end": _iso(s + dur, all_day)})
        d += timedelta(days=1)
    return out


def _instances(ev: dict, today: date) -> list:
    """一筆行程實際佔掉的那幾段：一串＝展開（沒有 until 就看到 REPEAT_HORIZON_DAYS），其他＝自己。"""
    if not ev.get("repeat") or ev.get("occurrence"):
        return [ev]
    first = _dt(ev["start"]).date()
    until = _day((ev.get("repeat") or {}).get("until"))
    return _expand(ev, first, until or (max(first, today) + timedelta(days=REPEAT_HORIZON_DAYS)))


def _plain(rec: dict) -> dict:
    return {**rec, "id": str(rec["id"])}


def between(data: dict, from_day, to_day, include_cancelled: bool = False) -> list:
    """跟 [from_day, to_day]（含兩端）有交集的行程，照開始時間排。重複行程展開成每一次（id 是字串）。"""
    a, b = _need_day(from_day), _need_day(to_day)
    lo, hi = datetime(a.year, a.month, a.day), datetime(b.year, b.month, b.day, 23, 59)
    out = []
    for rec in data["events"]:
        if not include_cancelled and rec.get("status") == "cancelled":
            continue
        for ev in (_expand(rec, a, b) if rec.get("repeat") else [_plain(rec)]):
            s, e = _span(ev)
            if s <= hi and (e >= lo if ev.get("all_day") else e > lo):      # 前一晚剛好結束在 00:00 不算碰到這天
                out.append(ev)
    out.sort(key=lambda e: (str(e.get("start", "")), str(e.get("title", ""))))
    return out


def occurrence_of(rec: dict, day: str):
    """某一串在某一天的那一次（跳過了、或那天不是它的日子就 None）。"""
    if not rec or not rec.get("repeat"):
        return None
    d = _day(day)
    if d is None:
        return None
    return next((o for o in _expand(rec, d, d) if o["occurrence"] == d.isoformat()), None)


def find(data: dict, eid):
    """一筆行程（讀出來的樣子）；'3@日期' 回那一串在那天的那一次。找不到回 None。"""
    sid, day = split_id(eid)
    rec = next((e for e in data["events"] if str(e["id"]) == sid), None)
    if rec is None:
        return None
    return occurrence_of(rec, day) if day else _plain(rec)


def _record(data: dict, eid) -> dict:
    sid, _day_part = split_id(eid)
    rec = next((e for e in data["events"] if str(e["id"]) == sid), None)
    if rec is None:
        raise CalError(f"找不到這筆行程：{eid}（/cal_list 看編號）")
    return rec


# ── 增刪改 ────────────────────────────────────────────────────────────

def add(data: dict, payload: dict, now_epoch: int) -> dict:
    ev = clean(payload)
    ev["id"] = int(data.get("next_id") or 1)
    ev["at"] = int(now_epoch)
    data["next_id"] = ev["id"] + 1
    data["events"].append(ev)
    return _plain(ev)


def _add_skip(rec: dict, day: str) -> None:
    rule = dict(rec.get("repeat") or {})
    rule["skip"] = sorted(set(rule.get("skip") or []) | {day})[-MAX_SKIP:]
    rec["repeat"] = rule


def update(data: dict, eid, patch: dict, now_epoch: int = 0) -> dict:
    """改一筆。eid 是一整串（或一般的一筆）→ 改那一筆紀錄（重複行程＝整串）。
    eid 是 '3@日期' → 只改那一次：那天加進 skip，另外建一筆一般的行程（回傳新的那一筆）；
    改成「取消了」＝只跳過那一次（回傳那一次，帶 skipped: True）。"""
    patch = dict(patch or {})
    sid, day = split_id(eid)
    hit = _record(data, eid)
    if not day:
        merged = clean(patch, base=hit)
        hit.clear()
        hit.update(merged)
        return _plain(hit)

    occ = occurrence_of(hit, day)
    if occ is None:
        raise CalError(f"找不到這一次：{eid}（那天跳過了、或不是「{repeat_text(hit.get('repeat'))}」的日子）")
    if "repeat" in patch or "until" in patch:
        raise CalError(f"只改這一次的時候不能改重複規則——要改請改整串（編號 {sid}）")
    _add_skip(hit, day)
    if str(patch.get("status") or "") == "cancelled":
        return {**occ, "status": "cancelled", "skipped": True}
    base = {k: v for k, v in occ.items() if k not in ("id", "series_id", "occurrence", "repeat", "at", "clash_ok")}
    base["detached_from"] = occ["id"]
    single = clean(patch, base=base)
    single["id"] = int(data.get("next_id") or 1)
    single["at"] = int(now_epoch)
    data["next_id"] = single["id"] + 1
    # 「兩個都留」記的是那一次的 id——拆出來之後改記新的這一筆，不然一改，那一組又變成沒處理
    carried = []
    for x in hit.get("clash_ok") or []:
        other = next((e for e in data["events"] if str(e["id"]) == split_id(x)[0]), None)
        if other and occ["id"] in (other.get("clash_ok") or []):
            other["clash_ok"] = [str(single["id"]) if y == occ["id"] else y for y in other["clash_ok"]]
            carried.append(x)
    if carried:
        single["clash_ok"] = carried
    data["events"].append(single)
    return _plain(single)


def delete(data: dict, eid) -> dict:
    """刪掉並把刪掉的那一筆回傳。'3@日期' ＝只跳過那一次（回傳那一次，帶 skipped: True）；只給編號＝整串（或一般的一筆）。"""
    sid, day = split_id(eid)
    hit = _record(data, eid)
    if day:
        occ = occurrence_of(hit, day)
        if occ is None:
            raise CalError(f"找不到這一次：{eid}（那天已經跳過了、或不是它的日子）")
        _add_skip(hit, day)
        return {**occ, "skipped": True}
    data["events"] = [e for e in data["events"] if str(e["id"]) != sid]
    for e in data["events"]:                                       # 別人記的「兩個都留」也一起清掉
        if e.get("clash_ok"):
            e["clash_ok"] = [x for x in e["clash_ok"] if split_id(x)[0] != sid]
            if not e["clash_ok"]:
                del e["clash_ok"]
    return _plain(hit)


# ── 看：描述、卡片 ────────────────────────────────────────────────────

def days_touched(ev: dict) -> list:
    """這筆行程會出現在哪幾天的格子裡。結束在隔天 00:00 的不算碰到隔天。"""
    start, end = _span(ev)
    cur, last, out = start.date(), end.date(), []
    if not ev.get("all_day") and end.time() == time(0, 0) and last > cur:
        last -= timedelta(days=1)
    while cur <= last and len(out) <= MAX_SPAN_DAYS + 1:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def minutes_of(ev: dict) -> int:
    start, end = _span(ev)
    return max(0, int((end - start).total_seconds() // 60))


def hm_range(ev: dict) -> str:
    """『19:30–21:00』；整天的、真的跨日的回空字串。"""
    if ev.get("all_day"):
        return ""
    s, e = _span(ev)
    if e.date() == s.date():
        return f"{s:%H:%M}–{e:%H:%M}"
    if e.time() == time(0, 0) and e.date() == s.date() + timedelta(days=1):
        return f"{s:%H:%M}–24:00"
    return ""


def describe(ev: dict) -> str:
    """一行人看得懂的時間：『10/20（一）14:00–15:30』／『10/20（一）整天』／一整串：『每週二四 20:00–21:00（10/13 起）』。"""
    s, e = _span(ev)
    if is_series(ev):
        t = "整天" if ev.get("all_day") else (hm_range(ev) or f"{s:%H:%M} 起 {minutes_of(ev)} 分鐘")
        until = _day((ev.get("repeat") or {}).get("until"))
        rng = f"{s:%m/%d}～{until:%m/%d}" if until else f"{s:%m/%d} 起"
        return f"{repeat_text(ev['repeat'])} {t}（{rng}）"
    head = _md(s.date())
    if ev.get("all_day"):
        return f"{head}–{e.month:02d}/{e.day:02d} 整天" if e.date() > s.date() else f"{head}整天"
    return head + (hm_range(ev) or f"{s:%H:%M}–{e.month:02d}/{e.day:02d} {e:%H:%M}")


def card(ev: dict) -> dict:
    """送到畫面／指令／AI 的那一份：事實加已經算好的字串。"""
    s, _ = _span(ev)
    rule = ev.get("repeat") or None
    return {"id": str(ev["id"]), "title": ev.get("title", ""), "start": ev["start"], "end": ev.get("end") or ev["start"],
            "day": s.date().isoformat(), "all_day": bool(ev.get("all_day")),
            "location": ev.get("location", ""), "note": ev.get("note", ""), "status": ev.get("status", "planned"),
            "status_note": str(ev.get("status_note") or ""),
            "when": describe(ev), "time": "" if ev.get("all_day") else s.strftime("%H:%M"),
            "range": hm_range(ev), "minutes": minutes_of(ev),
            "series_id": str(ev.get("series_id") or ""), "occurrence": str(ev.get("occurrence") or ""),
            "repeat": repeat_text(rule),
            "repeat_rule": ({"weekdays": list(rule.get("weekdays") or []), "until": rule.get("until") or "",
                             "skip": list(rule.get("skip") or [])} if rule else None),
            "detached_from": str(ev.get("detached_from") or "")}


# ── 撞時間 ────────────────────────────────────────────────────────────

def _overlaps(a: dict, b: dict) -> bool:
    a0, a1 = _span(a)
    b0, b1 = _span(b)
    return a0 < b1 and b0 < a1


def _acked(a: dict, b: dict) -> bool:
    """這一組說過「兩個都留」：兩邊的紀錄都記著對方（重複行程記的是那一次的 id）。"""
    return str(b["id"]) in (a.get("clash_ok") or []) and str(a["id"]) in (b.get("clash_ok") or [])


def _pairs(items: list) -> list:
    """這堆 item 裡兩兩重疊的組合（整天的、取消的不算；同一串的不跟自己比）。照開始時間掃一遍。"""
    timed = []
    for ev in items:
        if ev.get("all_day") or ev.get("status") == "cancelled":
            continue
        s, e = _span(ev)
        timed.append((s, e, ev))
    timed.sort(key=lambda t: (t[0], t[1], str(t[2].get("title", ""))))
    out = []
    for i, (_s0, e0, a) in enumerate(timed):
        for s1, _e1, b in timed[i + 1:]:
            if s1 >= e0:
                break
            if a["id"] == b["id"] or (a.get("series_id") and a.get("series_id") == b.get("series_id")):
                continue
            out.append((a, b))
    return out


def _label(ev: dict) -> str:
    r = hm_range(ev)
    return f'{ev.get("title", "")} {r}' if r else f'{ev.get("title", "")}（{describe(ev)}）'


def clash_sig(a: dict, b: dict) -> str:
    """這一組撞時間的「內容」指紋：兩邊的名稱＋起訖。改了時間＝新的指紋。"""
    parts = sorted(f'{x.get("title", "")}|{x.get("start", "")}|{x.get("end", "")}' for x in (a, b))
    return hashlib.sha1("×".join(parts).encode("utf-8")).hexdigest()[:12]


def _clash_out(a: dict, b: dict, ok: bool) -> dict:
    s = max(_span(a)[0], _span(b)[0])
    e = min(_span(a)[1], _span(b)[1])
    pair = f"{_label(a)} × {_label(b)}"
    return {"a": card(a), "b": card(b), "ok": ok, "date": s.date().isoformat(),
            "start": s.strftime("%Y-%m-%dT%H:%M"), "end": e.strftime("%Y-%m-%dT%H:%M"),
            "key": "×".join(sorted([str(a["id"]), str(b["id"])])), "sig": clash_sig(a, b),
            "day": _md(s.date()), "pair": pair, "text": f"{_md(s.date())}{pair}"}


def conflicts(data: dict, ev: dict, today: date, include_ok: bool = False) -> list:
    """這一筆（可以是剛存好的整串）跟哪些已經排好的行程撞了。只回報，不阻止。回傳的是別人那一筆（展開後的，有日期）。"""
    if ev.get("all_day") or ev.get("status") == "cancelled":
        return []
    ev = {**ev, "id": str(ev["id"])}
    mine = _instances(ev, today)
    if not mine:
        return []
    own = {ev["id"], str(ev.get("series_id") or "")} - {""}
    lo = min(_span(m)[0] for m in mine).date() - timedelta(days=1)
    hi = max(_span(m)[1] for m in mine).date()
    others = [o for o in between(data, lo, hi) if not o.get("all_day") and o["id"] not in own
              and str(o.get("series_id") or o["id"]) not in own]
    out, seen = [], set()
    for m in mine:
        for o in others:
            if o["id"] in seen or not _overlaps(m, o) or (not include_ok and _acked(m, o)):
                continue
            seen.add(o["id"])
            out.append(o)
    out.sort(key=lambda o: (str(o.get("start", "")), str(o.get("title", ""))))
    return out


def clashes(data: dict, from_day, days: int = 30, include_ok: bool = False) -> list:
    """[from_day, from_day+days) 裡兩兩撞在一起的行程（重複行程的每一次都算）。預設只回還沒處理的。"""
    start = _need_day(from_day)
    days = max(1, min(int(days or 30), 366))
    out = []
    for a, b in _pairs(between(data, start, start + timedelta(days=days - 1))):
        ok = _acked(a, b)
        if ok and not include_ok:
            continue
        out.append(_clash_out(a, b, ok))
    return out


def ack_clash(data: dict, a_id, b_id) -> dict:
    """「兩個都留」：在兩邊的紀錄上互相記下對方（重複行程記的是那一次的 id）。"""
    a_id, b_id = str(a_id or "").strip(), str(b_id or "").strip()
    if not a_id or not b_id or a_id == b_id:
        raise CalError("要給兩筆不一樣的行程（重複行程的某一次寫成 編號@日期）")
    pairs = []
    for eid in (a_id, b_id):
        sid, day = split_id(eid)
        rec = _record(data, eid)
        if day:
            item = occurrence_of(rec, day)
            if item is None:
                raise CalError(f"找不到這一次：{eid}（那天跳過了、或不是它的日子）")
        elif rec.get("repeat"):
            raise CalError(f"「{rec.get('title', '')}」是重複行程——要講是哪一次：{sid}@YYYY-MM-DD")
        else:
            item = _plain(rec)
        pairs.append((rec, item))
    (ra, a), (rb, b) = pairs
    if a.get("all_day") or b.get("all_day") or "cancelled" in (a.get("status"), b.get("status")) or not _overlaps(a, b):
        raise CalError(f"這兩筆沒有撞在一起（{describe(a)} {a.get('title', '')}／{describe(b)} {b.get('title', '')}），不用標")
    for rec, other in ((ra, b), (rb, a)):
        rec["clash_ok"] = ([x for x in (rec.get("clash_ok") or []) if x != other["id"]] + [other["id"]])[-MAX_CLASH_OK:]
    a2, b2 = {**a, "clash_ok": ra["clash_ok"]}, {**b, "clash_ok": rb["clash_ok"]}
    return _clash_out(*sorted((a2, b2), key=lambda x: _span(x)[0]), True)


# ── 一段時間的清單、月曆用的卡 ─────────────────────────────────────────

def agenda(data: dict, from_day, days: int = 21, include_cancelled: bool = False) -> dict:
    """「接下來這幾天有什麼事」：days[] 是結構化的事實，report 是可以直接唸出來的幾行。"""
    start = _need_day(from_day)
    days = max(1, min(int(days or 21), 366))
    last = start + timedelta(days=days - 1)
    items = between(data, start, last, include_cancelled)
    by_day = {}
    for ev in items:
        for d in days_touched(ev):
            if start.isoformat() <= d <= last.isoformat():
                by_day.setdefault(d, []).append(ev)
    out_days = []
    for i in range(days):
        d = start + timedelta(days=i)
        cards = [card(e) for e in sorted(by_day.get(d.isoformat(), []),
                                         key=lambda e: (not e.get("all_day"), str(e.get("start", ""))))]
        out_days.append({"date": d.isoformat(), "weekday": WEEKDAY_LABEL[d.weekday()], "items": cards,
                         "busy_minutes": sum(c["minutes"] for c in cards if not c["all_day"])})
    clash, clash_ok = [], []
    for a, b in _pairs(items):
        ok = _acked(a, b)
        (clash_ok if ok else clash).append(_clash_out(a, b, ok))
    lines = [f"🗓 {start.isoformat()} ～ {last.isoformat()}（{days} 天）共 {len(items)} 件"]
    for d in out_days:
        if not d["items"]:
            continue
        lines.append(f'{d["date"]}（{d["weekday"]}）')
        for c in d["items"]:
            tail = f'　@{c["location"]}' if c["location"] else ""
            mark = "" if c["status"] == "planned" else f'［{STATUS_LABEL.get(c["status"], c["status"])}］'
            if c["status"] == "not_done" and c["status_note"]:
                tail += f'（{c["status_note"]}）'
            lines.append(f'  · [{c["id"]}] {c["when"]}　{"🔁 " if c["series_id"] else ""}{mark}{c["title"]}{tail}')
    if len(lines) == 1:
        lines.append("（這段時間沒有排任何行程）")
    if clash:
        lines.append(f"⚠ 有 {len(clash)} 組時間撞在一起：")
        lines += [f'  · {c["text"]}' for c in clash[:6]]
        if len(clash) > 6:
            lines.append(f"  （還有 {len(clash) - 6} 組；/cal_clash 看全部）")
    if clash_ok:
        lines.append(f"（另外 {len(clash_ok)} 組撞在一起的，你說過兩個都留）")
    return {"from": start.isoformat(), "to": last.isoformat(), "days": out_days, "count": len(items),
            "conflicts": clash, "conflicts_ok": clash_ok, "report": "\n".join(lines)}


def month_cards(data: dict, first: date, last: date) -> dict:
    """給月曆：這段日期範圍裡每一天該畫哪幾筆。取消的也畫（打橫線；月曆是唯一能把它改回來的畫面）。
    每一張多帶 clash（還沒處理、跟誰撞）與 clash_ok（說過兩個都留的）。"""
    items = between(data, first, last, include_cancelled=True)
    marks = {}
    for a, b in _pairs(items):
        kind = "clash_ok" if _acked(a, b) else "clash"
        for x, y in ((a, b), (b, a)):
            marks.setdefault(x["id"], {"clash": [], "clash_ok": []})[kind].append(
                {"id": y["id"], "title": str(y.get("title", "")), "when": describe(y)})
    out = {}
    for ev in items:
        c = card(ev)
        m = marks.get(ev["id"]) or {}
        c["clash"], c["clash_ok"] = m.get("clash", []), m.get("clash_ok", [])
        for d in days_touched(ev):
            if first.isoformat() <= d <= last.isoformat():
                out.setdefault(d, []).append(c)
    for d in out:
        out[d].sort(key=lambda c: (not c["all_day"], c["start"]))
    return out
