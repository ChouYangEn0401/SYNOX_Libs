"""快速記帳的選項：給聊天助理的按鈕用——先選類別、再選品項，全部從使用者自己的記帳算，不寫死。

- 類別：支出細項照「最近 180 天用了幾次」排（一樣多時生活費的類別在前，再照類別樹的順序）；**現在這個時間的那一餐排第一**
  （10:30 前早餐……，同 classify.meal_by_time）。回 total 讓按鈕知道還有多少、可以「更多」往下翻（offset）。
- 品項：某個類別裡記過的內容（desc）。最近＝最後一次記的時間排；最常＝次數排（跟最近重複的不再列）；
  每個品項帶「常見金額」（出現最多次的金額，一樣多取最近的）與最後一次的帳戶，按了就能直接記。
  more 是其餘的（照次數），total 是這個類別一共有幾種品項。
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date, timedelta
from typing import Any

from .. import clock
from ..errors import NotFound
from . import classify, labels, refdata

WINDOW_DAYS = 180
LUNCH_WINDOW = 45                                              # 午餐提醒看最近幾天吃了什麼
UNSORTED = "待分類"


def categories(conn: sqlite3.Connection, *, limit: int = 8, offset: int = 0, now: str | None = None) -> dict[str, Any]:
    since = (clock.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    meal = refdata.system_name(conn, classify.meal_key_by_time(now or clock.now().strftime("%H:%M")))
    counts = {r["category_id"]: (r["n"], r["last"]) for r in conn.execute(
        "SELECT category_id, COUNT(*) AS n, MAX(date) AS last FROM transactions WHERE status = 'active' AND kind = 'expense' "
        "AND category_id IS NOT NULL AND date >= ? GROUP BY category_id", (since,))}
    ids = {c["name"]: c["id"] for c in refdata.categories(conn)}
    rows = []
    order = 0
    for b in refdata.tree(conn):
        for g in b["groups"]:
            for c in g["categories"]:
                if c["kind"] != "expense" or c["name"] == UNSORTED:
                    continue
                n, last = counts.get(ids[c["name"]], (0, None))
                rows.append({"name": c["name"], "group": g["name"], "count": n, "last_used": last,
                             "now": c["name"] == meal, "_order": order, "_living": b["is_living"]})
                order += 1
    rows.sort(key=lambda r: (not r["now"], -r["count"], not r["_living"], r["_order"]))
    for r in rows:
        r.pop("_order")
        r.pop("_living")
    return {"total": len(rows), "offset": offset, "limit": limit, "items": rows[offset:offset + limit],
            "has_more": offset + limit < len(rows), "meal_now": meal}


def items(conn: sqlite3.Connection, category: str, *, limit: int = 4, offset: int = 0) -> dict[str, Any]:
    cat = refdata.find_category(conn, category)
    if cat is None:
        raise NotFound(f"找不到類別「{category}」", suggestions=refdata.category_suggestions(conn, category))
    seen: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
            "SELECT t.description, t.amount, t.date, t.time, a.name AS account FROM transactions t "
            "JOIN accounts a ON a.id = t.account_id WHERE t.status = 'active' AND t.category_id = ? "
            "AND TRIM(t.description) <> '' ORDER BY t.date DESC, COALESCE(t.time, '') DESC, t.rowid DESC", (cat["id"],)):
        desc = r["description"].strip()
        if labels.is_filler(desc, cat["name"]):                  # 網路轉、午餐／午餐：不是「細節」，不要變成按鈕
            continue
        it = seen.get(desc)
        if it is None:
            it = seen[desc] = {"desc": desc, "count": 0, "last_date": r["date"], "account": r["account"], "_amounts": Counter(),
                               "_order": len(seen)}
        it["count"] += 1
        it["_amounts"][r["amount"]] += 1
    for it in seen.values():
        amounts = it.pop("_amounts")
        top = max(amounts.values())
        it["amount"] = next(a for a in amounts if amounts[a] == top)   # 一樣多：先出現的（＝最近的）
    by_recent = sorted(seen.values(), key=lambda x: x["_order"])
    recent = by_recent[:limit]
    taken = {x["desc"] for x in recent}
    by_count = sorted(seen.values(), key=lambda x: (-x["count"], x["_order"]))
    frequent = [x for x in by_count if x["desc"] not in taken][:limit]
    taken |= {x["desc"] for x in frequent}
    rest = [x for x in by_count if x["desc"] not in taken]
    for x in seen.values():
        x.pop("_order", None)
    return {"category": cat["name"], "group": cat["group_name"], "total": len(seen), "recent": recent,
            "frequent": frequent, "more": rest[offset:offset + limit * 2], "more_total": len(rest),
            "has_more": offset + limit * 2 < len(rest), "offset": offset}


def lunch(conn: sqlite3.Connection, *, day: date | None = None, limit: int = 3) -> dict[str, Any]:
    """平日中午聊天助理問「午餐吃了嗎」用（由外部排程在中午觸發）。

    設計：每天中午主動問一次；午餐不一定每天同一家、金額也會變，所以給「平常吃的」一鍵記，也給最近吃過的別家、
    還有「別家／金額不一樣」與「今天沒吃」。這裡只算資料：
    - ask：平日（設定 quick.lunch_weekdays，預設週一到週五）而且今天還沒有午餐才問；週末、已經記了就不出聲。國定假日算不出來，按「今天沒吃」就好。
    - suggestion：最近 45 天最常出現的「店家＋金額」（一樣多取最近的）；others：最近吃過的其他組合（最近的在前）。
      每個都帶帳戶，按了就能直接記。從沒記過午餐就沒有 suggestion，只問怎麼記。
    """
    today = day or clock.today()
    from . import settings
    weekday = today.weekday() in settings.weekdays(settings.value(conn, "quick.lunch_weekdays"))   # 預設週一到週五
    cat = refdata.system_category(conn, "meal.lunch")
    out: dict[str, Any] = {"date": today.isoformat(), "weekday": weekday, "recorded": False, "ask": False,
                           "category": cat["name"] if cat else refdata.SYSTEM_KEYS["meal.lunch"], "suggestion": None, "others": [], "window_days": LUNCH_WINDOW}
    if cat is None:
        out["ask"] = weekday
        return out
    out["recorded"] = conn.execute(
        "SELECT 1 FROM transactions WHERE status = 'active' AND kind = 'expense' AND category_id = ? AND date = ? LIMIT 1",
        (cat["id"], today.isoformat())).fetchone() is not None
    out["ask"] = weekday and not out["recorded"]
    since = (today - timedelta(days=LUNCH_WINDOW)).isoformat()
    combos: dict[tuple[str, int], dict[str, Any]] = {}
    for r in conn.execute(
            "SELECT t.description, t.amount, t.date, a.name AS account FROM transactions t JOIN accounts a ON a.id = t.account_id "
            "WHERE t.status = 'active' AND t.kind = 'expense' AND t.category_id = ? AND t.date >= ? AND t.date < ? "
            "ORDER BY t.date DESC, COALESCE(t.time, '') DESC, t.rowid DESC", (cat["id"], since, today.isoformat())):
        desc = (r["description"] or "").strip()
        if labels.is_filler(desc, cat["name"]) or r["amount"] <= 0:
            continue
        it = combos.setdefault((desc, r["amount"]), {"desc": desc, "amount": r["amount"], "account": r["account"],
                                                     "count": 0, "last_date": r["date"], "_order": len(combos)})
        it["count"] += 1
    ranked = sorted(combos.values(), key=lambda x: (-x["count"], x["_order"]))
    if ranked:
        out["suggestion"] = ranked[0]
        rest = [x for x in sorted(combos.values(), key=lambda x: x["_order"]) if x is not ranked[0]]
        out["others"] = rest[:limit]
    for x in ([out["suggestion"]] if out["suggestion"] else []) + out["others"]:
        x.pop("_order", None)
    return out
