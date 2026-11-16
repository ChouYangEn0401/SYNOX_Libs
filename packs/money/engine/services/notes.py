"""小日記：每天一段話（舊試算表的「小日記」欄）。預設接在原本的後面，replace 才整段換掉。"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from ..db import Writer, row_dict
from ..errors import InvalidInput
from .transactions import parse_date

MAX_NOTE = 1000
JOIN = "；"


def put(w: Writer, day: Any, text: str, *, replace: bool = False) -> dict[str, Any]:
    key = parse_date(day)
    text = (text or "").strip()
    if not text:
        raise InvalidInput("小日記沒有內容", fields={"note": "空的"})
    before = row_dict(w.conn.execute("SELECT * FROM day_notes WHERE date = ?", (key,)).fetchone())
    if before and not replace:
        if text in before["note"].split(JOIN):
            return {"date": key, "note": before["note"], "changed": False}
        text = before["note"] + JOIN + text
    if len(text) > MAX_NOTE:
        raise InvalidInput(f"小日記太長（超過 {MAX_NOTE} 字）")
    w.conn.execute("INSERT INTO day_notes (date, note, updated_at, batch_id) VALUES (?,?,?,?) "
                   "ON CONFLICT(date) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at, "
                   "batch_id = excluded.batch_id", (key, text, w.now, w.batch_id))
    after = row_dict(w.conn.execute("SELECT * FROM day_notes WHERE date = ?", (key,)).fetchone())
    w.audit("update" if before else "create", "day_notes", key, before, after)
    return {"date": key, "note": text, "changed": True}


def get(conn: sqlite3.Connection, day: str) -> str | None:
    row = conn.execute("SELECT note FROM day_notes WHERE date = ?", (day,)).fetchone()
    return row[0] if row else None


def between(conn: sqlite3.Connection, start: date | str, end: date | str) -> dict[str, str]:
    rows = conn.execute("SELECT date, note FROM day_notes WHERE date BETWEEN ? AND ? ORDER BY date",
                        (str(start), str(end)))
    return {r["date"]: r["note"] for r in rows}
