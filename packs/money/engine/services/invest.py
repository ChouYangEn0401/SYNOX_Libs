"""投資：每月損益（簡單記就好）、部位快照、跟目標與基準比較。

- 每月一筆損益（月份＋帳戶唯一），可以附市值與心得；有市值時同時當成那個月底的核對餘額（證券戶的差額是行情，不是漏記）。
- 本金、開始日、年化目標存在設定（invest.capital／invest.start／invest.target_year）。
- 每月目標＝本金 ×（(1＋年化)^(1/12) − 1）；累計、報酬率、年化、達標月數，跟定存／ETF／基金／操盤手的年化比較
  （常見複利試算表會放的那幾個基準）。
- 部位快照：某天某帳戶的部位，依桶分（長單、短單、定存型、彈性本金…），看配置比例。
"""
from __future__ import annotations

import calendar
import re
import sqlite3
from datetime import date
from typing import Any

from .. import clock
from ..db import Writer, row_dict
from ..errors import InvalidInput, NotFound
from . import refdata, settings, transactions as tx

BENCHMARKS = [("銀行定存", 0.018), ("ETF 定存", 0.012), ("台股基金", 0.02), ("操盤手", 0.20)]
MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


def parse_month(text: str) -> str:
    m = MONTH_RE.match((text or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise InvalidInput(f"月份「{text}」看不懂", hint="寫成 YYYY-MM，例如 2026-09", fields={"month": "格式不對"})
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"


def _account(conn: sqlite3.Connection, name: str | None) -> dict[str, Any]:
    if name:
        return refdata.resolve_account(conn, name)
    default = settings.get(conn, "invest.default_account")
    if default:
        return refdata.resolve_account(conn, default)
    for a in refdata.accounts(conn):
        if a["kind"] == "securities" and not a.get("reserve_group"):
            return a
    raise InvalidInput("沒有投資帳戶", hint="先在設定頁加一個證券帳戶，或在紀錄裡寫 account")


def add_month(w: Writer, *, month: str, pnl: int, account: str | None = None, market_value: int | None = None,
              net_deposit: int | None = None, note: str = "", source: str = "cli") -> dict[str, Any]:
    key = parse_month(month)
    acc = _account(w.conn, account)
    before = row_dict(w.conn.execute("SELECT * FROM invest_monthly WHERE month = ? AND account_id = ?",
                                     (key, acc["id"])).fetchone())
    values = {"pnl": pnl, "market_value": market_value, "net_deposit": net_deposit, "note": note}
    if before and all(before[k] == v for k, v in values.items()):
        return {"status": "unchanged", "month": present(before, acc)}
    if before:
        w.conn.execute("UPDATE invest_monthly SET pnl = ?, market_value = ?, net_deposit = ?, note = ?, source = ?, "
                       "updated_at = ? WHERE id = ?", (pnl, market_value, net_deposit, note, source, w.now, before["id"]))
        mid = before["id"]
    else:
        mid = w.conn.execute("INSERT INTO invest_monthly (month, account_id, pnl, market_value, net_deposit, note, source, "
                             "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                             (key, acc["id"], pnl, market_value, net_deposit, note, source, w.now, w.now)).lastrowid
    after = row_dict(w.conn.execute("SELECT * FROM invest_monthly WHERE id = ?", (mid,)).fetchone())
    w.audit("update" if before else "create", "invest_monthly", mid, before, after)
    if market_value is not None:
        from . import balances
        y, m = int(key[:4]), int(key[5:])
        end = date(y, m, calendar.monthrange(y, m)[1])
        balances.set_snapshot(w, account=acc["name"], balance=market_value,
                              as_of=min(end, clock.today()).isoformat(), note=f"{key} 投資月結市值", source=source)
    return {"status": "updated" if before else "created", "month": present(after, acc)}


def present(r: dict, acc: dict | None = None) -> dict[str, Any]:
    return {"id": r["id"], "month": r["month"], "account": acc["name"] if acc else None, "pnl": r["pnl"],
            "market_value": r["market_value"], "net_deposit": r["net_deposit"], "note": r["note"]}


def config(conn: sqlite3.Connection) -> dict[str, Any]:
    return {"capital": settings.get(conn, "invest.capital"), "start": settings.get(conn, "invest.start"),
            "target_year": settings.get(conn, "invest.target_year", 0.0) or 0.0}


def set_config(w: Writer, *, capital: int | None = None, start: str | None = None, target_year: float | None = None) -> dict:
    if capital is not None:
        settings.put(w, "invest.capital", int(capital))
    if start is not None:
        settings.put(w, "invest.start", tx.parse_date(start, "start"))
    if target_year is not None:
        if not 0 <= target_year <= 5:
            raise InvalidInput("年化目標寫成小數，例如 0.22（＝22%）")
        settings.put(w, "invest.target_year", float(target_year))
    return config(w.conn)


def summary(conn: sqlite3.Connection, account: str | None = None) -> dict[str, Any]:
    cfg = config(conn)
    args: list[Any] = []
    where = ""
    if account:
        where = " WHERE m.account_id = ?"
        args.append(refdata.resolve_account(conn, account)["id"])
    rows = conn.execute("SELECT m.*, a.name AS account FROM invest_monthly m JOIN accounts a ON a.id = m.account_id"
                        + where + " ORDER BY m.month", args).fetchall()
    by_month: dict[str, dict[str, Any]] = {}
    for r in rows:
        item = by_month.setdefault(r["month"], {"month": r["month"], "pnl": 0, "market_value": 0, "notes": []})
        item["pnl"] += r["pnl"]
        item["market_value"] += r["market_value"] or 0
        if r["note"]:
            item["notes"].append(r["note"])
    capital = cfg["capital"] or 0
    target_m = round(capital * ((1 + cfg["target_year"]) ** (1 / 12) - 1)) if capital and cfg["target_year"] else 0
    months, cum = [], 0
    for i, item in enumerate(by_month.values()):
        cum += item["pnl"]
        months.append({**item, "note": "；".join(item["notes"]), "cumulative": cum, "target": target_m,
                       "target_cumulative": target_m * (i + 1), "vs_target": item["pnl"] - target_m,
                       "return": round(item["pnl"] / capital, 4) if capital else None})
    n = len(months)
    total = cum
    ret = total / capital if capital else None
    annual = ((1 + ret) ** (12 / n) - 1) if ret is not None and n and ret > -1 else None
    bench = []
    if ret is not None and n:
        for name, rate in BENCHMARKS:
            expected = capital * ((1 + rate) ** (n / 12) - 1)
            bench.append({"name": name, "rate": rate, "expected": round(expected), "diff": round(total - expected)})
    return {"config": cfg, "months": months, "count": n, "total": total, "return": round(ret, 4) if ret is not None else None,
            "annualized": round(annual, 4) if annual is not None else None, "target_monthly": target_m,
            "hit_months": sum(1 for m in months if target_m and m["pnl"] >= target_m), "benchmarks": bench,
            "holdings": latest_holdings(conn)}


def set_holdings(w: Writer, *, items: list[dict[str, Any]], as_of: str | None = None, account: str | None = None,
                 source: str = "cli") -> dict[str, Any]:
    acc = _account(w.conn, account)
    day = tx.parse_date(as_of or clock.today().isoformat(), "as_of")
    if not items:
        raise InvalidInput("部位清單是空的")
    old = w.conn.execute("SELECT * FROM holdings_snapshots WHERE account_id = ? AND as_of = ? ORDER BY id",
                         (acc["id"], day)).fetchall()
    key = lambda b, n, s, v, c, note: (b or "未分類", n, s, int(v), c, note or "")  # noqa: E731
    same = [key(r["bucket"], r["name"], r["symbol"], r["market_value"], r["cost"], r["note"]) for r in old] == \
        [key(i.get("bucket"), i["name"], i.get("symbol"), i["market_value"], i.get("cost"), i.get("note")) for i in items]
    if same:
        return {"account": acc["name"], "as_of": day, "count": len(items), "holdings": latest_holdings(w.conn, acc["name"])}
    for r in old:
        w.conn.execute("DELETE FROM holdings_snapshots WHERE id = ?", (r["id"],))
        w.audit("delete", "holdings_snapshots", r["id"], row_dict(r), None)
    for it in items:
        hid = w.conn.execute("INSERT INTO holdings_snapshots (as_of, account_id, bucket, name, symbol, market_value, cost, note, "
                             "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                             (day, acc["id"], it.get("bucket") or "未分類", it["name"], it.get("symbol"), int(it["market_value"]),
                              it.get("cost"), it.get("note", ""), w.now)).lastrowid
        w.audit("create", "holdings_snapshots", hid, None,
                row_dict(w.conn.execute("SELECT * FROM holdings_snapshots WHERE id = ?", (hid,)).fetchone()))
    return {"account": acc["name"], "as_of": day, "count": len(items), "holdings": latest_holdings(w.conn, acc["name"])}


def latest_holdings(conn: sqlite3.Connection, account: str | None = None) -> dict[str, Any]:
    args: list[Any] = []
    where = ""
    if account:
        where = " WHERE account_id = ?"
        args.append(refdata.resolve_account(conn, account)["id"])
    latest = conn.execute("SELECT account_id, MAX(as_of) AS as_of FROM holdings_snapshots" + where + " GROUP BY account_id",
                          args).fetchall()
    items = []
    for r in latest:
        items += [row_dict(x) for x in conn.execute("SELECT * FROM holdings_snapshots WHERE account_id = ? AND as_of = ? ORDER BY id",
                                                    (r["account_id"], r["as_of"]))]
    total = sum(i["market_value"] for i in items)
    buckets: dict[str, int] = {}
    for i in items:
        buckets[i["bucket"]] = buckets.get(i["bucket"], 0) + i["market_value"]
    return {"as_of": max((r["as_of"] for r in latest), default=None), "total": total,
            "buckets": [{"bucket": k, "value": v, "share": round(v / total, 4) if total else 0}
                        for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])],
            "items": [{"bucket": i["bucket"], "name": i["name"], "symbol": i["symbol"], "market_value": i["market_value"],
                       "cost": i["cost"]} for i in items]}


def get_month(conn: sqlite3.Connection, month_id: int) -> dict[str, Any]:
    r = row_dict(conn.execute("SELECT * FROM invest_monthly WHERE id = ?", (month_id,)).fetchone())
    if r is None:
        raise NotFound(f"找不到投資月紀錄 #{month_id}")
    return present(r)
