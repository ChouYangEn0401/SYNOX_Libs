"""批次：列出最近的寫入、整批撤銷。

撤銷＝把那一批的 audit_log 倒著套回 before：
- before 是空的（那一批新增的）→ 交易改成 deleted，其他表刪掉那一列；
- 其餘 → 把整列寫回 before（交易連品項一起還原）。
撤銷本身也是一批（有自己的 audit），所以撤銷可以再撤銷。
同一筆記錄如果在那一批之後又被別的（沒被撤銷的）批次改過，撤銷回衝突，免得蓋掉之後的修改。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import Writer, row_dict
from ..errors import Conflict, NotFound

# 每張會被 audit 的表用哪個欄位當主鍵；新的表加 audit 時要登記在這裡
PRIMARY_KEYS: dict[str, str] = {
    "transactions": "id", "settings": "key", "accounts": "id", "categories": "id",
    "day_notes": "date", "period_plans": "period", "dedup_pairs": "id", "attachments": "id",
    "card_statements": "id", "statement_lines": "id", "debts": "id", "debt_schedule": "id",
    "balance_snapshots": "id", "recurring": "id", "bills": "id",
    "invest_monthly": "id", "holdings_snapshots": "id", "reviews": "id", "buckets": "id", "notifications": "id",
    "category_groups": "id", "bucket_setaside": "id",
    "trades": "id", "books": "id", "quotes": "id", "cash_leftover": "period_key", "bucket_skips": "id",
    "bucket_moves": "id", "holdings_counts": "id",
}


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT b.*, (SELECT COUNT(*) FROM audit_log a WHERE a.batch_id = b.id) AS changes "
        "FROM batches b ORDER BY b.at DESC, b.id DESC LIMIT ?", (limit,)).fetchall()
    return [row_dict(r) for r in rows]


def get(conn: sqlite3.Connection, batch_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        raise NotFound(f"找不到批次 {batch_id}", hint="批次編號寫在記好、改好、刪掉時的回覆裡；撤銷最近一次用 /money_undo")
    out = row_dict(row)
    out["changes"] = [row_dict(r) for r in conn.execute(
        "SELECT action, entity, entity_id FROM audit_log WHERE batch_id = ? ORDER BY id", (batch_id,))]
    return out


def last_undoable(conn: sqlite3.Connection) -> dict[str, Any]:
    """最近一批：還沒被撤銷、自己也不是撤銷、而且真的有改動（batch undo 用）。"""
    row = conn.execute(
        "SELECT b.* FROM batches b WHERE b.reverted_by IS NULL AND b.reverts IS NULL "
        "AND EXISTS (SELECT 1 FROM audit_log a WHERE a.batch_id = b.id) ORDER BY b.at DESC, b.id DESC LIMIT 1").fetchone()
    if row is None:
        raise NotFound("沒有可以撤銷的寫入", hint="還沒有寫入過，或最近的都已經撤銷了")
    return row_dict(row)


def revert(w: Writer, batch_id: str) -> dict[str, Any]:
    batch = get(w.conn, batch_id)
    if batch["reverted_by"]:
        raise Conflict(f"批次 {batch_id} 已經撤銷過了（被 {batch['reverted_by']}）")
    entries = w.conn.execute("SELECT * FROM audit_log WHERE batch_id = ? ORDER BY id DESC", (batch_id,)).fetchall()
    if not entries:
        raise Conflict(f"批次 {batch_id} 沒有任何改動可以撤銷")
    for e in entries:
        later = w.conn.execute(
            "SELECT a.batch_id FROM audit_log a JOIN batches b ON b.id = a.batch_id "
            "WHERE a.entity = ? AND a.entity_id = ? AND a.id > ? AND a.batch_id <> ? AND b.reverted_by IS NULL "
            "LIMIT 1", (e["entity"], e["entity_id"], e["id"], batch_id)).fetchone()
        if later:
            raise Conflict(f"{e['entity']} {e['entity_id']} 在這批之後又被改過（批次 {later[0]}），不能直接撤銷",
                           hint="先撤銷之後的那一批，或手動改回來")
    for e in entries:
        try:
            _restore(w, e["entity"], e["entity_id"], json.loads(e["before"]) if e["before"] else None)
        except sqlite3.IntegrityError:
            # 例：這一批開的倉，之後別的批次又記了交易進去——倉刪不掉。整批不動，講清楚怎麼辦
            raise Conflict(f"{e['entity']} {e['entity_id']} 後來還有別的資料在用它，不能直接撤銷這一批",
                           hint="先撤銷之後記進去的那幾批（例如記進這一倉的交易），再撤銷這一批") from None
    if any(e["entity"] in ("trades", "books") for e in entries):
        from . import books                   # 交易或倉變了：違規從頭重新看一次（violation 是算出來的）
        books.recheck(w)
    w.conn.execute("UPDATE batches SET reverted_by = ? WHERE id = ?", (w.batch_id, batch_id))
    w.conn.execute("UPDATE batches SET reverts = ? WHERE id = ?", (batch_id, w.batch_id))
    return {"reverted": batch_id, "by": w.batch_id, "changes": len(entries)}


def _current(conn: sqlite3.Connection, table: str, pk: str, key: str) -> dict | None:
    row = row_dict(conn.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (key,)).fetchone())
    if row is not None and table == "transactions":
        row["_items"] = [row_dict(r) for r in conn.execute(
            "SELECT name, qty, amount FROM txn_items WHERE txn_id = ? ORDER BY id", (key,))]
    return row


def _restore(w: Writer, table: str, key: str, before: dict | None) -> None:
    pk = PRIMARY_KEYS.get(table)
    if pk is None:
        raise Conflict(f"不知道怎麼撤銷 {table} 的改動")
    now = _current(w.conn, table, pk, key)
    if before is None:
        if now is None:
            return
        if table == "transactions":
            w.conn.execute("UPDATE transactions SET status = 'deleted', updated_at = ? WHERE id = ?", (w.now, key))
        else:
            w.conn.execute(f"DELETE FROM {table} WHERE {pk} = ?", (key,))
        w.audit("delete", table, key, now, _current(w.conn, table, pk, key))
        return
    items = before.pop("_items", None)
    cols = list(before)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != pk)
    w.conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                   f"ON CONFLICT({pk}) DO UPDATE SET {updates}", tuple(before[c] for c in cols))
    if table == "transactions" and items is not None:
        w.conn.execute("DELETE FROM txn_items WHERE txn_id = ?", (key,))
        for item in items:
            w.conn.execute("INSERT INTO txn_items (txn_id, name, qty, amount) VALUES (?,?,?,?)",
                           (key, item["name"], item["qty"], item["amount"]))
    w.audit("restore" if now is None or now.get("status") == "deleted" else "update", table, key, now,
            _current(w.conn, table, pk, key))
