"""依你現在的錢估算每天可以花多少。

設計：每個月薪水進來後要重算之後每天的生活額度，算法是
[剩下的錢] − [一定跑不掉的固定支出（卡費、訂閱、房租、…）]，再攤到剩下的天數。

計畫的每日額度（stats.living 的 per_day_left）只看預算；這裡看**手上真的有多少錢**：

    可以用的錢 pool ＝ 現金帳戶現在的錢 − 還沒做完的用途要留的（wallet.summary 的 reserved：固定、捐贈、存錢、
                       滾存、還沒到期的分期…）− 信用卡要預留的（cardcash 的 keep：這期還沒繳＋下期已刷）
    每天估算 ＝ max(pool, 0) ÷ 這期剩下的天數（含今天）

口徑跟撲滿現金層「分到最後剩下的」（piggy.cash_view）是**同一套**：那邊是
leftover ＝ 現金 − 要留的 − 生活費還剩的 − 卡費要留的，所以 pool ＝ leftover ＋ 生活費還剩的（測試會驗）。
為什麼不用錢包的「可以自由用」再扣卡費：錢包在**刷卡那一刻**就扣了（見 cardcash 開頭），再扣卡費會扣兩次；
現金帳戶的餘額是**繳費那天**才少，所以要扣卡費——撲滿用的就是這個，兩邊才對得起來。

**不寫進資料庫**（要不要存、怎麼存還沒定）：`compute()` 是純函式，之後要存快照只要把它的結果存起來。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import clock
from . import cardcash, periods, piggy, wallet

WARN_BELOW = 0.8          # 估出來的比計畫少兩成以上＝黃字提醒（多的不提醒：多出來不是壞事）


def compute(*, cash_total: int, unverified: int, reserved_items: list[dict[str, Any]],
            card_items: list[dict[str, Any]], days_left: int, plan_per_day: int | None,
            salary: dict[str, Any] | None) -> dict[str, Any]:
    """純函式：給數字就算，不碰資料庫。reasons 是畫面展開用的白話（扣掉了什麼、各多少）。"""
    reserved = sum(int(r["amount"]) for r in reserved_items)
    card_keep = sum(int(c["keep"]) for c in card_items)
    pool = cash_total - reserved - card_keep
    per_day = round(max(pool, 0) / days_left) if days_left > 0 else None
    arrived = bool(salary and salary.get("arrived"))
    if arrived:
        basis, label = "after_salary", f"薪水 {_md(salary['date'])} 入帳後重算"
    else:
        basis, label = "before_salary", "薪水還沒進來，這個估算是用上期剩的錢"
    reasons = [{"text": "現金和銀行帳戶現在一共有" + (f"（{unverified} 個帳戶還沒核對過餘額，是用記帳推算的）" if unverified else ""),
                "amount": cash_total}]
    reasons += [{"text": f"留給「{r['name']}」（還沒做完）", "amount": -int(r["amount"])} for r in reserved_items if r["amount"]]
    for c in card_items:
        if c["keep"]:
            parts = [f"這期帳單還沒繳 {c['bill']:,}" if c.get("bill") else "", f"下期已刷 {c['next']:,}" if c.get("next") else ""]
            reasons.append({"text": f"{c['account']} 要預留的卡費（" + "＋".join(p for p in parts if p) + "）", "amount": -int(c["keep"])})
    reasons.append({"text": "剩下可以用的", "amount": pool})
    if per_day is not None:
        reasons.append({"text": f"÷ 這期還有 {days_left} 天（含今天）＝每天", "amount": per_day})
    level = "ok"
    if pool < 0 or (per_day is not None and plan_per_day and per_day < plan_per_day * WARN_BELOW):
        level = "warn"
    return {"per_day": per_day, "pool": pool, "days_left": days_left, "plan_per_day": plan_per_day,
            "cash": cash_total, "reserved": reserved, "card_keep": card_keep, "unverified": unverified,
            "basis": basis, "label": label, "level": level, "reasons": reasons}


def _md(iso: str) -> str:
    return f"{int(iso[5:7])}/{int(iso[8:10])}"


def for_period(conn: sqlite3.Connection, period_key: str | None = None, *, wallet_summary: dict | None = None,
               cards: list[dict[str, Any]] | None = None, living: dict[str, Any] | None = None,
               salary: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """進行中的那一期才估（過去的期沒有「之後每天」）；home 已經算好的東西可以傳進來，不用再算一次。"""
    from . import stats
    p = periods.get(conn, period_key)
    today = clock.today()
    if not p.is_open(today):
        return None
    w = wallet_summary or wallet.summary(conn, p.key)
    lv = living or stats.period_summary(conn, p.key)["living"]
    if salary is None:
        from .home import _salary_cycle    # 薪水到了沒：跟總覽同一個判斷（避免循環匯入，用到才載入）
        salary = _salary_cycle(conn, p.to_dict(today))
    cash = [x for x in piggy.levels(conn, today.isoformat()) if x["key"] == "cash"][0]
    ready = [c for c in (cards if cards is not None else cardcash.all_cards(conn)) if c.get("ready")]
    card_items = [{"account": c["account"], "bill": int((c.get("bill") or {}).get("left") or 0),
                   "next": int((c.get("next") or {}).get("amount") or 0), "keep": int(c.get("keep") or 0)} for c in ready]
    return compute(cash_total=cash["total"], unverified=cash["unverified"], reserved_items=w["reserved_items"],
                   card_items=card_items, days_left=int(lv["days_left"] or 0), plan_per_day=lv["per_day_left"],
                   salary=salary)


def text(e: dict[str, Any] | None) -> str | None:
    """CLI／推送用的一行。"""
    if not e or e["per_day"] is None:
        return None
    plan = f"（計畫是 {e['plan_per_day']:,}）" if e["plan_per_day"] else ""
    return f"依現在的錢估：每天可以花 {e['per_day']:,}{plan}；{e['label']}"
