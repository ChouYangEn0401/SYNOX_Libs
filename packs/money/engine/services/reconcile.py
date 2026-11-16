"""對明細：**最後對到哪一天**、有沒有記錯或漏算，加一個檢查模式。

兩邊各有一個「最後核對日」：
- 記帳＝每個帳戶最後一次核對餘額（`balance_snapshots`，/money_balance）；核對之後又記了幾筆、差了多少。
- 股票＝每一檔最後一次對過券商庫存的實際股數（`holdings_counts`，/money_count）；跟照交易明細算出來的股數比。

`status()` 是每一邊的現況（給網站卡片與 CLI），`check()` 是檢查模式：只讀、不改任何資料，
列出「這裡可能記錯或漏算」——放空（賣比買多，多半漏記買進或配股）、股數對不上、收掉的倉還有沒平倉的、
很久沒核對、核對時帳裡差錢。規矩跟 conflicts 一樣：只列，不自己解決。

不推算、不猜（對帳單匯入原則）：缺的股數由使用者看券商庫存告訴我們，這裡只存他說的數字。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from .. import clock
from ..db import Writer, row_dict
from ..errors import InvalidInput
from . import balances, books, refdata, trades
from . import transactions as tx

STALE_DAYS = 35                      # 帳戶超過這麼多天沒核對餘額，檢查模式就提醒
LEVEL_ORDER = {"bad": 0, "warn": 1, "info": 2}


def _days_ago(iso: str | None) -> int | None:
    return (clock.today() - date.fromisoformat(iso)).days if iso else None


def _item(key: str, level: str, title: str, why: str, tab: str, hint: str = "") -> dict[str, Any]:
    return {"key": key, "level": level, "title": title, "why": why, "where": {"tab": tab, "hint": hint}}


# ---------------------------------------------------------------- 記帳：核對餘額

def ledger_status(conn: sqlite3.Connection) -> dict[str, Any]:
    out, today = [], clock.today().isoformat()
    for a in refdata.accounts(conn):
        snaps = conn.execute("SELECT as_of, balance FROM balance_snapshots WHERE account_id = ? AND as_of <= ? "
                             "ORDER BY as_of DESC LIMIT 2", (a["id"], today)).fetchall()
        moves = conn.execute("SELECT COUNT(*), MAX(date) FROM transactions WHERE status = 'active' "
                             "AND (account_id = ? OR to_account_id = ?) AND date > ?",
                             (a["id"], a["id"], snaps[0]["as_of"] if snaps else "0000-01-01")).fetchone()
        if not snaps and not moves[0]:
            continue                                              # 沒核對過、也沒有交易的帳戶不用管
        diff = None
        if len(snaps) == 2:                                       # 只有一次＝起始餘額，沒得比
            last, prev = snaps[0], snaps[1]
            moved = conn.execute(balances._effect_sql(), {"a": a["id"], "since": prev["as_of"], "until": last["as_of"]}).fetchone()[0]
            diff = last["balance"] - (prev["balance"] + moved)
        out.append({"account": a["name"], "kind": a["kind"], "last_checked": snaps[0]["as_of"] if snaps else None,
                    "days": _days_ago(snaps[0]["as_of"]) if snaps else None, "diff": diff,
                    "since": moves[0], "last_txn": moves[1]})
    checked = [x["last_checked"] for x in out if x["last_checked"]]
    out.sort(key=lambda x: (x["last_checked"] or "", x["account"]))      # 最久沒核對的在前面
    return {"last_checked": max(checked) if checked else None, "oldest_checked": min(checked) if checked else None,
            "never": [x["account"] for x in out if not x["last_checked"]], "accounts": out}


# ---------------------------------------------------------------- 股票：股數核對

def _stock_account_ids(conn: sqlite3.Connection) -> dict[int, str]:
    return {a["id"]: a["name"] for layer in books.LAYERS for a in books.layer_accounts(conn, layer)}


def count(w: Writer, *, account: str, symbol: str, shares: Any, as_of: str | None = None, note: str = "",
          source: str = "cli") -> dict[str, Any]:
    """記下「我對過券商庫存，這一檔那天實際是 N 股」（0＝已經沒有了）。同一天重打會蓋掉；回跟帳上算的差多少。"""
    acc = refdata.resolve_account(w.conn, account)
    sym = (symbol or "").strip().upper()
    if not sym:
        raise InvalidInput("要寫哪一檔（代號）", fields={"symbol": "必填"})
    try:
        n = int(str(shares).replace(",", "").strip())
    except ValueError:
        raise InvalidInput("股數要是整數（0＝已經沒有了）", fields={"shares": "整數"}) from None
    if n < 0:
        raise InvalidInput("股數不能是負的；券商庫存沒有「放空」的話就寫 0", fields={"shares": "≥ 0"})
    day = tx.parse_date(as_of or clock.today().isoformat(), "as_of")
    before = row_dict(w.conn.execute("SELECT * FROM holdings_counts WHERE account_id = ? AND symbol = ? AND as_of = ?",
                                     (acc["id"], sym, day)).fetchone())
    if before and before["shares"] == n and before["note"] == note:
        cid = before["id"]                                            # 一模一樣：不重寫
    elif before:
        w.conn.execute("UPDATE holdings_counts SET shares = ?, note = ?, source = ?, batch_id = ? WHERE id = ?",
                       (n, note, source, w.batch_id, before["id"]))
        cid = before["id"]
    else:
        cid = w.conn.execute("INSERT INTO holdings_counts (account_id, symbol, as_of, shares, note, source, batch_id, created_at) "
                             "VALUES (?,?,?,?,?,?,?,?)", (acc["id"], sym, day, n, note, source, w.batch_id, w.now)).lastrowid
    after = row_dict(w.conn.execute("SELECT * FROM holdings_counts WHERE id = ?", (cid,)).fetchone())
    if after != before:
        w.audit("update" if before else "create", "holdings_counts", cid, before, after)
    derived = _derived_at(w.conn, acc["id"], sym, day)
    return {"id": cid, "account": acc["name"], "symbol": sym, "as_of": day, "counted": n, "derived": derived,
            "diff": n - derived, "match": n == derived}


def _derived_at(conn: sqlite3.Connection, account_id: int, symbol: str, day: str) -> int:
    shares = 0
    for r, _realized, _before, book in trades.walk(trades.rows(conn, account_ids=[account_id])):
        if r["date"] > day:
            break
        if (r["symbol"] or "").upper() == symbol:
            shares = book[(account_id, trades.key_of(r))].shares
    return shares


def stock_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """每一檔：照明細算出來幾股、最後對過幾股（哪天）、對不對得上。一次走過交易（cursor），不整包載入。"""
    ids = _stock_account_ids(conn)
    if not ids:
        return {"last_trade": None, "last_counted": None, "items": []}
    counts: dict[tuple[int, str], dict[str, Any]] = {}
    for r in conn.execute("SELECT * FROM holdings_counts ORDER BY as_of, id"):
        counts[(r["account_id"], r["symbol"])] = dict(r)               # 同一檔留最新那次
    at_count: dict[tuple[int, str], int] = {}
    first_short: dict[tuple[int, str], str] = {}
    final: dict[tuple[int, str], trades.Position] = {}
    last_trade, moved_after = None, set()
    for r, _realized, _before, book in trades.walk(trades.rows(conn, account_ids=list(ids))):
        if r["instrument"] != "stock":
            continue
        last_trade = r["date"]
        k = (r["account_id"], (r["symbol"] or "").upper())
        raw = (r["account_id"], trades.key_of(r))
        pos = book[raw]
        if pos.shares < 0 and k not in first_short:
            first_short[k] = r["date"]
        c = counts.get(k)
        if c and r["date"] <= c["as_of"]:
            at_count[k] = pos.shares
        elif c:
            moved_after.add(k)                                         # 對過之後又有交易
        final = book
    bk = {b["id"]: b for b in conn.execute("SELECT id, name, status FROM books")}
    items, seen = [], set()
    for raw, pos in final.items():
        if pos.instrument != "stock":
            continue
        k = (raw[0], (pos.symbol or "").upper())
        seen.add(k)
        c = counts.get(k)
        if not pos.shares and not c:
            continue                                                   # 早就出清、也沒人對過：不用列
        state = ("short" if pos.shares < 0 else "uncounted") if not c else (
            "ok" if at_count.get(k, 0) == c["shares"] else "mismatch")
        book = bk.get(pos.book_id)
        items.append({"account": ids[raw[0]], "symbol": pos.symbol, "name": pos.name, "key": raw[1], "derived": pos.shares,
                      "side": "short" if pos.shares < 0 else ("long" if pos.shares else None), "state": state,
                      "counted": c["shares"] if c else None, "counted_on": c["as_of"] if c else None,
                      "derived_at_count": at_count.get(k, 0) if c else None,
                      "diff": (c["shares"] - at_count.get(k, 0)) if c else None,
                      "moved_since": k in moved_after, "first_short": first_short.get(k), "last_date": pos.last_date,
                      "book": book["name"] if book else None, "book_status": book["status"] if book else None})
    order = {"mismatch": 0, "short": 1, "uncounted": 2, "ok": 3}
    items.sort(key=lambda i: (order[i["state"]], i["symbol"] or ""))
    dates = [c["as_of"] for c in counts.values()]
    return {"last_trade": last_trade, "last_counted": max(dates) if dates else None, "items": items}


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    return {"today": clock.today().isoformat(), "ledger": ledger_status(conn), "stocks": stock_status(conn)}


# ---------------------------------------------------------------- 檢查模式

def check(conn: sqlite3.Connection, st: dict[str, Any] | None = None) -> dict[str, Any]:
    """檢查模式：只讀。回 {count, items, headline, status}；items 紅的在前。"""
    st = st or status(conn)
    items: list[dict[str, Any]] = []
    for a in st["ledger"]["accounts"]:
        name = a["account"]
        if not a["last_checked"]:
            items.append(_item(f"ledger:never:{name}", "warn", f"{name}還沒核對過餘額",
                               f"這個帳戶有記帳，但從來沒有對過網銀或存摺的餘額，記錯或漏算不會被發現。", "book",
                               f"看一眼網銀或存摺的餘額，用 /money_balance 核對 {name}"))
            continue
        if a["diff"]:
            items.append(_item(f"ledger:diff:{name}", "warn", f"{name}上次核對時，帳裡差了 {abs(a['diff']):,}",
                               f"最近兩次核對之間，帳戶實際{'少' if a['diff'] < 0 else '多'}了 {abs(a['diff']):,}，帳裡沒有記到。",
                               "book", "想起來是什麼就補記；想不起來就用 /money_balance 補一筆未記差額"))
        if a["since"] and a["days"] is not None and a["days"] > STALE_DAYS:
            items.append(_item(f"ledger:stale:{name}", "info", f"{name}已經 {a['days']} 天沒核對",
                               f"最後核對是 {a['last_checked']}，之後記了 {a['since']} 筆；久了不容易找出哪一筆記錯。",
                               "book", "對一次餘額"))
    for i in st["stocks"]["items"]:
        label = f"{i['symbol']} {i['name']}".strip() or i["key"]
        if i["state"] == "mismatch":
            items.append(_item(f"stock:mismatch:{i['symbol']}", "bad", f"{label}股數對不上",
                               f"{i['counted_on']} 你對過券商是 {i['counted']:,} 股，照交易明細算是 {i['derived_at_count']:,} 股"
                               f"（差 {abs(i['diff']):,}）。多半是漏記買進／賣出、配股或零股。", "piggy",
                               "找那段時間的對帳單補記缺的；不要為了補平自己湊一筆"))
        elif i["state"] == "short":
            items.append(_item(f"stock:short:{i['symbol']}", "warn", f"{label}帳上是放空 {abs(i['derived']):,} 股",
                               f"從 {i['first_short']} 起賣出比買進多。你如果沒有放空，就是漏記了買進、配股或零股。", "piggy",
                               "對券商庫存，把實際股數記下來（撲滿的「部位狀況」或 /money_count）"))
        elif i["state"] == "uncounted" and i["derived"]:
            items.append(_item(f"stock:uncounted:{i['symbol']}", "info", f"{label}還沒對過券商庫存",
                               f"帳上算還有 {i['derived']:,} 股，但從沒核對過實際股數。", "piggy",
                               "對一次，把實際股數記下來"))
        if i["derived"] and i["book_status"] in ("closed", "archived"):
            items.append(_item(f"stock:closedbook:{i['symbol']}", "warn", f"{label}在已收的倉「{i['book']}」裡還沒平倉",
                               "收倉的結果已經寫死、不含這檔；它可能是真的還持有，也可能是賣出沒記全。", "piggy",
                               "對券商庫存後決定：真的持有就留著，不然補記缺的交易"))
    items.sort(key=lambda x: LEVEL_ORDER[x["level"]])
    bad = sum(1 for x in items if x["level"] == "bad")
    head = ("沒有發現要對的地方" if not items else f"有 {len(items)} 處要對" + (f"（{bad} 處對不上）" if bad else ""))
    return {"count": len(items), "bad": bad, "headline": head, "items": items, "status": st}
