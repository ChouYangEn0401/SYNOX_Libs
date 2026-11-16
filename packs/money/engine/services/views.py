"""畫面共用的小零件：期別導覽（上一期、下一期、本期）。六個用途的名字在資料庫（plans.bucket_names）。

整包畫面資料在 services/home.py（首頁、一年）與 services/wallet.py（存摺）。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import clock
from . import periods


def period_nav(conn: sqlite3.Connection, key: str | None) -> dict[str, Any]:
    p = periods.get(conn, key)
    today = clock.today()
    prev = periods.shift(conn, p, -1)
    nxt = periods.shift(conn, p, 1) if p.end < today else None
    return {"period": p.to_dict(today), "prev": prev.key, "next": nxt.key if nxt else None,
            "current": periods.current(conn).key}
