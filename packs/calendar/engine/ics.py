"""iCalendar（RFC 5545）匯出：整本行事曆 → .ics 文字（給 Google／Apple 日曆匯入）。純文字處理，不碰檔案。"""

from __future__ import annotations

from datetime import datetime, timedelta

from .events import _dt, sort_key

_BYDAY = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


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
    """時間用「浮動時間」（沒有時區＝照匯入那一邊的當地時間）。取消的不匯出；重複行程匯出成 RRULE（略過的那幾天 EXDATE）。"""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SYNOX//calendar pack 0.2//ZH-TW", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:SYNOX 行事曆"]
    dtstamp = stamp.strftime("%Y%m%dT%H%M%S")
    for e in sorted((x for x in events if x.get("status") != "cancelled"), key=sort_key):
        start, end = _dt(e["start"]), _dt(e.get("end") or e["start"])
        weekdays = (e.get("repeat") or {}).get("weekdays")
        if weekdays:
            # RFC 5545：DTSTART 本身一定算一次。開始那天不是重複的星期幾時，挪到第一個是的那天（跟畫面一樣不多出一次）
            shift = next(n for n in range(7) if (start + timedelta(days=n)).isoweekday() in weekdays)
            start, end = start + timedelta(days=shift), end + timedelta(days=shift)
            until = (e["repeat"] or {}).get("until")
            if until and start.date() > _dt(until).date():
                continue                                     # 這一串一次都沒有
        lines += ["BEGIN:VEVENT", f"UID:synox-cal-{e['id']}-{e.get('at', 0)}@synox.local", f"DTSTAMP:{dtstamp}"]
        if e.get("all_day"):
            lines.append(f"DTSTART;VALUE=DATE:{start:%Y%m%d}")
            lines.append(f"DTEND;VALUE=DATE:{end + timedelta(days=1):%Y%m%d}")          # 結尾日期不含當天
        else:
            lines.append(f"DTSTART:{start:%Y%m%dT%H%M}00")
            lines.append(f"DTEND:{end:%Y%m%dT%H%M}00")
        rule = e.get("repeat") or {}
        if rule.get("weekdays"):
            parts = ["FREQ=WEEKLY", "BYDAY=" + ",".join(_BYDAY[d - 1] for d in rule["weekdays"])]
            if rule.get("until"):
                until = _dt(rule["until"])
                parts.append(f"UNTIL={until:%Y%m%d}" if e.get("all_day") else f"UNTIL={until:%Y%m%d}T235959")
            lines.append("RRULE:" + ";".join(parts))
            for d in rule.get("skip") or []:
                day = _dt(d)
                lines.append(f"EXDATE;VALUE=DATE:{day:%Y%m%d}" if e.get("all_day")
                             else f"EXDATE:{day:%Y%m%d}T{start:%H%M}00")
        lines.append("SUMMARY:" + ics_escape(e["title"]))
        if e.get("location"):
            lines.append("LOCATION:" + ics_escape(e["location"]))
        if e.get("note"):
            lines.append("DESCRIPTION:" + ics_escape(e["note"]))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(ics_fold(x) + "\r\n" for x in lines)
