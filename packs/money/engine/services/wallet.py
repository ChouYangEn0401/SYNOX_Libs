"""錢包：像存摺一樣一筆一行、累計餘額；要保留的錢、可以自由用的錢。

錢包＝收入還沒用掉的錢（不分帳戶）。新帳本從 0 開始（設定 wallet.opening 可以給起始金額），
可以是負的——薪水還沒進來就先花了，薪水一進來就補回來。每一筆對錢包的影響：
- 收入 ＋；支出 −（退款存成負的支出，所以是 ＋）；
- 分期或借錢買的東西 0：錢是之後一期一期扣，**每一期到期那天才扣**（像存摺上的分期扣款）；
- 轉帳：借給別人 −、收回 ＋；存錢、投資、勞退、沒連到排程的還款、轉進綁了用途的帳戶 −（錢放到別的地方了）；
  繳卡費、自己帳戶互轉 0（刷卡那時已經扣過，錢還在自己口袋）：存摺上照樣列一行（灰的、餘額不變），
  錢從哪裡到哪裡才看得到；
  連到排程的還款 0（那一期到期時已經扣過）。
保留＝這一期還沒做完的用途（生活費以外的：捐贈、固定、存錢、投資、還債；計畫 − 已用，不小於 0）
＋這一期還沒到期的分期。用途清單與名字從資料庫來（plans.buckets）。
自由可用＝錢包 − 保留：真的可以拿去花、不會動到要留的錢。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from .. import clock
from . import labels, periods, plans, settings, stats


ROWS_SQL = f"""
SELECT t.id, t.date, t.time, t.kind, t.amount, t.financed, t.purpose, t.category_id, t.debt_id,
       t.description, t.merchant, t.counterparty, t.note AS memo, t.for_period, t.nature, c.name AS category, g.name AS group_name, COALESCE(c.bucket_override, g.bucket_id) AS bucket_id,
       COALESCE(b.is_living, 0) AS is_living, a.name AS account, a.kind AS account_kind, ta.name AS to_account,
       {stats.TO_BUCKET_SQL}
FROM transactions t
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN category_groups g ON g.id = c.group_id
LEFT JOIN buckets b ON b.id = COALESCE(c.bucket_override, g.bucket_id)
JOIN accounts a ON a.id = t.account_id
{stats.TO_BUCKET_JOINS}
WHERE t.status = 'active' AND t.date {{cond}}
ORDER BY t.date, COALESCE(t.time, ''), t.rowid
"""

DUES_SQL = """
SELECT s.no, s.amount, s.due_date, s.status, d.name, d.periods, d.kind
FROM debt_schedule s JOIN debts d ON d.id = s.debt_id
WHERE d.active = 1 AND s.status <> 'skipped' AND s.due_date {cond}
ORDER BY s.due_date, d.id, s.no
"""


def effect(r: sqlite3.Row | dict) -> int:
    """這一筆讓錢包多（＋）或少（−）多少。"""
    kind, amount = r["kind"], r["amount"]
    if kind == "income":
        return amount
    if kind == "expense":
        # 別人先幫我付的（記在負債帳戶「欠別人」）：錢還沒從我這裡出去，還錢那天才扣
        return 0 if r["financed"] or r["account_kind"] == "loan" else -amount
    purpose = r["purpose"]
    if purpose in ("lend", "repay"):                  # 代墊出去、還給先幫我付的人：錢真的離開了
        return -amount
    if purpose == "collect":
        return amount
    if purpose == "debt" and r["debt_id"]:
        return 0
    return -amount if stats.bucket_of(r) else 0


def opening(conn: sqlite3.Connection) -> int:
    return int(settings.get(conn, "wallet.opening", 0) or 0)


def balance_before(conn: sqlite3.Connection, day: date) -> int:
    """day 之前（不含）錢包的餘額。"""
    total = opening(conn)
    for r in conn.execute(ROWS_SQL.format(cond="< ?"), (day.isoformat(),)):
        total += effect(r)
    row = conn.execute("SELECT COALESCE(SUM(s.amount), 0) FROM debt_schedule s JOIN debts d ON d.id = s.debt_id "
                       "WHERE d.active = 1 AND s.status <> 'skipped' AND s.due_date < ?", (day.isoformat(),)).fetchone()
    return total - int(row[0])


def _label(r: sqlite3.Row) -> str:
    return (r["description"] or r["merchant"] or r["category"] or "（沒寫）").strip()


def passbook(conn: sqlite3.Connection, period_key: str | None = None) -> dict[str, Any]:
    """一期的存摺：期初餘額、每一筆（新的在上面）、每筆之後的餘額、期末（或到今天）的餘額。"""
    p = periods.get(conn, period_key)
    today = clock.today()
    until = min(p.end, today) if p.start <= today else p.start
    start_balance = balance_before(conn, p.start)
    items: list[dict[str, Any]] = []
    for r in conn.execute(ROWS_SQL.format(cond="BETWEEN ? AND ?"), (p.start.isoformat(), p.end.isoformat())):
        amount = effect(r)
        note = None
        who = (r["counterparty"] or "").strip()                       # 代墊給誰、誰還的
        if r["kind"] == "expense" and r["financed"]:
            note = f"分期，{r['amount']:,} 之後一期一期扣"
        elif r["kind"] == "expense" and r["account_kind"] == "loan":
            note = f"{who or '別人'}先幫你付，還的那天才扣"
        elif r["kind"] == "transfer":
            note = (f"{who}・" if who else "") + f"{r['account']} → {r['to_account']}" + ("（錢還在自己這裡）" if amount == 0 else "")
        lab = labels.split(desc=r["description"], merchant=r["merchant"], category=r["category"], note=r["memo"], kind=r["kind"],
                           purpose=r["purpose"])
        items.append({"type": "txn", "id": r["id"], "date": r["date"], "time": r["time"], "desc": _label(r),
                      "title": lab["title"],                              # 大項目（類別）：畫面的標題
                      "detail": "・".join([*lab["detail"], *([note] if note else [])]),   # 細節：店家、菜名、轉帳走向…
                      "item": (r["description"] or "").strip(),          # 真正寫的品項（desc 沒寫時會拿店名或類別頂著）
                      "merchant": (r["merchant"] or "").strip(),         # 細節裡也會出現的店名與備註：編輯框要有地方改，
                      "memo": (r["memo"] or "").strip(),                 # 不然畫面寫得出來、點開卻改不到
                      "category": r["category"] or ("待分類" if r["kind"] == "expense" else None),
                      "group": r["group_name"], "account": r["account"], "account_kind": r["account_kind"],
                      "to_account": r["to_account"], "for_period": r["for_period"], "nature": r["nature"], "bucket": r["bucket_id"],
                      "counterparty": r["counterparty"], "financed": bool(r["financed"]),
                      "kind": r["kind"], "purpose": r["purpose"], "amount": amount, "raw": r["amount"], "note": note})
    for d in conn.execute(DUES_SQL.format(cond="BETWEEN ? AND ?"), (p.start.isoformat(), until.isoformat())):
        items.append({"type": "due", "id": None, "date": d["due_date"], "time": None,
                      "desc": f"{d['name']} 第 {d['no']}/{d['periods']} 期", "category": "分期", "group": "還債",
                      "title": f"{d['name']} 第 {d['no']}/{d['periods']} 期", "detail": "",
                      "account": None, "account_kind": None, "to_account": None, "for_period": None, "nature": None, "bucket": "debt", "kind": "debt", "purpose": None,
                      "amount": -d["amount"], "raw": d["amount"], "note": None})
    items.sort(key=lambda x: (x["date"], x["type"] == "txn", x["time"] or ""))   # 同一天：先扣分期再列當天的帳
    balance = start_balance
    for it in items:
        balance += it["amount"]
        it["balance"] = balance
    return {"period": p.key, "start": p.start.isoformat(), "end": p.end.isoformat(), "opening": start_balance,
            "closing": balance, "income": sum(i["amount"] for i in items if i["amount"] > 0),
            "out": -sum(i["amount"] for i in items if i["amount"] < 0), "items": list(reversed(items))}


def summary(conn: sqlite3.Connection, period_key: str | None = None) -> dict[str, Any]:
    """錢包、要保留的、可以自由用的（只有進行中的那一期有保留；過去的期保留是 0）。"""
    book = passbook(conn, period_key)
    p = periods.get(conn, book["period"])
    today = clock.today()
    reserved: list[dict[str, Any]] = []
    if p.is_open(today):
        plan = plans.get(conn, p.key)
        actual = stats.period_summary(conn, p.key)["allocation"]["actual"]
        skipped = plans.skips(conn, p.key)
        for b in plans.buckets(conn):
            if b["is_living"] or b["id"] == "debt" or b["id"] in skipped:   # 還債另外算；這期跳過的不用留
                continue
            quota = int(plan["buckets"].get(b["id"]) or 0)
            if b["rollover"]:                                   # 滾存的格子：前幾期留下來的也要留著（水電瓦斯的帳單還沒來）
                quota += plans.rollover_carry(conn, b["id"], p.key)
            used = int(actual.get(b["id"]) or 0)
            # 會浮動的差不多做到了：不用再留——但滾存的格子剩的還要滾到下期，錢照留（只是不提醒），不然同一筆錢算兩次
            left = 0 if plans.close_enough(conn, b, quota, used) and not b["rollover"] else max(quota - used, 0)
            if left:
                reserved.append({"id": b["id"], "name": b["name"], "amount": left, "locked": b["locked"]})
        upcoming = conn.execute(DUES_SQL.format(cond="BETWEEN ? AND ?"),
                                (date.fromordinal(today.toordinal() + 1).isoformat(), p.end.isoformat())).fetchall()
        if upcoming:
            reserved.append({"id": "debt", "name": "還沒到期的分期", "amount": sum(d["amount"] for d in upcoming)})
        from . import debts                   # 別人先幫我付、還沒還的：錢還在錢包裡，但不是自由的
        owed = debts.pending_advances(conn)["owe"]["total"]
        if owed:
            reserved.append({"id": "owe", "name": "欠別人的", "amount": owed})
    total_reserved = sum(r["amount"] for r in reserved)
    return {"period": p.key, "wallet": book["closing"], "reserved": total_reserved, "free": book["closing"] - total_reserved,
            "reserved_items": reserved, "income": book["income"], "out": book["out"], "opening": book["opening"]}
