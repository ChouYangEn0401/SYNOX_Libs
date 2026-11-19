"""資料的形狀與舊版資料的升級。

    {"v": 2,
     "events": [行程…],            # events.py：一串重複行程只有一筆
     "next_id": 1,                 # 行程編號（固定不變；/cal_rm 3 的 3）
     "tasks": [今日清單的卡片…],    # tasks.py
     "next_task": 1,
     "focus": {"YYYY-MM-DD": 卡片編號}}   # 那一天「最重要的一件事」

0.1.2 以前（v 沒有）的行程長這樣：{id, title, day, start: "HH:MM"|"", end: "HH:MM"|"", all_day, location, note, at}。
migrate() 把它們改成 v2（start／end 變成帶日期的 "YYYY-MM-DDTHH:MM"，整天的只有日期），編號不變，所以舊的 /cal_rm 編號照用。
migrate 可以重複跑（已經是 v2 的原封不動），讀取時一律先跑；下一次寫入才真的存成 v2。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

VERSION = 2
DEFAULT_MINUTES = 60


def empty() -> dict:
    """每次都是新的 dict 和 list（共用的常數做淺拷貝，第一次寫入會改到共用的 list）。"""
    return {"v": VERSION, "events": [], "next_id": 1, "tasks": [], "next_task": 1, "focus": {}}


def _old_event(e: dict) -> dict:
    day = str(e.get("day") or "")
    try:
        d = date.fromisoformat(day)
    except ValueError:
        d = date.today()
    start, end = str(e.get("start") or ""), str(e.get("end") or "")
    out = {k: v for k, v in e.items() if k not in ("day", "start", "end")}
    all_day = bool(e.get("all_day")) or not start
    if all_day:
        out["start"] = out["end"] = d.isoformat()
        out["all_day"] = True
    else:
        s = datetime.fromisoformat(f"{d.isoformat()}T{start}")
        if end:
            t = datetime.fromisoformat(f"{d.isoformat()}T{end}")
            if t <= s:
                t = s + timedelta(minutes=DEFAULT_MINUTES)
        else:
            t = s + timedelta(minutes=DEFAULT_MINUTES)
        out["start"], out["end"] = s.strftime("%Y-%m-%dT%H:%M"), t.strftime("%Y-%m-%dT%H:%M")
        out["all_day"] = False
    out.setdefault("status", "planned")
    return out


def migrate(data) -> dict:
    """舊版或空的資料 → v2（原地補欄位並回傳同一個 dict）。"""
    if not isinstance(data, dict):
        return empty()
    old = data.get("v", 1)                         # 先記下版本（補欄位會把沒有的 v 補成新版）
    for key, value in empty().items():
        data.setdefault(key, value)
    if not isinstance(old, int) or old < VERSION:
        data["events"] = [_old_event(e) if "day" in e else e for e in data["events"]]
        data["v"] = VERSION
    top = max([e["id"] for e in data["events"] if isinstance(e.get("id"), int)] or [0]) + 1
    data["next_id"] = max(int(data.get("next_id") or 1), top)
    return data
