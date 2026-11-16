"""塞進來的價格：我們不抓股價，誰要給就塞進一張清單；按「查詢當下價格」才去看有沒有。

設計：預設什麼都不算；使用者主動按「查詢當下價格」才去看有沒有人塞過資料。沒有就照舊顯示「沒有資料」、
畫面不變；有就顯示，並記下那是什麼時間點的價格。

- `push`：一次塞好幾檔（指令 `/money_price_push TEST1=101.5 TEST2=33`）；同一檔同一個時間點重塞＝更新。
- `latest`：每一檔最新的那一筆（價格、時間、誰塞的）。只回被問到的那幾檔。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable

from .. import clock
from ..db import Writer, row_dict
from ..errors import InvalidInput

PAIR_RE = re.compile(r"^\s*([0-9A-Za-z.\-]{1,20})\s*[=:]\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*$")
AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")


def parse_pairs(items: Iterable[str]) -> dict[str, str]:
    """「TEST1=101.5」「TEST2:33」→ {代號: 價格}。"""
    out: dict[str, str] = {}
    for item in items:
        for part in str(item).replace("、", " ").replace("；", " ").replace(";", " ").split():   # 逗號是千分位，不當分隔
            if not part.strip():
                continue
            m = PAIR_RE.match(part)
            if not m:
                raise InvalidInput(f"「{part.strip()}」看不懂", hint="寫成 代號=價格，例 TEST1=101.5")
            price = float(m.group(2).replace(",", ""))
            if price <= 0:
                raise InvalidInput(f"{m.group(1)} 的價格要大於 0")
            out[m.group(1).upper()] = f"{price:.4f}".rstrip("0").rstrip(".")
    if not out:
        raise InvalidInput("沒有價格", hint="例：/money_price_push TEST1=101.5 TEST2=33")
    return out


def push(w: Writer, prices: dict[str, Any], *, as_of: str | None = None, source: str = "cli") -> list[dict[str, Any]]:
    at = (as_of or clock.now_iso()[:16]).replace("T", " ")
    if not AT_RE.match(at):
        raise InvalidInput(f"時間「{at}」看不懂", hint="YYYY-MM-DD 或 YYYY-MM-DD HH:MM")
    out = []
    for symbol, price in parse_pairs([f"{k}={v}" for k, v in prices.items()]).items():
        same = row_dict(w.conn.execute("SELECT * FROM quotes WHERE symbol = ? AND as_of = ?", (symbol, at)).fetchone())
        if same and same["price"] == price:
            out.append({**same, "status": "same"})
            continue
        if same:
            w.conn.execute("UPDATE quotes SET price = ?, source = ?, batch_id = ? WHERE id = ?", (price, source, w.batch_id, same["id"]))
            qid = same["id"]
        else:
            qid = w.conn.execute("INSERT INTO quotes (symbol, price, as_of, source, batch_id, created_at) VALUES (?,?,?,?,?,?)",
                                 (symbol, price, at, source, w.batch_id, w.now)).lastrowid
        after = row_dict(w.conn.execute("SELECT * FROM quotes WHERE id = ?", (qid,)).fetchone())
        w.audit("update" if same else "create", "quotes", qid, same, after)
        out.append({**after, "status": "updated" if same else "created"})
    return out


def latest(conn: sqlite3.Connection, symbols: Iterable[str]) -> dict[str, dict[str, Any]]:
    """每一檔最新塞進來的價格；沒有的不在結果裡。"""
    wanted = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})[:200]
    if not wanted:
        return {}
    marks = ", ".join("?" * len(wanted))
    sql = (f"SELECT q.* FROM quotes q JOIN (SELECT symbol, MAX(as_of) AS at FROM quotes WHERE symbol IN ({marks}) "
           "GROUP BY symbol) m ON m.symbol = q.symbol AND m.at = q.as_of ORDER BY q.id DESC")
    out: dict[str, dict[str, Any]] = {}
    for r in conn.execute(sql, wanted):
        out.setdefault(r["symbol"], {"symbol": r["symbol"], "price": float(r["price"]), "as_of": r["as_of"], "source": r["source"]})
    return out


def needed(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """要塞價格的清單：投資與操盤現在還持有的每一檔（模擬倉不算），附最近一次塞的價格與時間。
    給別的程式用：先讀 quotes_needed 拿清單，查好價格再 `/money_price_push 代號=價格 …`。"""
    from . import books, trades
    held: dict[str, dict[str, Any]] = {}
    for layer in books.LAYERS:
        names = [a["name"] for a in books.layer_accounts(conn, layer)]
        if not names:
            continue
        for i in trades.positions(conn, accounts=names)["items"]:
            if i["shares"] > 0 and i["symbol"]:
                h = held.setdefault(i["symbol"].upper(), {"symbol": i["symbol"].upper(), "name": i["name"], "shares": 0, "layers": [],
                                                         "unit": i["unit"], "multiplier": i["multiplier"]})
                h["shares"] += i["shares"]
                if layer not in h["layers"]:
                    h["layers"].append(layer)
    last = latest(conn, held)
    out = []
    for sym, h in sorted(held.items()):
        q = last.get(sym)
        out.append({**h, "last_price": q["price"] if q else None, "last_at": q["as_of"] if q else None})
    return out


def recent(conn: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
    return [row_dict(r) for r in conn.execute("SELECT * FROM quotes ORDER BY as_of DESC, id DESC LIMIT ?", (limit,))]
