"""去重：寫入當下判斷「看起來像」的兩筆、開網站時再掃一遍、讓使用者決定。

打分（≥ 0.7 就建一組待確認）：
- 先篩：同種類、還沒刪、金額相同（兩筆都有外幣時外幣差 ≤1%）、日期差 ≤ 1 天、不在同一批。
- 基本 0.4；同帳戶 +0.3；內容＋商家的雙字詞相似度 ≥ 0.5 時 +0.4；兩筆都有時間而且差超過 2 小時 −0.5。
兩筆都先算進統計，直到使用者處理：兩筆都留／刪較新的／合併（保留較舊的，補上較新那筆的品項、收據、時間、商家）。
同一對處理過就不再問（dedup_pairs 唯一鍵）。
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from ..db import Writer, row_dict
from ..errors import Conflict, InvalidInput, NotFound
from . import refdata, transactions as tx

THRESHOLD = 0.7
ACTIONS = ("keep_both", "delete_newer", "merge")


def _bigrams(text: str) -> set[str]:
    s = "".join(ch for ch in refdata.normalize(text) if not ch.isdigit() and ch not in "()（）[]【】,，:：/")
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def similarity(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def _minutes(row: dict) -> int | None:
    if not row.get("time"):
        return None
    d = date.fromisoformat(row["date"])
    h, m = int(row["time"][:2]), int(row["time"][3:5])
    return int(datetime(d.year, d.month, d.day, h, m).timestamp() // 60)


def score(a: dict, b: dict) -> tuple[float, list[str]]:
    reasons = [f"同金額 {a['amount']:,}"]
    s = 0.4
    if a["account_id"] == b["account_id"]:
        s += 0.3
        reasons.append("同一個帳戶")
    text_a = f"{a.get('description') or a.get('desc') or ''} {a.get('merchant') or ''}"
    text_b = f"{b.get('description') or b.get('desc') or ''} {b.get('merchant') or ''}"
    sim = similarity(text_a, text_b)
    if sim >= 0.5:
        s += 0.4
        reasons.append("內容很像")
    ta, tb = _minutes(a), _minutes(b)
    if ta is not None and tb is not None:
        gap = abs(ta - tb)
        if gap > 120:
            s -= 0.5
            reasons.append(f"時間差 {gap // 60} 小時")
        else:
            reasons.append(f"時間只差 {gap} 分鐘")
    if a["date"] != b["date"]:
        reasons.append("日期差一天")
    return round(s, 2), reasons


def _same_amount(a: dict, b: dict) -> bool:
    if a["amount"] != b["amount"]:
        if a.get("foreign_amount") and b.get("foreign_amount") and a.get("currency") == b.get("currency"):
            fa, fb = float(a["foreign_amount"]), float(b["foreign_amount"])
            return abs(fa - fb) <= max(abs(fa), abs(fb)) * 0.01
        return False
    return True


def _candidates(conn: sqlite3.Connection, row: dict) -> list[dict]:
    d = date.fromisoformat(row["date"])
    rows = conn.execute(
        "SELECT rowid AS seq, * FROM transactions WHERE status = 'active' AND kind = ? AND id <> ? AND batch_id <> ? "
        "AND date BETWEEN ? AND ?",
        (row["kind"], row["id"], row["batch_id"], (d - timedelta(days=1)).isoformat(),
         (d + timedelta(days=1)).isoformat())).fetchall()
    return [row_dict(r) for r in rows if _same_amount(row, row_dict(r))]


def check(w: Writer, txn_id: str) -> dict[str, Any] | None:
    """新寫入的一筆跟既有的比；最像的那筆 ≥ 門檻就建一組（a＝較舊）。回那一組，或 None。"""
    row = row_dict(w.conn.execute("SELECT rowid AS seq, * FROM transactions WHERE id = ?", (txn_id,)).fetchone())
    if row is None or row["kind"] == "transfer":
        return None
    best = None
    for other in _candidates(w.conn, row):
        s, reasons = score(other, row)
        if s >= THRESHOLD and (best is None or s > best[0]):
            best = (s, reasons, other)
    if best is None:
        return None
    s, reasons, other = best
    a, b = (other, row) if other["seq"] < row["seq"] else (row, other)       # rowid＝寫入順序（同一秒也分得出先後）
    if w.conn.execute("SELECT 1 FROM dedup_pairs WHERE a_id = ? AND b_id = ?", (a["id"], b["id"])).fetchone():
        return None
    pid = w.conn.execute("INSERT INTO dedup_pairs (a_id, b_id, score, reason, created_at) VALUES (?,?,?,?,?)",
                         (a["id"], b["id"], s, "、".join(reasons), w.now)).lastrowid
    after = row_dict(w.conn.execute("SELECT * FROM dedup_pairs WHERE id = ?", (pid,)).fetchone())
    w.audit("create", "dedup_pairs", pid, None, after)
    return {"pair_id": pid, "score": s, "reason": after["reason"], "duplicate_of": tx.get(w.conn, other["id"])}


def scan(w: Writer, *, days: int = 60) -> list[dict[str, Any]]:
    """把最近幾天的每一筆都再比一次（網站關著時 CLI 寫進來、或門檻調過之後補抓）。"""
    from .. import clock
    since = (clock.today() - timedelta(days=days)).isoformat()
    ids = [r[0] for r in w.conn.execute("SELECT id FROM transactions WHERE status='active' AND kind <> 'transfer' "
                                        "AND date >= ? ORDER BY created_at", (since,))]
    found = []
    for tid in ids:
        pair = check(w, tid)
        if pair:
            found.append(pair)
    return found


def _close_stale(conn: sqlite3.Connection) -> None:
    """其中一筆已經被刪掉的組，自動當成處理完了（不再出現在待確認）。只改狀態、不留 audit。"""
    conn.execute("UPDATE dedup_pairs SET status = 'deleted', resolved_by = 'auto' WHERE status = 'pending' AND ("
                 "a_id IN (SELECT id FROM transactions WHERE status='deleted') OR "
                 "b_id IN (SELECT id FROM transactions WHERE status='deleted'))")


def pending(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT p.* FROM dedup_pairs p JOIN transactions a ON a.id = p.a_id JOIN transactions b ON b.id = p.b_id "
        "WHERE p.status = 'pending' AND a.status = 'active' AND b.status = 'active' ORDER BY p.created_at").fetchall()
    return [{"pair_id": r["id"], "score": r["score"], "reason": r["reason"], "created_at": r["created_at"],
             "a": tx.get(conn, r["a_id"]), "b": tx.get(conn, r["b_id"])} for r in rows]


def resolve(w: Writer, pair_id: int, action: str, *, expect_a: str | None = None,
            expect_b: str | None = None) -> dict[str, Any]:
    if action not in ACTIONS:
        raise InvalidInput(f"處理方式「{action}」不對", hint="keep_both（兩筆都留）／delete_newer（刪較新的）／merge（合併）")
    _close_stale(w.conn)
    before = row_dict(w.conn.execute("SELECT * FROM dedup_pairs WHERE id = ?", (pair_id,)).fetchone())
    if before is None:
        raise NotFound(f"找不到可能重複的第 {pair_id} 組", hint="重新找一次、看還沒處理的：/money_dedup")
    if before["status"] != "pending":
        raise Conflict(f"第 {pair_id} 組已經處理過了（{before['status']}）")
    a, b = tx.get(w.conn, before["a_id"]), tx.get(w.conn, before["b_id"])
    if (expect_a and expect_a != a["updated_at"]) or (expect_b and expect_b != b["updated_at"]):
        raise Conflict("這兩筆在你看的時候之後又被改過了", hint="重新整理再決定一次")
    status = {"keep_both": "kept_both", "delete_newer": "deleted", "merge": "merged"}[action]
    kept = a
    if action == "delete_newer":
        tx.delete(w, b["id"])
    elif action == "merge":
        changes: dict[str, Any] = {"confidence": None}
        if not a["time"] and b["time"]:
            changes["time"] = b["time"]
        if not a["merchant"] and b["merchant"]:
            changes["merchant"] = b["merchant"]
        if not a["category_id"] and b["category_id"]:
            changes["category_id"] = b["category_id"]
        if b["note"] and b["note"] not in a["note"]:
            changes["note"] = (a["note"] + "；" if a["note"] else "") + b["note"]
        if len(b["desc"]) > len(a["desc"]):
            changes["description"] = b["desc"]
        if not a["items"] and b["items"]:
            changes["items"] = b["items"]
        kept = tx.update(w, a["id"], **changes)
        for att in w.conn.execute("SELECT id FROM attachments WHERE txn_id = ?", (b["id"],)).fetchall():
            w.conn.execute("UPDATE attachments SET txn_id = ? WHERE id = ?", (a["id"], att["id"]))
        tx.delete(w, b["id"])
    w.conn.execute("UPDATE dedup_pairs SET status = ?, resolved_at = ?, resolved_by = ? WHERE id = ?",
                   (status, w.now, w.actor, pair_id))
    after = row_dict(w.conn.execute("SELECT * FROM dedup_pairs WHERE id = ?", (pair_id,)).fetchone())
    w.audit("update", "dedup_pairs", pair_id, before, after)
    return {"pair_id": pair_id, "status": status, "kept": tx.get(w.conn, kept["id"])}
