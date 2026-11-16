"""帳戶餘額與資金水位。

- 一次核對＝某天「結束時」的餘額快照；推算餘額＝最近一次快照＋之後的交易（收入 +、支出 −、轉出 −、轉入 +）。
  沒有快照的帳戶從 0 開始推算（信用卡這樣推算出來就是負的未繳金額）。
- 新快照跟推算值不同 → 回報差額；有上一次快照時可以補一筆「未記差額」（少了＝待分類的支出、多了＝其他收入）。
- 備用金：帳戶可以屬於一個備用金群組（例：緊急預備金分在銀行存款和基金兩處），水位條合起來看；
  群組的底線／目標取成員裡設定的最大值。估「幾個月到底線」用最近三期轉進這個群組的平均。
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from typing import Any

from .. import clock
from ..db import Writer, row_dict
from ..errors import Conflict, InvalidInput, NotFound
from . import periods, refdata, transactions as tx

ASSET_KINDS = ("cash", "bank", "securities", "e_wallet", "receivable", "pension")


def _effect_sql() -> str:
    return ("SELECT COALESCE(SUM(CASE "
            "WHEN kind = 'income' AND account_id = :a THEN amount "
            "WHEN kind = 'expense' AND account_id = :a THEN -amount "
            "WHEN kind = 'transfer' AND account_id = :a THEN -amount "
            "WHEN kind = 'transfer' AND to_account_id = :a THEN amount ELSE 0 END), 0) "
            "FROM transactions WHERE status = 'active' AND (account_id = :a OR to_account_id = :a) "
            "AND date > :since AND date <= :until")


def latest_snapshot(conn: sqlite3.Connection, account_id: int, on_or_before: str) -> dict | None:
    return row_dict(conn.execute("SELECT * FROM balance_snapshots WHERE account_id = ? AND as_of <= ? "
                                 "ORDER BY as_of DESC LIMIT 1", (account_id, on_or_before)).fetchone())


def balance_at(conn: sqlite3.Connection, account_id: int, day: str | None = None) -> dict[str, Any]:
    """某天結束時的餘額（推算）。回 {balance, verified_on, verified_balance}。"""
    day = day or clock.today().isoformat()
    snap = latest_snapshot(conn, account_id, day)
    since = snap["as_of"] if snap else "0000-01-01"
    moved = conn.execute(_effect_sql(), {"a": account_id, "since": since, "until": day}).fetchone()[0]
    base = snap["balance"] if snap else 0
    return {"balance": base + moved, "verified_on": snap and snap["as_of"], "verified_balance": snap and snap["balance"]}


def set_snapshot(w: Writer, *, account: str, balance: int, as_of: str | None = None, note: str = "",
                 source: str = "cli") -> dict[str, Any]:
    acc = refdata.resolve_account(w.conn, account)
    day = tx.parse_date(as_of or clock.today().isoformat(), "as_of")
    prev = latest_snapshot(w.conn, acc["id"], (date.fromisoformat(day) - timedelta(days=1)).isoformat())
    derived = balance_at(w.conn, acc["id"], day)["balance"]
    before = row_dict(w.conn.execute("SELECT * FROM balance_snapshots WHERE account_id = ? AND as_of = ?",
                                     (acc["id"], day)).fetchone())
    if before and before["balance"] == balance and before["note"] == note:
        sid = before["id"]                                   # 一模一樣的核對：不重寫（重跑匯入不會多一批）
    elif before:
        w.conn.execute("UPDATE balance_snapshots SET balance = ?, note = ?, source = ?, batch_id = ? WHERE id = ?",
                       (balance, note, source, w.batch_id, before["id"]))
        sid = before["id"]
    else:
        sid = w.conn.execute("INSERT INTO balance_snapshots (account_id, as_of, balance, source, note, batch_id, created_at) "
                             "VALUES (?,?,?,?,?,?,?)", (acc["id"], day, balance, source, note, w.batch_id, w.now)).lastrowid
    after = row_dict(w.conn.execute("SELECT * FROM balance_snapshots WHERE id = ?", (sid,)).fetchone())
    if after != before:
        w.audit("update" if before else "create", "balance_snapshots", sid, before, after)
    # 推算值：同一天已經有快照時，要用「沒有這張快照」的推算才比得出差額
    if before:
        base = latest_snapshot(w.conn, acc["id"], (date.fromisoformat(day) - timedelta(days=1)).isoformat())
        since = base["as_of"] if base else "0000-01-01"
        moved = w.conn.execute(_effect_sql(), {"a": acc["id"], "since": since, "until": day}).fetchone()[0]
        derived = (base["balance"] if base else 0) + moved
    diff = balance - derived
    return {"snapshot_id": sid, "account": acc["name"], "as_of": day, "balance": balance, "derived": derived,
            "diff": diff, "first": prev is None, "can_adjust": prev is not None and diff != 0}


def adjust(w: Writer, snapshot_id: int) -> dict[str, Any]:
    """補一筆「未記差額」，讓推算值等於核對的餘額。"""
    snap = row_dict(w.conn.execute("SELECT * FROM balance_snapshots WHERE id = ?", (snapshot_id,)).fetchone())
    if snap is None:
        raise NotFound(f"找不到核對紀錄 #{snapshot_id}")
    prev = latest_snapshot(w.conn, snap["account_id"], (date.fromisoformat(snap["as_of"]) - timedelta(days=1)).isoformat())
    if prev is None:
        raise Conflict("這是第一次核對，沒有可以比的推算值", hint="第一次核對就當成起始餘額，不用補")
    moved = w.conn.execute(_effect_sql(), {"a": snap["account_id"], "since": prev["as_of"], "until": snap["as_of"]}).fetchone()[0]
    diff = snap["balance"] - (prev["balance"] + moved)
    if diff == 0:
        raise Conflict("推算值跟核對的餘額一樣，不用補")
    if diff < 0:
        t = tx.create(w, kind="expense", amount=-diff, date=snap["as_of"], account_id=snap["account_id"],
                      description="未記差額（核對餘額時補）", note=f"{prev['as_of']} 到 {snap['as_of']} 之間少了這些錢，記得分類",
                      source="balance", source_ref=f"adjust:{snapshot_id}")
    else:
        cat = refdata.system_category(w.conn, "income.other")
        t = tx.create(w, kind="income", amount=diff, date=snap["as_of"], account_id=snap["account_id"],
                      category_id=cat["id"], description="未記差額（核對餘額時補）",
                      note=f"{prev['as_of']} 到 {snap['as_of']} 之間多了這些錢", source="balance",
                      source_ref=f"adjust:{snapshot_id}")
    return {"adjustment": t, "diff": diff}


def snapshots(conn: sqlite3.Connection, account: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    args: list[Any] = []
    where = ""
    if account:
        where = " WHERE s.account_id = ?"
        args.append(refdata.resolve_account(conn, account)["id"])
    rows = conn.execute("SELECT s.*, a.name AS account FROM balance_snapshots s JOIN accounts a ON a.id = s.account_id"
                        + where + " ORDER BY s.as_of DESC, s.id DESC LIMIT ?", (*args, limit)).fetchall()
    return [row_dict(r) for r in rows]


def overview(conn: sqlite3.Connection, period_key: str | None = None) -> dict[str, Any]:
    """每個帳戶的餘額、最後核對、本期變化，以及資產、負債、淨值。"""
    from . import debts
    p = periods.get(conn, period_key)
    today = clock.today()
    end = min(p.end, today).isoformat()
    before_start = (p.start - timedelta(days=1)).isoformat()
    rows = []
    for a in refdata.accounts(conn):
        now = balance_at(conn, a["id"], end)
        then = balance_at(conn, a["id"], before_start)
        rows.append({"id": a["id"], "name": a["name"], "kind": a["kind"], "purpose": a["purpose"], "liquid": bool(a["liquid"]),
                     "reserve_group": a.get("reserve_group"), "floor": a["floor_amount"], "target": a["target_amount"],
                     "balance": now["balance"], "verified_on": now["verified_on"], "change": now["balance"] - then["balance"]})
    assets = sum(r["balance"] for r in rows if r["kind"] in ASSET_KINDS and r["balance"] > 0)
    liquid = sum(r["balance"] for r in rows if r["kind"] in ASSET_KINDS and r["liquid"] and r["balance"] > 0)
    card_debt = -sum(r["balance"] for r in rows if r["kind"] in ("credit_card", "loan") and r["balance"] < 0)
    other_debt = sum(d["remaining"] for d in debts.all_debts(conn) if d["kind"] != "card_installment")
    return {"period": p.to_dict(today), "as_of": end, "accounts": rows, "assets": assets, "liquid": liquid,
            "card_debt": card_debt, "other_debt": other_debt, "net_worth": assets - card_debt - other_debt}


def _monthly_inflow(conn: sqlite3.Connection, ids: list[int], months: int = 3) -> int:
    if not ids:
        return 0
    p = periods.current(conn)
    start = p
    for _ in range(months):
        start = periods.shift(conn, start, -1)
    marks = ",".join("?" * len(ids))
    total = conn.execute(f"SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE status = 'active' AND kind = 'transfer' "
                         f"AND to_account_id IN ({marks}) AND account_id NOT IN ({marks}) AND date BETWEEN ? AND ?",
                         (*ids, *ids, start.start.isoformat(), (p.start - timedelta(days=1)).isoformat())).fetchone()[0]
    return round(total / months)


def reserves(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """備用金群組的水位：餘額合計、底線、目標、差多少、照最近的存法幾個月到。"""
    groups: dict[str, list[dict]] = {}
    for a in refdata.accounts(conn):
        name = a.get("reserve_group") or (a["name"] if a["floor_amount"] or a["target_amount"] else None)
        if name:
            groups.setdefault(name, []).append(a)
    out = []
    for name, members in groups.items():
        bal = sum(balance_at(conn, m["id"])["balance"] for m in members)
        floor = max((m["floor_amount"] or 0) for m in members) or None
        target = max((m["target_amount"] or 0) for m in members) or None
        monthly = _monthly_inflow(conn, [m["id"] for m in members])
        if floor and bal < floor:
            gap, goal = floor - bal, "底線"
        elif target and bal < target:
            gap, goal = target - bal, "目標"
        else:
            gap, goal = 0, None
        months = math.ceil(gap / monthly) if gap and monthly > 0 else None
        extra = (f"還差 {gap:,} 到{goal}" + (f"，照最近每月存 {monthly:,} 約 {months} 個月" if months else "，最近沒有固定存進來")
                 if gap else ("可以把多的轉去投資" if target else ""))
        notes = "、".join(sorted({m["purpose"] for m in members if m["purpose"]}))
        out.append({"name": name, "note": notes, "accounts": [m["name"] for m in members], "balance": bal,
                    "floor": floor, "target": target, "gap": gap, "goal": goal, "monthly": monthly, "months": months,
                    "extra": extra, "level": "crit" if floor and bal < floor else ("warn" if target and bal < target else "good")})
    return out


def validate_amount(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise InvalidInput("餘額要是整數（元）") from None
