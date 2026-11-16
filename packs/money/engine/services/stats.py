"""本期統計：生活費還能花多少、每天花多少、各大類花費、六大類實際數字。總覽、budget show、summary、檢討都用這裡。

生活支出＝支出、屬於生活支出六大類（或待分類）、不是分期／借錢買的（financed 另外列）。
六大類實際數字的算法：
- 支出 → 細項所屬大類的六大類；待分類算生活；financed 不算（期數由負債排程補，P7）；
- 轉帳 → 轉進的帳戶綁了用途或是資金操盤帳戶就算那一格；再來細項有六大類就用它（例：勞退自提→固定支出），否則照用途：saving→存錢、investing→資產投資、
  debt→負債償還；繳卡費、帳戶互轉、借出、收回都不算（底下的刷卡已經算過）。
- 負債償還＝當期到期的排程（卡分期、自借款、信貸的每一期）＋沒連到排程的還款轉帳；連到排程的轉帳不再算一次。
每日狀態：花費 ÷ 日額度 <0.5 省很多、≤1 正常、≤1.5 超額、>1.5 超超額（同舊試算表的叫法）。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from .. import clock
from . import periods, plans

PURPOSE_BUCKET = {"saving": "saving", "investing": "invest", "debt": "debt", "pension": "fixed"}
UNCATEGORIZED = "待分類"
MEAL_ORDER = ("breakfast", "lunch", "dinner")
WEEKDAYS = "一二三四五六日"


def day_status(total: int, daily_budget: float) -> dict[str, str]:
    if daily_budget <= 0:
        return {"text": "沒有日額度", "level": "none"}
    ratio = total / daily_budget
    if ratio > 1.5:
        return {"text": "超超額", "level": "crit"}
    if ratio > 1:
        return {"text": "超額", "level": "warn"}
    if ratio < 0.5:
        return {"text": "省很多", "level": "save"}
    return {"text": "正常", "level": "good"}


# 轉進帳戶的錢算哪一格（把資金操盤帳戶的做法推廣到任何綁了用途的帳戶）：
# 1. 帳戶自己綁了一格（accounts.bucket_id），那一格開著、不是生活費／還債 → 那一格（撥額度那一筆不算：滾存的 keep 已經在保留裡，算了會扣兩次；
#    代墊、收回、繳卡費是搬錢不是做到哪一格，也不算）；
# 2. 轉進「資金操盤」帳戶、資金操盤那一格開著 → trading（照舊）。
# 錢包與統計共用這一段（wallet.ROWS_SQL 也接它），要 JOIN：ta＝轉入帳戶、ab＝它綁的格子、tb＝trading 那一格。
TO_BUCKET_SQL = """CASE WHEN t.kind = 'transfer' AND ab.active = 1 AND ab.is_living = 0 AND ab.id <> 'debt'
            AND COALESCE(t.source, '') <> 'setaside' AND COALESCE(t.purpose, '') NOT IN ('lend', 'collect', 'card_payment') THEN ab.id
       WHEN t.kind = 'transfer' AND ta.asset_class = 'trading' AND tb.active = 1 THEN 'trading' END AS to_bucket"""
TO_BUCKET_JOINS = """LEFT JOIN accounts ta ON ta.id = t.to_account_id
LEFT JOIN buckets ab ON ab.id = ta.bucket_id
LEFT JOIN buckets tb ON tb.id = 'trading'"""

ROWS_SQL = f"""
SELECT t.id, t.date, t.time, t.kind, t.amount, t.financed, t.purpose, t.category_id, t.account_id, t.debt_id,
       t.description, t.for_period, t.nature, c.meal_slot,
       c.name AS category, g.name AS group_name, COALESCE(c.bucket_override, g.bucket_id) AS bucket_id, COALESCE(b.is_living, 0) AS is_living,
       {TO_BUCKET_SQL}
FROM transactions t
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN category_groups g ON g.id = c.group_id
LEFT JOIN buckets b ON b.id = COALESCE(c.bucket_override, g.bucket_id)
{TO_BUCKET_JOINS}
WHERE t.status = 'active' AND t.date BETWEEN ? AND ?
"""


TAGGED_SQL = ROWS_SQL.replace("t.date BETWEEN ? AND ?", "t.for_period = ? AND t.date NOT BETWEEN ? AND ?")


def allocation_rows(conn: sqlite3.Connection, period: Any, rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """算「薪水怎麼分」用的那一批：付款日在這一期、而且沒標別的期，加上別的日子付的、標了「算這一期」的（預繳／補繳）。
    現金還是照付款日算（錢包、明細不變），只有各用途做到多少看「算哪一期」。"""
    kept = [r for r in rows if not r["for_period"] or r["for_period"] == period.key]
    extra = conn.execute(TAGGED_SQL, (period.key, period.start.isoformat(), period.end.isoformat())).fetchall()
    return kept + list(extra)


def is_living(r: sqlite3.Row) -> bool:
    return r["kind"] == "expense" and not r["financed"] and (r["category_id"] is None or bool(r["is_living"]))


def bucket_of(r: sqlite3.Row) -> str | None:
    if r["kind"] == "expense":
        if r["financed"]:
            return None
        return r["bucket_id"] or ("living" if r["category_id"] is None else None)
    if r["kind"] == "transfer":
        if r["purpose"] == "debt" and r["debt_id"]:
            return None                      # 有連到排程：已經用排程算進負債償還
        # 轉進的帳戶綁了一格或是資金操盤帳戶 → 那一格；明說的帳戶優先，其次細項、最後照用途推
        return r["to_bucket"] or r["bucket_id"] or PURPOSE_BUCKET.get(r["purpose"] or "")
    return None


def range_rows(conn: sqlite3.Connection, start: date | str, end: date | str) -> list[sqlite3.Row]:
    return conn.execute(ROWS_SQL, (str(start), str(end))).fetchall()


CAPS_SQL = """
SELECT g.id AS group_id, g.name AS group_name, g.icon AS group_icon, c.id AS category_id, c.name AS category,
       c.icon AS icon, c.default_budget AS cap, COALESCE(c.cap_mode, c.cap_basis, 'period') AS cap_basis,
       COALESCE((SELECT SUM(t.amount) FROM transactions t
                 WHERE t.category_id = c.id AND t.status = 'active' AND t.kind = 'expense'
                   AND t.financed = 0 AND t.date BETWEEN ? AND ?), 0) AS spent,
       COALESCE((SELECT COUNT(*) FROM transactions t2
                 WHERE t2.category_id = c.id AND t2.status = 'active' AND t2.kind = 'expense'
                   AND t2.financed = 0 AND t2.date BETWEEN ? AND ?
                   AND c.default_budget IS NOT NULL AND t2.amount > c.default_budget), 0) AS over_each
FROM categories c
JOIN category_groups g ON g.id = c.group_id
LEFT JOIN buckets b ON b.id = COALESCE(c.bucket_override, g.bucket_id)
WHERE c.active = 1 AND g.active = 1 AND c.kind = 'expense' AND COALESCE(b.is_living, 0) = 1
ORDER BY g.sort, g.id, c.sort, c.id
"""


def caps(conn: sqlite3.Connection, period_key: str | None = None) -> list[dict[str, Any]]:
    """可以花的錢怎麼分：每個大類的**共用上限**與已花，底下每個細項的**單項上限**與已花。

    想法是「幾個項目各有上限，但合起來也不能超過一個額度」：大類＝共用上限（期計畫的預算），
    細項＝單項上限（categories.default_budget）。上限加起來可以大於共用上限（不是每項都會花）。

    單項上限有三種算法（`cap_mode`）：`period` 這一期總共、`day` 每天（例：午餐每天 150，乘天數）、
    `each` **每一次**（例：每一次晚餐 500）。每次的沒有期總量，改回報這一期有幾筆超過（`over_each`）。
    三種都是**軟上限**：不擋、不亮燈，記下去那一刻輕輕說一句，誇張的留給月檢討講。
    """
    period = periods.get(conn, period_key)
    plan = plans.get(conn, period.key)
    out: dict[str, dict[str, Any]] = {}
    start, end = period.start.isoformat(), period.end.isoformat()
    for r in conn.execute(CAPS_SQL, (start, end, start, end)):
        g = out.setdefault(r["group_name"], {
            "id": r["group_id"], "name": r["group_name"], "icon": r["group_icon"] or "",
            "budget": int(plan["group_budgets"].get(r["group_name"]) or 0), "spent": 0, "categories": []})
        g["spent"] += int(r["spent"] or 0)
        cap = int(r["cap"] or 0)
        mode = r["cap_basis"] or "period"
        # 每次：不換算成一期的總量（那是「每一次最多花多少」），改記這一期有幾筆超過
        per_period = 0 if mode == "each" else (cap * period.days if mode == "day" else cap)
        g["categories"].append({"id": r["category_id"], "name": r["category"], "icon": r["icon"] or "",
                                "cap": r["cap"], "cap_basis": mode, "cap_period": per_period or None,
                                "spent": int(r["spent"] or 0), "over_each": int(r["over_each"] or 0),
                                "over": bool(per_period) and int(r["spent"] or 0) > per_period})
    for g in out.values():
        g["cap_total"] = sum(int(c["cap_period"] or 0) for c in g["categories"])
        g["over"] = bool(g["budget"]) and g["spent"] > g["budget"]
        g["left"] = g["budget"] - g["spent"] if g["budget"] else 0
    for name, amount in plan["group_budgets"].items():        # 有上限但底下還沒有細項的大類也列出來
        if name not in out:
            out[name] = {"id": None, "name": name, "icon": "", "budget": int(amount or 0), "spent": 0,
                         "categories": [], "cap_total": 0, "over": False, "left": int(amount or 0)}
    return list(out.values())


def period_summary(conn: sqlite3.Connection, period_key: str | None = None) -> dict[str, Any]:
    period = periods.get(conn, period_key)
    plan = plans.get(conn, period.key)
    today = clock.today()
    rows = range_rows(conn, period.start, period.end)

    living = [r for r in rows if is_living(r)]
    spent = sum(r["amount"] for r in living)
    # 長期用（囤貨、3C…）：錢花了、算在生活費總額，但不算在每天的日曆，也不拿來判斷那天花得兇不兇
    stock = [r for r in living if r["nature"] == "stock"]
    stock_total = sum(r["amount"] for r in stock)
    daily_rows = [r for r in living if r["nature"] != "stock"]
    by_group: dict[str, int] = {}
    by_category: dict[str, int] = {}
    counts: dict[str, int] = {}
    for r in living:
        g = r["group_name"] or UNCATEGORIZED
        by_group[g] = by_group.get(g, 0) + r["amount"]
        c = r["category"] or UNCATEGORIZED
        by_category[c] = by_category.get(c, 0) + r["amount"]
        counts[c] = counts.get(c, 0) + 1

    budget = plan["living_budget"]
    effective = max(budget - stock_total, 0) if budget else 0          # 扣掉長期用的，每天才有的額度
    daily_budget = effective / period.days if effective else 0
    elapsed = period.elapsed(today)
    is_open = period.is_open(today)
    per_day: dict[str, int] = {}
    unsorted: dict[str, list[int]] = {}                       # 每天沒分類的：[金額, 筆數]，日曆上標「？」
    stock_day: dict[str, int] = {}
    meals: dict[str, set] = {}
    top: dict[str, tuple[int, str]] = {}
    for r in stock:
        stock_day[r["date"]] = stock_day.get(r["date"], 0) + r["amount"]
    for r in daily_rows:
        per_day[r["date"]] = per_day.get(r["date"], 0) + r["amount"]
        if r["category_id"] is None:
            u = unsorted.setdefault(r["date"], [0, 0])
            u[0] += r["amount"]
            u[1] += 1
        if r["meal_slot"]:
            meals.setdefault(r["date"], set()).add(r["meal_slot"])
        best = top.get(r["date"])
        if best is None or r["amount"] > best[0]:
            top[r["date"]] = (r["amount"], r["description"] or r["category"] or "")
    daily = []
    for i in range(period.days):
        d = period.start + timedelta(days=i)
        total = per_day.get(d.isoformat(), 0)
        future, is_today = d > today, d == today
        st = {"text": "還沒到", "level": "future"} if future else (
            {"text": "今天", "level": "today"} if is_today else day_status(total, daily_budget))
        biggest = top.get(d.isoformat())
        daily.append({"date": d.isoformat(), "weekday": "一二三四五六日"[d.weekday()], "total": total,
                      "meals": [m for m in MEAL_ORDER if m in meals.get(d.isoformat(), ())],
                      "top": {"label": biggest[1], "amount": biggest[0]} if biggest else None,
                      "unsorted": unsorted.get(d.isoformat(), [0, 0])[0], "unsorted_count": unsorted.get(d.isoformat(), [0, 0])[1],
                      "stock": stock_day.get(d.isoformat(), 0),
                      "future": future, "today": is_today, "status": st["text"], "level": st["level"]})

    remaining = budget - spent
    days_left = (period.days - elapsed + 1) if is_open else 0
    groups = []
    for name, gbudget in plan["group_budgets"].items():
        groups.append({"name": name, "spent": by_group.get(name, 0), "budget": gbudget})
    for name, amount in by_group.items():
        if name not in plan["group_budgets"]:
            groups.append({"name": name, "spent": amount, "budget": 0})

    buckets = {b: 0 for b in plans.bucket_ids(conn)}
    income = 0
    financed = 0
    for r in allocation_rows(conn, period, rows):
        if r["kind"] == "income":
            income += r["amount"]
            continue
        if r["kind"] == "expense" and r["financed"]:
            financed += r["amount"]
        b = bucket_of(r)
        if b:
            buckets[b] = buckets.get(b, 0) + r["amount"]     # 停用的用途也算（舊資料還在）
    from . import debts as debts_service
    debts_due = debts_service.due_between(conn, period.start.isoformat(), period.end.isoformat())
    buckets["debt"] = buckets.get("debt", 0) + sum(d["amount"] for d in debts_due)
    uncategorized = [r for r in living if r["category_id"] is None]
    return {
        "period": period.to_dict(today), "plan_source": plan["source"],
        "living": {
            "budget": budget, "spent": spent, "remaining": remaining,
            "daily_budget": round(daily_budget), "days_left": days_left,
            "per_day_left": round(max(remaining, 0) / days_left) if days_left else None,
            "pace": round(effective * elapsed / period.days) if effective else 0,
            "stock": stock_total, "daily_spent": spent - stock_total,
            "over_days": sum(1 for d in daily if d["level"] in ("warn", "crit")),
            "uncategorized_count": len(uncategorized), "uncategorized_amount": sum(r["amount"] for r in uncategorized),
        },
        "groups": groups,
        "categories": [{"name": k, "spent": v, "count": counts[k]} for k, v in
                       sorted(by_category.items(), key=lambda kv: -kv[1])],
        "daily": daily,
        "allocation": {"income": income, "income_expected": plan["income_expected"], "plan": plan["buckets"],
                       "actual": buckets, "financed": financed,
                       "debts_due": [{"name": d["name"], "no": d["no"], "amount": d["amount"], "due_date": d["due_date"],
                                      "status": d["status"]} for d in debts_due]},
    }


def weekday_pattern(conn: sqlite3.Connection, *, weeks: int = 8, today: date | None = None) -> dict[str, Any] | None:
    """最近幾週「星期幾平均花最多」（實驗性）。資料太少、或沒有哪一天特別突出，就回 None——
    寧可不講，也不要講一句沒把握的話（要的是看得懂、信得過的一句話）。"""
    today = today or clock.today()
    start = today - timedelta(days=weeks * 7 - 1)
    rows = [r for r in range_rows(conn, start, today) if is_living(r) and r["nature"] != "stock"]
    if len({r["date"] for r in rows}) < 14:                   # 記不到兩週：不分析
        return None
    totals = {i: 0 for i in range(7)}
    counts = {i: 0 for i in range(7)}
    for r in rows:
        totals[date.fromisoformat(r["date"]).weekday()] += r["amount"]
    day = start
    while day <= today:
        counts[day.weekday()] += 1
        day += timedelta(days=1)
    avg = {i: totals[i] / counts[i] for i in range(7) if counts[i]}
    pick = max(avg, key=lambda i: avg[i])
    others = [v for i, v in avg.items() if i != pick]
    rest = sum(others) / len(others) if others else 0
    if not avg[pick] or avg[pick] < rest * 1.3:               # 沒有特別突出：不要亂講
        return None
    return {"weekday": WEEKDAYS[pick], "avg": round(avg[pick]), "others": round(rest), "weeks": weeks}


def text_summary(s: dict[str, Any]) -> str:
    p, lv = s["period"], s["living"]
    head = f"{p['key']} 期（{p['start'][5:]}–{p['end'][5:]}，第 {p['elapsed']}/{p['days']} 天）"
    if not lv["budget"]:
        return f"{head}生活支出 {lv['spent']:,} 元（還沒設預算）。"
    tail = (f"還能花 {lv['remaining']:,}，之後每天 {lv['per_day_left']:,}" if p["open"] and lv["per_day_left"] is not None
            else (f"結餘 {lv['remaining']:,}" if lv["remaining"] >= 0 else f"超支 {-lv['remaining']:,}"))
    return f"{head}生活支出 {lv['spent']:,} / {lv['budget']:,}，{tail}。"
