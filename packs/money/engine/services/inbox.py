"""待確認：需要使用者看一眼的東西（每個 CLI JSON 回應都附上數量）。

- uncategorized：還沒分類的支出
- low_confidence：agent 自評信心 < 0.6、還沒有人改過的（人改過就清掉信心值）
- duplicates：可能重複、還沒處理的組（P3）
"""
from __future__ import annotations

import sqlite3
from typing import Any

LOW_CONFIDENCE = 0.6


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    unc = conn.execute("SELECT COUNT(*) FROM transactions WHERE status='active' AND kind='expense' "
                       "AND category_id IS NULL").fetchone()[0]
    low = conn.execute("SELECT COUNT(*) FROM transactions WHERE status='active' AND confidence IS NOT NULL "
                       "AND confidence < ?", (LOW_CONFIDENCE,)).fetchone()[0]
    dup = 0
    if _table_exists(conn, "dedup_pairs"):
        dup = conn.execute(
            "SELECT COUNT(*) FROM dedup_pairs p JOIN transactions a ON a.id = p.a_id JOIN transactions b ON b.id = p.b_id "
            "WHERE p.status='pending' AND a.status='active' AND b.status='active'").fetchone()[0]
    return {"duplicates": dup, "uncategorized": unc, "low_confidence": low, "total": dup + unc + low}


def details(conn: sqlite3.Connection, limit: int = 10) -> dict[str, Any]:
    from . import transactions as tx
    out: dict[str, Any] = {"counts": counts(conn)}
    out["uncategorized"] = tx.query(conn, uncategorized=True, limit=limit)["items"]
    rows = conn.execute("SELECT id FROM transactions WHERE status='active' AND confidence IS NOT NULL "
                        "AND confidence < ? ORDER BY date DESC LIMIT ?", (LOW_CONFIDENCE, limit)).fetchall()
    out["low_confidence"] = [tx.get(conn, r["id"]) for r in rows]
    if _table_exists(conn, "dedup_pairs"):
        from . import dedup
        out["duplicates"] = dedup.pending(conn)[:limit]
    return out
