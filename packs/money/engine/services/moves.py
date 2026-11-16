"""額度挪用：這一期把 A 格的額度挪去補 B 格，每一次都留紀錄、有地方看統計。

為什麼要留紀錄：格子之間互相挪用很常見，要能事後統計「哪一格常被挪、挪去哪」。
- 挪用**不記帳、不搬錢**：錢還在原本的帳戶，改的是「這一期每一格可以用多少」——A 的額度少 N、B 的多 N。
- 效果只在挪用那一期：`plans.get()` 的 `buckets`＝計畫 − 挪出 ＋ 挪入（總覽、錢包要留的、額度提醒、滾存都讀它）；
  滾存的格子下一期帶過去的數字自然會反映（那一期的額度本來就變了），不會再套一次。
- 生活費可以挪進挪出：生活費的額度（`living_budget`、每天可以花多少）跟著變；大類的軟性預算不動。
- 擋：同一格、金額不是正的、停用的格子、挪進這期「跳過」的格子（挪進去也用不到）。
  挪出超過那一格剩下的**不擋**（實際上常常會超），回一句 warning。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..db import Writer, row_dict
from ..errors import InvalidInput, NotFound
from . import periods, plans
from .transactions import parse_amount


def _bucket(conn: sqlite3.Connection, ref: str, field: str) -> dict[str, Any]:
    text = str(ref or "").strip()
    if not text:
        raise InvalidInput("要說從哪一格挪到哪一格", fields={field: "必填"})
    for b in plans.buckets(conn, include_inactive=True):
        if text in (b["id"], b["name"], b["official"]):
            if not b["active"]:
                raise InvalidInput(f"「{b['name']}」停用了，不能挪進挪出", hint="先到設定把它打開", fields={field: "停用"})
            return b
    raise NotFound(f"沒有「{text}」這一格", suggestions=[b["name"] for b in plans.buckets(conn)][:8],
                   hint="、".join(f"{b['id']}（{b['name']}）" for b in plans.buckets(conn)))


def _left(conn: sqlite3.Connection, bucket: dict[str, Any], period_key: str) -> int:
    """這一格這期現在還剩多少（套上已經有的挪用；滾存的含前期帶進來的）。"""
    from . import stats
    plan = int(plans.get(conn, period_key)["buckets"].get(bucket["id"]) or 0)
    carry = plans.rollover_carry(conn, bucket["id"], period_key) if bucket["rollover"] else 0
    used = int(stats.period_summary(conn, period_key)["allocation"]["actual"].get(bucket["id"]) or 0)
    return plan + carry - used


def move(w: Writer, from_bucket: str, to_bucket: str, amount: Any, note: str = "",
         period_key: str | None = None) -> dict[str, Any]:
    """這期從 from 挪 amount 去補 to。回這一筆（含兩格的名字）與 warnings。"""
    src, dst = _bucket(w.conn, from_bucket, "from"), _bucket(w.conn, to_bucket, "to")
    if src["id"] == dst["id"]:
        raise InvalidInput("從哪一格、挪到哪一格要不一樣", fields={"to": "跟 from 一樣"})
    value = parse_amount(amount, "amount")
    if value <= 0:
        raise InvalidInput("挪的金額要大於 0", fields={"amount": "要大於 0"})
    p = periods.get(w.conn, period_key)
    if dst["id"] in plans.skips(w.conn, p.key):
        raise InvalidInput(f"「{dst['name']}」這期跳過了，挪進去也用不到", hint="要用就先取消跳過", fields={"to": "這期跳過"})
    warnings = []
    left = _left(w.conn, src, p.key)
    if value > left:
        warnings.append(f"「{src['name']}」這期只剩 {max(left, 0):,}，挪 {value:,} 之後會超過 {value - max(left, 0):,}"
                        if left > 0 else f"「{src['name']}」這期已經沒有剩了，挪完會再超過 {value:,}")
    note = (note or "").strip()[:60]
    new_id = w.conn.execute("INSERT INTO bucket_moves (period, from_bucket, to_bucket, amount, note, created_at) VALUES (?,?,?,?,?,?)",
                            (p.key, src["id"], dst["id"], value, note, w.now)).lastrowid
    w.audit("create", "bucket_moves", new_id, None,
            row_dict(w.conn.execute("SELECT * FROM bucket_moves WHERE id = ?", (new_id,)).fetchone()))
    out = next(m for m in list_moves(w.conn, p.key) if m["id"] == new_id)
    return {**out, "warnings": warnings}


def undo(w: Writer, move_id: int) -> dict[str, Any]:
    """拿掉一筆挪用（兩格的額度回到挪之前）。"""
    row = row_dict(w.conn.execute("SELECT * FROM bucket_moves WHERE id = ?", (int(move_id),)).fetchone())
    if row is None:
        raise NotFound(f"沒有編號 {move_id} 的挪用", hint="總覽的「這期的挪用」看這期有哪些")
    shown = next(m for m in list_moves(w.conn, row["period"]) if m["id"] == row["id"])
    w.conn.execute("DELETE FROM bucket_moves WHERE id = ?", (row["id"],))
    w.audit("delete", "bucket_moves", row["id"], row, None)
    return {**shown, "undone": True}


def list_moves(conn: sqlite3.Connection, period_key: str) -> list[dict[str, Any]]:
    """這一期的挪用，新的在上面。"""
    names = plans.bucket_names(conn)
    return [{"id": r["id"], "period": r["period"], "from": r["from_bucket"], "to": r["to_bucket"],
             "from_name": names.get(r["from_bucket"], r["from_bucket"]), "to_name": names.get(r["to_bucket"], r["to_bucket"]),
             "amount": r["amount"], "note": r["note"], "at": r["created_at"], "date": (r["created_at"] or "")[:10]}
            for r in conn.execute("SELECT * FROM bucket_moves WHERE period = ? ORDER BY created_at DESC, id DESC", (period_key,))]


def stats(conn: sqlite3.Connection, period_key: str) -> dict[str, Any]:
    """這一期的挪用統計：共挪幾次、多少；每一格挪入／挪出／淨額；最大的一筆；最常挪的那一對；被挪最多的格子。"""
    items = list_moves(conn, period_key)
    names = plans.bucket_names(conn)
    per: dict[str, dict[str, Any]] = {}
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for m in items:
        for bid, key in ((m["from"], "out"), (m["to"], "in")):
            row = per.setdefault(bid, {"id": bid, "name": names.get(bid, bid), "in": 0, "out": 0, "net": 0})
            row[key] += m["amount"]
            row["net"] += m["amount"] if key == "in" else -m["amount"]
        pair = pairs.setdefault((m["from"], m["to"]), {"from": m["from"], "to": m["to"], "from_name": m["from_name"],
                                                       "to_name": m["to_name"], "count": 0, "amount": 0})
        pair["count"] += 1
        pair["amount"] += m["amount"]
    buckets = sorted(per.values(), key=lambda r: (r["net"], r["name"]))
    biggest = max(items, key=lambda m: (m["amount"], m["id"]), default=None)
    top_pair = max(pairs.values(), key=lambda p: (p["count"], p["amount"]), default=None)
    taken = [b for b in buckets if b["out"]]
    most_taken = max(taken, key=lambda b: (b["out"], b["name"]), default=None)
    return {"period": period_key, "count": len(items), "total": sum(m["amount"] for m in items),
            "buckets": buckets, "biggest": biggest, "top_pair": top_pair, "most_taken": most_taken}


def summary(conn: sqlite3.Connection, period_key: str | None = None) -> dict[str, Any]:
    """總覽與 CLI `plan moves` 用的一包：清單＋統計。"""
    p = periods.get(conn, period_key)
    return {"period": p.key, "items": list_moves(conn, p.key), "stats": stats(conn, p.key)}
