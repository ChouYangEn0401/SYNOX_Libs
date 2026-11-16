"""帳單：電費、水費、瓦斯（記度數，看漲跌）以及其他有繳費期限的帳單。

- 一張帳單：種類、計費期間、度數與單位、金額、繳費期限；(種類, 計費期間迄日) 唯一，重送冪等。
- 已繳（或給了繳費日）→ 同時記一筆支出（類別依種類：電費／水費／瓦斯／手機網路……），帳單連到那筆。
- 還沒繳 → 到期前 7 天在總覽提醒；之後 pay() 補記支出。
- 夏月：計費期間的中點落在 6–9 月（台電夏月電價），同樣度數會比較貴。
- 趨勢：每期金額、度數、每度多少錢、跟去年同期比（把「用得多」和「電價高」分開看）。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from .. import clock
from ..db import Writer, row_dict
from ..errors import Conflict, InvalidInput, NotFound
from . import refdata, transactions as tx

TYPES = {"electricity": ("電費", "度", "bill.electricity"), "water": ("水費", "度", "bill.water"), "gas": ("瓦斯", "度", "bill.gas"),
         "phone": ("手機網路", None, "bill.phone"), "rent": ("房租", None, "bill.rent")}     # 第三個是類別的系統代號
ALIASES = {"電費": "electricity", "電": "electricity", "水費": "water", "自來水": "water",
           "水": "water", "瓦斯": "gas", "天然氣": "gas", "手機": "phone", "手機費": "phone", "電信": "phone",
           "網路": "phone", "房租": "rent"}


def normalize_type(text: str) -> str:
    t = (text or "").strip()
    if t in TYPES:
        return t
    return ALIASES.get(t, t)


def label(bill_type: str) -> str:
    return TYPES.get(bill_type, (bill_type, None, None))[0]


def is_summer(start: str | None, end: str) -> bool:
    e = date.fromisoformat(end)
    s = date.fromisoformat(start) if start else e - timedelta(days=60)
    mid = s + (e - s) / 2
    return 6 <= mid.month <= 9


def _audit(w: Writer, bid: int, action: str, before: dict | None) -> None:
    w.audit(action, "bills", bid, before, row_dict(w.conn.execute("SELECT * FROM bills WHERE id = ?", (bid,)).fetchone()))


def save(w: Writer, *, bill: str, amount: int, period_end: str | None = None, period_start: str | None = None,
         usage: float | None = None, unit: str | None = None, due_date: str | None = None, paid: bool = False,
         paid_date: str | None = None, account: str | None = None, note: str = "", source: str = "cli",
         source_ref: str | None = None) -> dict[str, Any]:
    btype = normalize_type(bill)
    end = tx.parse_date(period_end or paid_date or clock.today().isoformat(), "period_end")
    start = tx.parse_date(period_start, "period_start") if period_start else None
    due = tx.parse_date(due_date, "due_date", allow_future=True) if due_date else None
    unit = unit or TYPES.get(btype, (None, None, None))[1]
    existing = row_dict(w.conn.execute("SELECT * FROM bills WHERE bill_type = ? AND period_end = ?", (btype, end)).fetchone())
    if existing and existing["amount"] == amount and (existing["usage"] or None) == (usage or None):
        return {"status": "replayed", "bill": get(w.conn, existing["id"])}
    if existing:
        raise Conflict(f"{label(btype)} {end} 那期已經記過，金額或度數不一樣（原本 {existing['amount']:,}）",
                       hint="先撤銷那一筆（/money_undo 或明細頁）再重記，或確認是不是另一期")
    bid = w.conn.execute(
        "INSERT INTO bills (bill_type, period_start, period_end, usage, unit, amount, due_date, summer, note, source, "
        "source_ref, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (btype, start, end, usage, unit, amount, due, int(btype == "electricity" and is_summer(start, end)), note, source,
         source_ref, w.now)).lastrowid
    _audit(w, bid, "create", None)
    if paid or paid_date:
        pay(w, bid, account=account, day=paid_date)
    return {"status": "created", "bill": get(w.conn, bid)}


def pay(w: Writer, bill_id: int, *, account: str | None = None, day: str | None = None) -> dict[str, Any]:
    b = row_dict(w.conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone())
    if b is None:
        raise NotFound(f"找不到帳單 #{bill_id}")
    if b["txn_id"]:
        raise Conflict("這張帳單已經記過繳費了")
    from . import settings
    acc = refdata.resolve_account(w.conn, account or settings.get(w.conn, "default_account") or "現金")
    cat_key = TYPES.get(b["bill_type"], (None, None, None))[2]
    cat = refdata.system_category(w.conn, cat_key) if cat_key else None
    usage = f"，{b['usage']:g} {b['unit'] or ''}".rstrip() if b["usage"] else ""
    t = tx.create(w, kind="expense", amount=b["amount"], date=day or clock.today().isoformat(), account_id=acc["id"],
                  category_id=cat and cat["id"], description=f"{label(b['bill_type'])}（{(b['period_start'] or '')[5:]}–{b['period_end'][5:]}{usage}）",
                  source="bill", source_ref=f"bill:{bill_id}")
    w.conn.execute("UPDATE bills SET txn_id = ? WHERE id = ?", (t["id"], bill_id))
    _audit(w, bill_id, "update", b)
    return {"bill": get(w.conn, bill_id), "payment": t}


def get(conn: sqlite3.Connection, bill_id: int) -> dict[str, Any]:
    b = row_dict(conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone())
    if b is None:
        raise NotFound(f"找不到帳單 #{bill_id}")
    today = clock.today()
    due = date.fromisoformat(b["due_date"]) if b["due_date"] else None
    return {**b, "label": label(b["bill_type"]), "paid": bool(b["txn_id"]), "summer": bool(b["summer"]),
            "unit_price": round(b["amount"] / b["usage"], 2) if b["usage"] else None,
            "days_to_due": (due - today).days if due else None}


def all_bills(conn: sqlite3.Connection, bill_type: str | None = None, limit: int = 60) -> list[dict[str, Any]]:
    args: list[Any] = []
    where = ""
    if bill_type:
        where = " WHERE bill_type = ?"
        args.append(normalize_type(bill_type))
    rows = conn.execute("SELECT id FROM bills" + where + " ORDER BY period_end DESC LIMIT ?", (*args, limit)).fetchall()
    return [get(conn, r["id"]) for r in rows]


def unpaid(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [b for b in all_bills(conn) if not b["paid"]]


def alerts(conn: sqlite3.Connection, within_days: int = 7) -> list[dict[str, Any]]:
    out = []
    for b in unpaid(conn):
        if b["days_to_due"] is not None and b["days_to_due"] <= within_days:
            when = "今天" if b["days_to_due"] == 0 else (f"{b['days_to_due']} 天後" if b["days_to_due"] > 0 else f"已過 {-b['days_to_due']} 天")
            out.append({"id": b["id"], "label": b["label"], "amount": b["amount"], "due_date": b["due_date"],
                        "alert": f"{b['label']} {b['amount']:,} {when}到期"})
    return out


def trend(conn: sqlite3.Connection, bill_type: str = "electricity", count: int = 12) -> dict[str, Any]:
    btype = normalize_type(bill_type)
    rows = list(reversed(all_bills(conn, btype, count)))
    points = [{"id": b["id"], "label": b["period_end"][2:7].replace("-", "/"), "period_start": b["period_start"],
               "period_end": b["period_end"], "amount": b["amount"], "usage": b["usage"], "unit": b["unit"],
               "unit_price": b["unit_price"], "summer": b["summer"], "paid": b["paid"]} for b in rows]
    insight = None
    if points:
        last = points[-1]
        end = date.fromisoformat(last["period_end"])
        prev = next((p for p in reversed(points[:-1]) if abs((date.fromisoformat(p["period_end"]) - end.replace(year=end.year - 1)).days) <= 20), None)
        if prev:
            parts = [f"這期 {last['amount']:,} 元" + (f"、{last['usage']:g} {last['unit'] or ''}" if last["usage"] else "")]
            pct = round((last["amount"] / prev["amount"] - 1) * 100) if prev["amount"] else None
            text = f"比去年同期（{prev['label']}）{'多' if pct >= 0 else '少'} {abs(pct)}%" if pct is not None else ""
            if last["usage"] and prev["usage"]:
                upct = round((last["usage"] / prev["usage"] - 1) * 100)
                text += f"，度數{'多' if upct >= 0 else '少'} {abs(upct)}%"
                if last["unit_price"] and prev["unit_price"]:
                    text += f"；每度 {last['unit_price']:.2f} 元（去年 {prev['unit_price']:.2f}）"
            insight = "，".join(parts) + "，" + text + ("。夏月電價（6–9 月）同樣度數會比較貴。" if last["summer"] else "。")
        else:
            insight = f"這期 {last['amount']:,} 元" + (f"、{last['usage']:g} {last['unit'] or ''}" if last["usage"] else "") + "；還沒有去年同期可以比。"
    return {"type": btype, "label": label(btype), "points": points, "insight": insight}


def validate_type(text: str) -> str:
    t = normalize_type(text)
    if not t:
        raise InvalidInput("帳單種類不能是空的")
    return t
