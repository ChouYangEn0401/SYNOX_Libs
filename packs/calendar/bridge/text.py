"""把服務層回的 dict 排成聊天裡的文字（純字串處理，不碰資料）。day_label 由 pack.py 傳進來（SYNOX 共用的日期標籤）。"""

from __future__ import annotations

from datetime import date

from ..engine.events import STATUS_LABEL

PRIORITY_MARK = {0: "", 1: "·", 2: "‼", 3: "🔥"}


def line(c: dict, today: date, day_label, with_day: bool = True) -> str:
    """'[3] 明天 11/10（二） 14:00–15:00 看牙醫 @診所'；重複的帶 🔁，不是「排著」的帶狀態。"""
    parts = [f"[{c['id']}]"]
    if with_day:
        parts.append(day_label(date.fromisoformat(c["day"]), today))
    if c["all_day"]:
        parts.append("整天" if c["start"] == c["end"] else f"整天至 {c['end'][5:]}")
    else:
        parts.append(c["range"] or c["when"])
    if c["series_id"] or c["repeat"]:
        parts.append("🔁")
    if c["status"] != "planned":
        parts.append(f"［{STATUS_LABEL.get(c['status'], c['status'])}］")
    parts.append(c["title"])
    if c["location"]:
        parts.append(f"@{c['location']}")
    return " ".join(parts)


def clash_lines(clashes) -> list:
    """clashes：別的行程的卡片（剛加的跟誰撞）或 {text}（一組撞在一起的）。"""
    if not clashes:
        return []
    rows = [c["text"] if "text" in c else f"[{c['id']}] {c['when']} {c['title']}" for c in clashes[:6]]
    return ["⚠ 撞時間（/cal_keep 編號 編號 ＝兩個都留）："] + [f"  · {r}" for r in rows]


def task_line(t: dict) -> str:
    box = "☑" if t.get("done") else ("⭐" if t.get("is_focus") else "☐")
    mark = PRIORITY_MARK.get(t.get("priority", 0), "")
    late = "（順延）" if t.get("late") else ""
    return f"{box} #{t['id']} {mark}{t['title']}{late}".replace("  ", " ")


def tasks_block(view: dict) -> list:
    if not view["tasks"]:
        return []
    out = [f"✅ 今天清單（{view['done']}/{view['total']}）"]
    if view["focus"]:
        out.append(f"🎯 最重要：{view['focus']['title']}")
    return out + [task_line(t) for t in view["tasks"]]
