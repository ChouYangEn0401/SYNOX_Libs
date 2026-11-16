"""網站要的整包資料：首頁（這個月）、一年、存摺。這裡只組裝，計算在 stats／wallet／health。

首頁一個中心：「生活費還可以花多少」；旁邊是錢包（存摺餘額、要保留的、可以自由用的）、
用途（計畫、用了、剩；照資料庫，使用者自己加的也會出現）、這一期的日曆（每天花多少、還有幾天發薪）、
分期（第幾期／共幾期、每月多少、還剩多少）、健康檢查的燈號、**可能記重複的那幾組**（在網站上確認）、
最近一份月報。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from .. import clock
from . import (cardcash, conflicts, debts, dedup, envelopes, estimate, health, periods, piggy, plans, refdata,
                                reviews, settings, stats, wallet)
from . import moves                  # 額度挪用；分開一行，合併時不跟上面那行打架
from .views import period_nav

def _calendar(conn: sqlite3.Connection, s: dict[str, Any], *, w: dict[str, Any] | None = None,
              cards: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """這一期的日曆：像月曆一樣一眼看出每天花了多少、離下次發薪日還有幾天。

    每天：花了多少、紅黃綠、有沒有記到三餐、那天最大一筆是什麼。
    另外給：本來每天可以花多少、**現在**剩下的天數每天可以花多少（花超了會看到這個數字變小）、
    還有幾天發薪、最近「星期幾最會花」（沒把握就不給）。
    `estimate`：依手上真的有的錢估每天可以花多少（services/estimate；只有進行中的期，不寫資料庫）。
    """
    p, lv = s["period"], s["living"]
    today = clock.today()
    end = date.fromisoformat(p["end"])
    unsorted = {"amount": sum(d["unsorted"] for d in s["daily"]), "count": sum(d["unsorted_count"] for d in s["daily"])}
    salary = _salary_cycle(conn, p)
    return {"days": s["daily"], "daily_budget": lv["daily_budget"], "per_day_left": lv["per_day_left"],
            "stock": lv["stock"], "unsorted": unsorted,       # 長期用（不算在每天）與沒分類的（顏色還沒定）
            "days_left": lv["days_left"], "payday": (end + timedelta(days=1)).isoformat(),
            "days_to_payday": max((end - today).days + 1, 0) if p["open"] else 0,
            "pattern": stats.weekday_pattern(conn), "salary": salary,
            "estimate": estimate.for_period(conn, p["key"], wallet_summary=w, cards=cards, living=lv, salary=salary)}


def _salary_cycle(conn: sqlite3.Connection, p: dict[str, Any]) -> dict[str, Any]:
    """薪水週期：每月照日期換期，但要看得到這一期的薪水到了沒、比起算日晚多久。
    換期只看 period_start_day；薪水延後不影響，這裡只回報事實：到了＝哪天、多少、比起算日晚幾天。"""
    start = date.fromisoformat(p["start"])
    row = conn.execute("SELECT t.date, t.amount FROM transactions t JOIN categories c ON c.id = t.category_id "
                       "WHERE t.status = 'active' AND t.kind = 'income' AND c.system_key = 'salary' AND t.date BETWEEN ? AND ? "
                       "ORDER BY t.date, t.time LIMIT 1", (p["start"], p["end"])).fetchone()
    if row:
        d = date.fromisoformat(row["date"])
        return {"arrived": True, "date": row["date"], "amount": row["amount"], "start": p["start"], "late_days": (d - start).days}
    today = clock.today()
    return {"arrived": False, "start": p["start"], "waiting_days": max((today - start).days, 0) if p["open"] else 0}


def _debts(conn: sqlite3.Connection, income: int) -> dict[str, Any]:
    """分期走到第幾期：已經到期（或記成已付）的期數；還剩：還沒到期、也還沒付的。"""
    today = clock.today().isoformat()
    items = []
    for d in debts.all_debts(conn):
        rows = [r for r in d["schedule"] if r["status"] != "skipped"]
        passed = [r for r in rows if r["status"] == "paid" or r["due_date"] <= today]
        ahead = [r for r in rows if r not in passed]
        if not ahead:
            continue                                          # 全部到期了：不再是「還在還」的分期
        items.append({"id": d["id"], "name": d["name"], "kind": d["kind_label"], "paid": len(passed),
                      "periods": d["periods"], "per_period": d["per_period"], "remaining": sum(r["amount"] for r in ahead),
                      "next_date": ahead[0]["due_date"], "next_no": ahead[0]["no"]})
    monthly = sum(i["per_period"] for i in items)
    return {"items": items, "monthly": monthly, "remaining": sum(i["remaining"] for i in items),
            "share": round(monthly / income, 3) if income and monthly else None}


def _month_end(conn: sqlite3.Connection, env: list[dict[str, Any]], cards: list[dict[str, Any]], w: dict[str, Any],
               lv: dict[str, Any]) -> dict[str, Any]:
    """月底要留在帳戶的錢、可以轉出去的結餘（常見用法：每月結餘轉到另一個帳戶，原帳戶只留滾存＋信用卡費用）。

    - pots：滾存的格子還留著、等帳單的錢（水電瓦斯、手機費）；
    - card_keep：銀行要留給卡費的（這期還沒繳＋下期已經刷的；刷卡時錢包已經扣過，所以不在「多出來」裡）；
    - surplus：多出來的＝錢包 − 要留（還沒做完的格子，含滾存）− 生活費還剩的，就是可以轉去存錢帳戶的那筆；
    - to：可以轉進去的帳戶（扣卡費的那個之外的銀行帳戶，優先有目標或底線的）；沒有就是 None，畫面上叫你先加一個。"""
    pots = [{"id": e["id"], "name": e["name"], "amount": e["keep"]} for e in env if e.get("rollover")]
    card_keep = sum(int(c.get("keep") or 0) for c in cards if c.get("ready"))
    living_left = max(int(lv["remaining"] or 0), 0) if lv["budget"] else 0
    surplus = w["wallet"] - w["reserved"] - living_left
    banks = [a for a in refdata.accounts(conn) if a["kind"] == "bank"]
    saving_like = lambda a: bool(a["floor_amount"] or a["target_amount"] or a.get("reserve_group"))   # noqa: E731 — 有目標、底線的是存錢用的
    default = settings.get(conn, "default_account")
    home_bank = (next((c.get("bank") for c in cards if c.get("ready") and c.get("bank")), None)     # 扣卡費的那個＝薪水進來、留滾存的
                 or next((a["name"] for a in banks if a["name"] == default), None)
                 or next((a["name"] for a in banks if not saving_like(a)), None)
                 or (banks[0]["name"] if banks else None))
    savers = sorted((a for a in banks if a["name"] != home_bank), key=lambda a: (not saving_like(a), a.get("sort") or 0))
    return {"pots": pots, "pots_total": sum(p["amount"] for p in pots), "card_keep": card_keep, "surplus": surplus,
            "from": home_bank, "to": savers[0]["name"] if savers else None}


def _review(conn: sqlite3.Connection) -> dict[str, Any] | None:
    rv = reviews.latest(conn, "month")
    if not rv:
        return None
    s = rv.get("suggestion") or {}
    return {"id": rv["id"], "grade": rv["grade"], "title": rv["title"], "label": rv["label"],
            "points": [p["text"] for p in ((rv["good"] or [])[:1] + (rv["watch"] or [])[:1] + (rv["strategy"] or [])[:1])],
            "can_apply": bool(s.get("valid")) and not rv["applied_period"], "apply_period": s.get("period"),
            "applied_period": rv["applied_period"]}


def home(conn: sqlite3.Connection, period_key: str | None = None) -> dict[str, Any]:
    nav = period_nav(conn, period_key)
    key = nav["period"]["key"]
    s = stats.period_summary(conn, key)
    plan = plans.get(conn, key)
    lv = s["living"]
    income = s["allocation"]["income"] or plan["income_expected"] or 0
    planned = sum(int(v or 0) for v in plan["buckets"].values())
    w = wallet.summary(conn, key)
    items = health.checks(conn, key, wallet_summary=w)
    env = envelopes.rows(conn, plan, s["allocation"]["actual"], periods.get(conn, key))
    cards = cardcash.all_cards(conn)
    has_txn = conn.execute("SELECT 1 FROM transactions WHERE status = 'active' LIMIT 1").fetchone() is not None
    return {**nav,
            "living": {"budget": lv["budget"], "spent": lv["spent"], "left": lv["remaining"],
                       "per_day_left": lv["per_day_left"], "days_left": lv["days_left"], "pace": lv["pace"],
                       "follows": plan["living_follows"]},        # 生活費是不是「大類加起來」（不是自己填的）
            "wallet": w,
            "plan": {"income": income, "income_actual": s["allocation"]["income"], "income_expected": plan["income_expected"],
                     "planned": planned, "unassigned": income - planned if income else 0, "source": plan["source"]},
            "envelopes": env,
            "moves": moves.summary(conn, key),                # 這期的額度挪用：清單＋統計（沒有就不畫）
            "month_end": _month_end(conn, env, cards, w, lv),
            "calendar": _calendar(conn, s, w=w, cards=cards),
            "debts": _debts(conn, income),
            "advance": debts.pending_advances(conn),          # 代墊還沒收回的（燈號那一行「處理報帳」用）
            "cards": cards,
            "health": items, "health_level": health.headline(items),
            "conflicts": conflicts.payload(conflicts.scan(conn, cards=cards)),    # 帳裡對不上、要你去處理的（只標出來，不自動解決）
            "duplicates": dedup.pending(conn)[:5],            # 可能記重複的，直接在總覽上處理
            "review": _review(conn),
            "piggy": piggy.brief(conn) if nav["period"]["open"] else None,   # 撲滿待處理＋目前 vs 基準紅線
            "start": {"has_plan": not (plan["source"] == "default" and not plan["income_expected"]),
                      "has_transactions": has_txn}}


def year(conn: sqlite3.Connection, year_number: int | None = None) -> dict[str, Any]:
    """一年 12 期：收入、花掉（六個用途合計）、生活費 vs 預算、存下來（存錢＋投資）。還沒到的期留空。"""
    today = clock.today()
    current = periods.current(conn).key
    y = year_number or int(current[:4])
    months = []
    for m in range(1, 13):
        key = f"{y}-{m:02d}"
        if key > current:
            months.append({"key": key, "month": m, "future": True})
            continue
        s = stats.period_summary(conn, key)
        a = s["allocation"]
        months.append({"key": key, "month": m, "future": False, "income": a["income"],
                       "used": sum(a["actual"].values()), "living": s["living"]["spent"],
                       "living_budget": s["living"]["budget"], "saved": a["actual"].get("saving", 0) + a["actual"].get("invest", 0),
                       "over": bool(s["living"]["budget"]) and s["living"]["spent"] > s["living"]["budget"]})
    done = [x for x in months if not x["future"]]
    first = conn.execute("SELECT MIN(date) FROM transactions WHERE status = 'active'").fetchone()[0]
    return {"year": y, "today": today.isoformat(), "months": months,
            "income": sum(x["income"] for x in done), "used": sum(x["used"] for x in done),
            "saved": sum(x["saved"] for x in done), "living": sum(x["living"] for x in done),
            "over_months": sum(1 for x in done if x["over"]),
            "prev": y - 1 if first and int(first[:4]) < y else None, "next": y + 1 if y < int(current[:4]) else None}
