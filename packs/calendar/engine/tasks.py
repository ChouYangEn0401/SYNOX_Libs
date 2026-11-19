"""今日清單與「今天最重要的一件事」（簡化版，從同一個程式的任務桌（今日欄與 focus）移植）。

只留這幾個規則：
- 一張卡片＝{id, title, detail, priority 0–3, date, done}。date 是「這件事排在哪一天」。
- **今天沒做完的，過了午夜自動順延到今天**（舊版的「今日欄」規則；carry_over）。做完的留在它完成的那天。
- 每一天可以挑**一件最重要的事**（focus）；清單把它排在最前面。挑的卡片不存在就當沒挑。
- 完成不會刪卡片；刪除是真的刪。

舊版任務桌的自訂欄位、備忘清單、步驟、時間窗、方向／專案標籤、封存、放下的理由、估時，都不在這裡
（要的話是別的工具的事）。
"""

from __future__ import annotations

from datetime import date, timedelta

from .errors import CalError

MAX_TITLE = 120
MAX_DETAIL = 1000
MAX_PRIORITY = 3
KEEP_DONE_DAYS = 90          # 完成超過這麼久的卡片、舊的 focus 紀錄會被清掉（這是「今天」的東西，不是日誌）


def _day(value, what="日期") -> date:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise CalError(f"{what}要是 YYYY-MM-DD：{value!r}") from None


def _priority(value) -> int:
    try:
        n = int(str(value).strip() or 0)
    except ValueError:
        raise CalError(f"重要程度要是 0–{MAX_PRIORITY}（0 沒標、1 低、2 中、3 高）：{value!r}") from None
    if not 0 <= n <= MAX_PRIORITY:
        raise CalError(f"重要程度要是 0–{MAX_PRIORITY}（0 沒標、1 低、2 中、3 高）：{value!r}")
    return n


def carry_over(data: dict, today: date) -> bool:
    """沒做完、日期已經過了的卡片順延到今天；清掉太舊的完成卡片與 focus。有改動回 True。"""
    moved = False
    iso = today.isoformat()
    for t in data["tasks"]:
        if not t.get("done") and t.get("date", iso) < iso:
            t["date"] = iso
            moved = True
    cutoff = (today - timedelta(days=KEEP_DONE_DAYS)).isoformat()
    keep = [t for t in data["tasks"] if not (t.get("done") and t.get("date", iso) < cutoff)]
    if len(keep) != len(data["tasks"]):
        data["tasks"] = keep
        moved = True
    for old in [d for d in data["focus"] if d < cutoff]:
        del data["focus"][old]
        moved = True
    return moved


def _find(data: dict, tid) -> dict:
    try:
        n = int(str(tid).strip())
    except ValueError:
        raise CalError(f"卡片編號要是數字：{tid!r}（/cal_tasks 看編號）") from None
    hit = next((t for t in data["tasks"] if t["id"] == n), None)
    if hit is None:
        raise CalError(f"沒有 #{n} 這張卡片（/cal_tasks 看編號）")
    return hit


def get(data: dict, tid) -> dict:
    return dict(_find(data, tid))


def add(data: dict, title, day, detail="", priority=0, now_epoch: int = 0) -> dict:
    title = str(title or "").strip()[:MAX_TITLE]
    if not title:
        raise CalError("卡片要有標題")
    t = {"id": int(data.get("next_task") or 1), "title": title, "detail": str(detail or "").strip()[:MAX_DETAIL],
         "priority": _priority(priority), "date": _day(day).isoformat(), "done": False, "at": int(now_epoch)}
    data["next_task"] = t["id"] + 1
    data["tasks"].append(t)
    return dict(t)


def update(data: dict, tid, patch: dict) -> dict:
    """改標題、細節、重要程度、日期；沒給的不動，給「-」清掉細節。"""
    t = _find(data, tid)
    p = {k: v for k, v in (patch or {}).items() if v is not None}
    if "title" in p:
        title = str(p["title"]).strip()[:MAX_TITLE]
        if not title:
            raise CalError("卡片要有標題")
        t["title"] = title
    if "detail" in p:
        t["detail"] = "" if str(p["detail"]).strip() == "-" else str(p["detail"]).strip()[:MAX_DETAIL]
    if "priority" in p:
        t["priority"] = _priority(p["priority"])
    if "date" in p:
        t["date"] = _day(p["date"]).isoformat()
    return dict(t)


def toggle(data: dict, tid, today: date, done=None) -> dict:
    """勾起來／取消勾。done 沒給＝反過來。沒做完的日期過了會順延到今天，所以勾起來時它的日子就是做完的那天
    （先做了未來的事則照原本排的那天留著）。"""
    t = _find(data, tid)
    t["done"] = (not t.get("done")) if done is None else bool(done)
    if t["done"] and t.get("date", "") < today.isoformat():
        t["date"] = today.isoformat()
    return dict(t)


def remove(data: dict, tid) -> dict:
    t = _find(data, tid)
    data["tasks"] = [x for x in data["tasks"] if x["id"] != t["id"]]
    for d in [d for d, ref in data["focus"].items() if ref == t["id"]]:
        del data["focus"][d]
    return dict(t)


def set_focus(data: dict, ref, day: str) -> int:
    """設定／清除（ref 空白或 -）那一天最重要的一件事。回卡片編號，清掉回 0。"""
    key = _day(day).isoformat()
    s = str(ref or "").strip()
    if s in ("", "-", "0", "none"):
        data["focus"].pop(key, None)
        return 0
    t = _find(data, s)
    data["focus"][key] = t["id"]
    return t["id"]


def on_day(data: dict, day: date, today: date) -> list:
    """這一天該看到的卡片：排在這天的；今天還加上沒做完、排在更早的（順延）。依建立順序，沒做完的在前。"""
    iso, t0 = day.isoformat(), today.isoformat()
    rows = [t for t in data["tasks"]
            if t.get("date") == iso or (iso == t0 and not t.get("done") and t.get("date", t0) < t0)]
    return sorted(rows, key=lambda t: (bool(t.get("done")), t["id"]))


def view(data: dict, day: date, today: date) -> dict:
    """某一天的清單：{date, focus: 卡片或 None, tasks: [卡片…], done, total}。focus 排在最前面（也留在 tasks 裡，標 is_focus）。"""
    focus_id = data["focus"].get(day.isoformat())
    rows = []
    for t in on_day(data, day, today):
        c = dict(t)
        c["is_focus"] = t["id"] == focus_id
        c["late"] = t.get("date", "") != day.isoformat()          # 從更早順延過來的
        rows.append(c)
    rows.sort(key=lambda c: (not c["is_focus"], bool(c.get("done")), -c.get("priority", 0), c["id"]))
    focus = next((c for c in rows if c["is_focus"]), None)
    return {"date": day.isoformat(), "focus": focus, "tasks": rows,
            "done": sum(1 for c in rows if c.get("done")), "total": len(rows)}
