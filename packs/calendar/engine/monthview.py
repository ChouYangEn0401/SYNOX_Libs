"""月曆總覽：把已經存在的東西攤在日期上（從同一個程式的月曆總覽移植）。**唯讀，自己沒有資料。**

三種來源，投影到日期格子上：
  - 行程（events.py）：整天的畫在最上面，有時間的照時間排；重複行程展開成每一次；撞在一起的帶 clash／clash_ok。
  - 今日清單的卡片（tasks.py）：排在哪天就畫在哪天；今天那格另含從更早順延來的。
  - **選用的外部項目**（links.py）：別的工具（習慣、旅程、專案、待辦…）願意提供的項目；沒有人提供就沒有，不會出錯。

護欄（從舊版帶過來）：這個模組本身只查詢；使用者在月曆上把行程／卡片拖到別天，畫面送的是既有的 edit／task_edit 動作，
不是這裡開的後門。停損線：月曆自己的編輯、跨日長條、時段欄位 → 不做。
"""

from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

from . import events, tasks

MAX_PER_DAY = 4      # 一格塞太多會變成看不懂的色塊；超過的畫面用「還有 N 件」帶過


def grid_range(first: date, last: date):
    """整週對齊（週一起算），所以格子永遠是完整的 5×7 或 6×7。"""
    return first - timedelta(days=first.weekday()), last + timedelta(days=6 - last.weekday())


def month(data: dict, year: int, mon: int, today: date, extra: list = ()) -> dict:
    """extra＝links.collect() 的結果（{date, text, …}）。"""
    if not 1 <= mon <= 12:
        raise ValueError(f"月份要在 1–12 之間：{mon}")
    first, last = date(year, mon, 1), date(year, mon, _cal.monthrange(year, mon)[1])
    start, end = grid_range(first, last)

    by_day = {}
    for iso, cards in events.month_cards(data, start, end).items():
        for c in cards:
            by_day.setdefault(iso, []).append({
                "kind": "event", "id": c["id"], "text": c["title"], "at": c["time"], "all_day": c["all_day"],
                "done": c["status"] in ("done", "cancelled"),          # 「這一格已經沒有要你處理的了」：完成的、取消的都算
                "status": c["status"], "when": c["when"], "location": c["location"],
                "series_id": c["series_id"], "occurrence": c["occurrence"], "repeat": c["repeat"],
                "clash": c["clash"], "clash_ok": c["clash_ok"]})
    d = start
    while d <= end:
        for t in tasks.on_day(data, d, today):
            by_day.setdefault(d.isoformat(), []).append({
                "kind": "task", "id": str(t["id"]), "text": t["title"], "at": "", "all_day": False,
                "done": bool(t.get("done")), "priority": t.get("priority", 0),
                "is_focus": data["focus"].get(d.isoformat()) == t["id"]})
        d += timedelta(days=1)
    for row in extra:
        by_day.setdefault(row["date"], []).append({
            "kind": "link", "id": str(row.get("id", "")), "text": row["text"], "at": row.get("at", ""), "all_day": False,
            "done": bool(row.get("done")), "source": row.get("source", "")})

    days = []
    d = start
    while d <= end:
        iso = d.isoformat()
        items = sorted(by_day.get(iso, []), key=lambda x: (0 if x.get("all_day") else 1, x.get("at") or "99:99",
                                                           x["kind"] != "event"))
        # 送完整清單（不在這裡截斷）：畫面要先依分類開關過濾，再自己截前 N 筆、算「還有 N 件」
        days.append({"date": iso, "day": d.day, "weekday": d.weekday(), "in_month": d.month == mon and d.year == year,
                     "is_today": d == today, "is_past": d < today, "items": items, "total": len(items),
                     "done": sum(1 for x in items if x["done"])})
        d += timedelta(days=1)

    prev_y, prev_m = (year, mon - 1) if mon > 1 else (year - 1, 12)
    next_y, next_m = (year, mon + 1) if mon < 12 else (year + 1, 1)
    inside = [x for dd in days if dd["in_month"] for x in dd["items"]]
    return {"year": year, "month": mon, "label": f"{year} 年 {mon} 月", "days": days, "today": today.isoformat(),
            "prev": {"year": prev_y, "month": prev_m}, "next": {"year": next_y, "month": next_m},
            "counts": {"tasks": sum(1 for x in inside if x["kind"] == "task"),
                       "links": sum(1 for x in inside if x["kind"] == "link"),
                       # 取消的照畫（才改得回來），但不算進「本月 N 個行程」；跨日行程出現在好幾格，只算一件
                       "events": len({x.get("series_id") or x["id"] for x in inside
                                      if x["kind"] == "event" and x.get("status") != "cancelled"})},
            "max_per_day": MAX_PER_DAY}
