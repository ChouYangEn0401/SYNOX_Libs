"""週報／月報的數字：花了多少、跟預算和上一段比、各大類、最大筆、常去的店、每日狀態、存錢與水位。

- 週＝ISO 週（一到日），月＝期（5 號起算）。週的預算＝那 7 天各自所屬那一期的日額度加總（週可能跨兩期）。
- 生活支出的定義跟 stats 一樣；月報另外帶六大類實際 vs 計畫（stats.period_summary）。
- 檢討把這份整包交給 AI（送數字、品項、商家、小日記；不送收據圖和帳號），也存成 reviews.metrics 的快照。
- build() 只讀不寫（periods.period_for 補期別列不算帳務寫入，跟其他讀取一樣）。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .. import clock
from . import balances, inbox, invest, notes, periods, plans, settings, stats

KINDS = ("week", "month")
KIND_NAMES = {"week": "週報", "month": "月報"}
TXN_LIMIT = 150

DETAIL_SQL = """
SELECT t.id, t.date, t.time, t.kind, t.amount, t.financed, t.purpose, t.category_id, t.description, t.merchant,
       t.debt_id, t.nature, c.name AS category, g.name AS group_name, COALESCE(c.bucket_override, g.bucket_id) AS bucket_id, COALESCE(b.is_living, 0) AS is_living,
       a.name AS account
FROM transactions t
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN category_groups g ON g.id = c.group_id
LEFT JOIN buckets b ON b.id = COALESCE(c.bucket_override, g.bucket_id)
JOIN accounts a ON a.id = t.account_id
WHERE t.status = 'active' AND t.date BETWEEN ? AND ?
ORDER BY t.date, t.time, t.id
"""


def resolve(conn: sqlite3.Connection, kind: str, key: str | None = None) -> dict[str, Any]:
    """kind＋key → 範圍。key 不給：週＝這一週、月＝這一期（看「到目前為止」）。"""
    if kind == "week":
        key = key or periods.week_key(clock.today())
        start, end = periods.week_range(key)
        label = f"{key}（{start.month}/{start.day}–{end.month}/{end.day}）"
    elif kind == "month":
        p = periods.get(conn, key)
        key, start, end = p.key, p.start, p.end
        label = f"{key} 期（{start.month}/{start.day}–{end.month}/{end.day}）"
    else:
        from ..errors import InvalidInput
        raise InvalidInput(f"報表種類「{kind}」不對", hint="week 或 month")
    return {"kind": kind, "key": key, "start": start, "end": end, "label": label, "days": (end - start).days + 1}


def previous_key(conn: sqlite3.Connection, kind: str, key: str) -> str:
    if kind == "week":
        start, _ = periods.week_range(key)
        return periods.week_key(start - timedelta(days=7))
    return periods.shift(conn, periods.get(conn, key), -1).key


def _daily_budgets(conn: sqlite3.Connection, start: date, end: date) -> dict[str, float]:
    """每一天的日額度（那天所屬那一期的生活預算 ÷ 天數）。"""
    out: dict[str, float] = {}
    cache: dict[str, float] = {}
    d = start
    while d <= end:
        p = periods.period_for(conn, d)
        if p.key not in cache:
            budget = plans.get(conn, p.key)["living_budget"]
            cache[p.key] = budget / p.days if budget else 0
        out[d.isoformat()] = cache[p.key]
        d += timedelta(days=1)
    return out


def _living(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return [r for r in rows if stats.is_living(r)]


def _group_totals(rows: list[sqlite3.Row]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        g = r["group_name"] or stats.UNCATEGORIZED
        out[g] = out.get(g, 0) + r["amount"]
    return out


def _label(r: sqlite3.Row) -> str:
    return (r["merchant"] or r["description"] or r["category"] or "（沒寫內容）").strip()


def build(conn: sqlite3.Connection, kind: str, key: str | None = None, *, include_notes: bool | None = None,
          with_transactions: bool = True) -> dict[str, Any]:
    rng = resolve(conn, kind, key)
    start, end = rng["start"], rng["end"]
    today = clock.today()
    rows = conn.execute(DETAIL_SQL, (start.isoformat(), end.isoformat())).fetchall()
    living = _living(rows)
    spent = sum(r["amount"] for r in living)
    budgets = _daily_budgets(conn, start, end)
    budget = round(sum(budgets.values()))
    closed = end < today
    elapsed = rng["days"] if closed else max(0, (min(today, end) - start).days + 1)

    # 各大類：花費、預算（週＝日額度比例分攤）、跟上一段比
    prev_key = previous_key(conn, kind, rng["key"])
    prev = resolve(conn, kind, prev_key)
    prev_rows = _living(conn.execute(DETAIL_SQL, (prev["start"].isoformat(), prev["end"].isoformat())).fetchall())
    prev_groups = _group_totals(prev_rows)
    by_group = _group_totals(living)
    group_budgets: dict[str, float] = {}
    for day, daily in budgets.items():
        p = periods.period_for(conn, date.fromisoformat(day))
        plan = plans.get(conn, p.key)
        if plan["living_budget"]:
            for g, amount in plan["group_budgets"].items():
                group_budgets[g] = group_budgets.get(g, 0) + amount / p.days
    names = list(dict.fromkeys([*group_budgets, *by_group]))
    groups = [{"name": g, "spent": by_group.get(g, 0), "budget": round(group_budgets.get(g, 0)),
               "share": round(by_group.get(g, 0) / spent, 3) if spent else 0,
               "previous": prev_groups.get(g, 0), "change": by_group.get(g, 0) - prev_groups.get(g, 0)}
              for g in names if by_group.get(g) or group_budgets.get(g)]
    groups.sort(key=lambda x: -x["spent"])

    # 每日：花費與狀態（同舊表的省很多／正常／超額／超超額）
    per_day: dict[str, int] = {}
    for r in living:
        if r["nature"] == "stock":
            continue                                          # 長期用不算在每一天的狀態
        per_day[r["date"]] = per_day.get(r["date"], 0) + r["amount"]
    daily = []
    for day, daily_budget in budgets.items():
        if date.fromisoformat(day) > today:
            break
        st = stats.day_status(per_day.get(day, 0), daily_budget)
        daily.append({"date": day, "total": per_day.get(day, 0), "budget": round(daily_budget), "status": st["text"],
                      "level": st["level"]})
    levels = [d["level"] for d in daily]

    # 最大筆、常去的店（同一個店名或內容合起來）
    top = sorted(living, key=lambda r: -r["amount"])[:5]
    places: dict[str, dict[str, Any]] = {}
    for r in living:
        name = _label(r)
        item = places.setdefault(name, {"name": name, "amount": 0, "count": 0, "category": r["category"]})
        item["amount"] += r["amount"]
        item["count"] += 1
    frequent = sorted(places.values(), key=lambda x: (-x["count"], -x["amount"]))[:8]

    other_expense = [r for r in rows if r["kind"] == "expense" and not stats.is_living(r)]
    income = sum(r["amount"] for r in rows if r["kind"] == "income")
    out: dict[str, Any] = {
        **{k: v for k, v in rng.items() if k not in ("start", "end")},
        "start": start.isoformat(), "end": end.isoformat(), "closed": closed, "elapsed": elapsed,
        "living": {"spent": spent, "budget": budget, "remaining": budget - spent, "count": len(living),
                   "stock": sum(r["amount"] for r in living if r["nature"] == "stock"),
                   "per_day": round(spent / elapsed) if elapsed else 0,
                   "daily_budget": round(budget / rng["days"]) if budget else 0,
                   "uncategorized": sum(1 for r in living if r["category_id"] is None)},
        "previous": {"key": prev_key, "label": prev["label"], "spent": sum(r["amount"] for r in prev_rows),
                     "change": spent - sum(r["amount"] for r in prev_rows)},
        "groups": groups,
        "day_stats": {"over": levels.count("warn") + levels.count("crit"), "way_over": levels.count("crit"),
                 "saved": levels.count("save"), "zero": sum(1 for d in daily if d["total"] == 0), "counted": len(daily)},
        "daily": daily,
        "top": [{"date": r["date"], "desc": _label(r), "category": r["category"] or stats.UNCATEGORIZED,
                 "amount": r["amount"]} for r in top],
        "frequent": frequent,
        "other_expense": sum(r["amount"] for r in other_expense),
        "income": income,
        "count": len(rows),
        "reserves": [{"name": x["name"], "balance": x["balance"], "floor": x["floor"], "target": x["target"],
                      "level": x["level"], "note": x["extra"]} for x in balances.reserves(conn)],
        "inbox": inbox.counts(conn),
    }
    if kind == "month":
        s = stats.period_summary(conn, rng["key"])
        alloc = s["allocation"]
        plan = plans.get(conn, rng["key"])
        out["allocation"] = {"income": alloc["income"], "income_expected": plan["income_expected"],
                             "plan": plan["buckets"], "actual": alloc["actual"], "financed": alloc["financed"],
                             "debts_due": alloc["debts_due"]}
        rolling = {b["id"]: {"name": b["name"], "carry": plans.rollover_carry(conn, b["id"], rng["key"])}
                   for b in plans.buckets(conn) if b["rollover"]}
        for bid, r in rolling.items():                           # 滾存的格子（水電瓦斯）：這期能用的是撥的＋前期留下的，不是只有 plan
            r["available"] = int(plan["buckets"].get(bid) or 0) + r["carry"]
        if rolling:
            out["allocation"]["rollover"] = rolling
        skipped = sorted(plans.skips(conn, rng["key"]))
        if skipped:                                               # 使用者決定這期不做的（不是忘了做）
            out["allocation"]["skipped"] = skipped
        flex = [b["id"] for b in plans.buckets(conn) if b.get("flex")]
        if flex:                                                  # 金額會浮動的格子：差在容許範圍內算做完，檢討不要唸
            out["allocation"]["flex"] = {"buckets": flex, "tolerance": plans.flex_tolerance(conn)}
        out["plan_source"] = plan["source"]
        months = {m["month"]: m for m in invest.summary(conn)["months"]}
        if rng["key"][:7] in months:
            m = months[rng["key"][:7]]
            out["invest"] = {"month": m["month"], "pnl": m["pnl"], "note": m["note"], "target": m["target"]}
    if include_notes is None:
        include_notes = bool(settings.get(conn, "review.include_notes", True))
    if include_notes:
        out["notes"] = notes.between(conn, start, end)
    if with_transactions:
        out["transactions"] = [
            {"date": r["date"], "time": r["time"], "desc": _label(r), "category": r["category"] or stats.UNCATEGORIZED,
             "group": r["group_name"] or stats.UNCATEGORIZED, "amount": r["amount"], "account": r["account"]}
            for r in living[:TXN_LIMIT]]
        out["transactions_truncated"] = max(0, len(living) - TXN_LIMIT)
    return out


def decimals_of(conn: sqlite3.Connection) -> int:
    """帳本的小數位數（0～3；壞掉的值當 0）。"""
    try:
        return max(0, min(3, int(settings.value(conn, "currency.decimals") or 0)))
    except (TypeError, ValueError):
        return 0


def money(n: int | float, places: int = 0, *, signed: bool = False) -> str:
    """最小單位 → 給人看的金額（千分位；小數位數 0 時跟 f"{n:,}" 一樣）。只管格式，不改計算。"""
    if not places:
        return f"{n:+,}" if signed else f"{n:,}"
    sign = "+" if signed and n > 0 else ""
    return sign + f"{Decimal(int(round(n))) / (Decimal(10) ** places):,.{places}f}"


def text(rep: dict[str, Any], places: int = 0) -> str:
    """給人看的摘要（聊天助理回覆）。places：帳本的小數位數（decimals_of(conn)）；金額是最小單位，照它印。"""
    lv = rep["living"]
    name = KIND_NAMES[rep["kind"]]
    head = f"{name} {rep['label']}" + ("" if rep["closed"] else f"（到今天，第 {rep['elapsed']}/{rep['days']} 天）")
    lines = [head]
    if lv["budget"]:
        state = f"剩 {money(lv['remaining'], places)}" if lv["remaining"] >= 0 else f"超出 {money(-lv['remaining'], places)}"
        lines.append(f"生活支出 {money(lv['spent'], places)} / 預算 {money(lv['budget'], places)}，{state}；平均每天 {money(lv['per_day'], places)}")
    else:
        lines.append(f"生活支出 {money(lv['spent'], places)}（沒有預算）；平均每天 {money(lv['per_day'], places)}")
    pv = rep["previous"]
    if pv["spent"]:
        lines.append(f"比上一段（{pv['label']}）{'多' if pv['change'] > 0 else '少'} {money(abs(pv['change']), places)}")
    d = rep["day_stats"]
    lines.append(f"超額 {d['over']} 天（超超額 {d['way_over']}）、省很多 {d['saved']} 天、沒花錢 {d['zero']} 天")
    for g in rep["groups"][:6]:
        budget = f" / {money(g['budget'], places)}" if g["budget"] else ""
        change = f"（{money(g['change'], places, signed=True)}）" if pv["spent"] else ""          # 上一段沒資料就不比
        lines.append(f"  {g['name']}：{money(g['spent'], places)}{budget}{change}")
    if rep["top"]:
        lines.append("最大筆：" + "、".join(f"{t['desc']} {money(t['amount'], places)}" for t in rep["top"][:3]))
    if rep["frequent"]:
        lines.append("最常：" + "、".join(f"{f['name']}×{f['count']}" for f in rep["frequent"][:3]))
    if "allocation" in rep:
        a = rep["allocation"]
        lines.append(f"收入 {money(a['income'], places)}；存錢（存錢＋投資）實際 {money(a['actual'].get('saving', 0) + a['actual'].get('invest', 0), places)}"
                     f" / 計畫 {money(a['plan'].get('saving', 0) + a['plan'].get('invest', 0), places)}")
    low = [r for r in rep["reserves"] if r["level"] == "crit"]
    if low:
        lines.append("低於底線：" + "、".join(r["name"] for r in low))
    if lv["uncategorized"]:
        lines.append(f"有 {lv['uncategorized']} 筆待分類")
    return "\n".join(lines)
