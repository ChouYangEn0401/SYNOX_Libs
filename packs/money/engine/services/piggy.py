"""撲滿頁要的整包資料：存下來的錢長怎樣。

為什麼多這一頁：總覽回答的是「今天還能不能花」，那是**每天**的事；投資與操盤是**每月／每年**的事，
塞進總覽只會把中心數字吵掉。總覽只放一張「撲滿待處理」（捐贈、該收的倉）和一行目前 vs 紅線（`brief`）。

四層（帳戶依「錢放在哪一種水平」分層）：
- `cash` 現金帳戶：大數字是**分到最後剩下的**＝結餘 − 還沒做完的用途要留的 − 生活費還剩的 − 卡費要留的
- `invest` 投資：目前已有＝證券戶現金（核對過或從第一筆轉帳算起才算得出來）＋持股成本；跟 基準尺度比、藍圖
- `trading` 資金操盤：現值、每一倉的操作金額／可虧損／違規、本月可虧損；跟基準比（停損額當成永遠消失的錢）
- `property` 產：帳戶的餘額

記憶體（資料可能很多，載入時要控制記憶體用量）：交易明細與個股比較都是分頁的另一支 API，
這一包只放算好的數字；走交易明細一律用 cursor 一列一列（trades.walk）。
"""
from __future__ import annotations

import itertools
import sqlite3
from datetime import date, timedelta
from typing import Any

from .. import clock
from ..db import Writer, row_dict
from ..errors import InvalidInput
from . import (balances, bench, books, cardcash, invest, periods, plans, quotes, refdata, settings,
                                stats, trades, views, wallet)

BUCKET_OF_LAYER = {"invest": "invest", "trading": "trading"}
HISTORY_KEEP = 12


def _layer_names(conn: sqlite3.Connection, layer: str) -> list[str]:
    return [a["name"] for a in books.layer_accounts(conn, layer)]


def levels(conn: sqlite3.Connection, day: str | None = None) -> list[dict[str, Any]]:
    """四層：每層有哪些帳戶、各自多少錢、小計。沒有帳戶的那層也列出來（才知道可以往裡面放東西）。"""
    day = day or clock.today().isoformat()
    accounts = refdata.accounts(conn)
    out = []
    for key, name in refdata.ASSET_CLASSES.items():
        items = []
        for a in accounts:
            if (a.get("asset_class") or "") != key:
                continue
            b = balances.balance_at(conn, a["id"], day)
            items.append({"id": a["id"], "name": a["name"], "kind": a["kind"], "purpose": a["purpose"],
                          "balance": b["balance"], "verified_on": b["verified_on"]})
        out.append({"key": key, "name": name, "accounts": items, "count": len(items),
                    "total": sum(i["balance"] for i in items),
                    "unverified": sum(1 for i in items if not i["verified_on"])})
    return out


# ---------------------------------------------------------------- 現金層：分到最後剩下的

def cash_view(conn: sqlite3.Connection, period_key: str | None = None) -> dict[str, Any]:
    """結餘 − 還沒做完的用途要留的（固定、捐贈、存錢、滾存、還沒到期的分期…）− 生活費還剩的 − 卡費要留的＝剩下的。"""
    p = periods.get(conn, period_key)
    today = clock.today()
    day = min(today, p.end).isoformat()
    cash = [x for x in levels(conn, day) if x["key"] == "cash"][0]
    w = wallet.summary(conn, p.key)
    lv = stats.period_summary(conn, p.key)["living"]
    living_left = max(int(lv["remaining"] or 0), 0) if lv["budget"] else 0
    cards = [c for c in cardcash.all_cards(conn) if c.get("ready")] if p.end >= today else []
    # 卡費要留＝每張卡「這期帳單還沒繳的」＋「下期已經刷的」；分開列，才不會跟「還沒做完的用途」搞混
    card_items = [{"account": c["account"], "bill": int((c.get("bill") or {}).get("left") or 0),
                   "due": (c.get("bill") or {}).get("due"), "next": int((c.get("next") or {}).get("amount") or 0),
                   "closing": (c.get("next") or {}).get("closing"), "keep": int(c.get("keep") or 0)} for c in cards]
    card_keep = sum(c["keep"] for c in card_items)
    leftover = cash["total"] - w["reserved"] - living_left - card_keep
    hist = [row_dict(r) for r in conn.execute("SELECT * FROM cash_leftover ORDER BY period_key DESC LIMIT ?", (HISTORY_KEEP,))]
    return {"period": p.key, "balance": cash["total"], "accounts": cash["accounts"], "unverified": cash["unverified"],
            "reserved": w["reserved"], "reserved_items": w["reserved_items"], "living_left": living_left,
            "card_keep": card_keep, "card_items": card_items, "leftover": leftover, "history": hist}


def mark(w: Writer) -> dict[str, Any] | None:
    """把這一期的「剩下的」記一筆（每天 20:30 notify-job 跑一次）；數字沒變就不寫（不會每天多一個批次）。"""
    v = cash_view(w.conn)
    row = {"period_key": v["period"], "amount": v["leftover"], "balance": v["balance"], "keep": v["reserved"],
           "card_keep": v["card_keep"], "living_left": v["living_left"], "recorded_on": clock.today().isoformat(),
           "batch_id": w.batch_id}
    before = row_dict(w.conn.execute("SELECT * FROM cash_leftover WHERE period_key = ?", (v["period"],)).fetchone())
    same = before and all(before[k] == row[k] for k in ("amount", "balance", "keep", "card_keep", "living_left"))
    if same:
        return {**before, "status": "same"}
    if before:
        w.conn.execute("UPDATE cash_leftover SET amount = ?, balance = ?, keep = ?, card_keep = ?, living_left = ?, "
                       "recorded_on = ?, batch_id = ? WHERE period_key = ?",
                       (row["amount"], row["balance"], row["keep"], row["card_keep"], row["living_left"],
                        row["recorded_on"], w.batch_id, v["period"]))
    else:
        w.conn.execute(f"INSERT INTO cash_leftover ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    after = row_dict(w.conn.execute("SELECT * FROM cash_leftover WHERE period_key = ?", (v["period"],)).fetchone())
    w.audit("update" if before else "create", "cash_leftover", v["period"], before, after)
    return {**after, "status": "updated" if before else "created"}


# ---------------------------------------------------------------- 投資／操盤：目前已有、跟基準比

def _anchor(conn: sqlite3.Connection, account_id: int) -> tuple[str | None, bool]:
    """從哪一天起算得出這個帳戶的現金：核對過＝最近一次核對那天（之後的才算，inclusive=False）；
    沒核對＝記帳裡第一筆碰到它的那天（當天起算，inclusive=True）；都沒有＝None。"""
    b = balances.balance_at(conn, account_id)
    if b["verified_on"]:
        return b["verified_on"], False
    first = conn.execute("SELECT MIN(date) FROM transactions WHERE status = 'active' AND (account_id = ? OR to_account_id = ?)",
                         (account_id, account_id)).fetchone()[0]
    return first, True


def _holdings_before(conn: sqlite3.Connection, account_id: int, day: str, inclusive: bool) -> int:
    """那一天開始之前（或那一天結束時）這個帳戶已經持有的股票（用成本；放空的扣掉）。"""
    final: dict[str, trades.Position] = {}
    before = itertools.takewhile(lambda r: r["date"] < day if inclusive else r["date"] <= day,
                                 trades.rows(conn, account_ids=[account_id]))
    for _r, _realized, _before, final in trades.walk(before):
        pass
    return sum(p.cost if p.shares > 0 else -p.cost if p.shares < 0 else 0 for p in final.values())


def _flows(conn: sqlite3.Connection, ids: list[int]) -> list[tuple[str, int]]:
    return _flows_since(conn, ids)[0]


def _flows_since(conn: sqlite3.Connection, ids: list[int]) -> tuple[list[tuple[str, int]], dict[int, tuple[str, bool]]]:
    """跟基準比的「錢進出」：
    - 起算那天已經有的（核對過的現金＋已經持有的股票用成本）＝第一筆投入——不然開始用這本帳之前就買的股票會被當成「賺來的」；
    - 之後記帳裡轉進這一層＝投入（＋）、轉出去＝拿回來（−）；同一層帳戶之間互轉不算。"""
    if not ids:
        return [], {}
    out: list[tuple[str, int]] = []
    since: dict[int, tuple[str, bool]] = {}
    for aid in ids:
        day, inclusive = _anchor(conn, aid)
        if not day:
            continue
        since[aid] = (day, inclusive)
        start = _holdings_before(conn, aid, day, inclusive)
        if not inclusive:
            start += balances.balance_at(conn, aid, day)["balance"]
        if start:
            out.append((day, start))
    marks = ", ".join("?" * len(ids))
    sql = (f"SELECT date, amount, account_id, to_account_id FROM transactions WHERE status = 'active' AND kind = 'transfer' "
           f"AND (to_account_id IN ({marks}) OR account_id IN ({marks})) ORDER BY date, id")
    for r in conn.execute(sql, ids + ids):
        inside_from, inside_to = r["account_id"] in ids, r["to_account_id"] in ids
        if inside_from and inside_to:
            continue
        aid = r["to_account_id"] if inside_to else r["account_id"]
        day, inclusive = since.get(aid, (None, True))
        if day is None or (r["date"] < day if inclusive else r["date"] <= day):
            continue                                          # 核對那天以前的轉帳已經在核對的餘額裡
        out.append((r["date"], r["amount"] if inside_to else -r["amount"]))
    out.sort(key=lambda x: x[0])
    return out, since


def layer_value(conn: sqlite3.Connection, layer: str) -> dict[str, Any]:
    """目前已有＝證券戶現金＋持股成本（放空的扣掉）。現金只有算得出來的帳戶才算：
    核對過（餘額快照）＝快照＋之後的轉帳＋之後的交易；沒核對但記帳裡有轉帳＝從第一筆轉帳那天起算；都沒有＝不知道。"""
    accs = books.layer_accounts(conn, layer)
    pos = trades.positions(conn, accounts=[a["name"] for a in accs]) if accs else {"holding_cost": 0, "short_proceeds": 0, "open": 0}
    cash, known, unknown = 0, [], []
    for a in accs:
        b = balances.balance_at(conn, a["id"])
        anchor, op = b["verified_on"], ">"
        if not anchor:
            anchor = conn.execute("SELECT MIN(date) FROM transactions WHERE status = 'active' AND (account_id = ? OR to_account_id = ?)",
                                  (a["id"], a["id"])).fetchone()[0]
            op = ">="
        if not anchor:
            unknown.append(a["name"])
            continue
        moved = conn.execute(f"SELECT COALESCE(SUM(t.net), 0) FROM trades t LEFT JOIN books k ON k.id = t.book_id "
                             f"WHERE t.account_id = ? AND t.date {op} ? AND (k.kind IS NULL OR k.kind <> 'sim')",
                             (a["id"], anchor)).fetchone()[0]
        cash += b["balance"] + moved
        known.append(a["name"])
    value = cash + pos["holding_cost"] - pos["short_proceeds"]
    return {"cash": cash, "cash_known": bool(known) and not unknown, "cash_unknown": unknown,
            "holding_cost": pos["holding_cost"], "short": pos["short_proceeds"], "open": pos["open"], "value": value}


def _saved(conn: sqlite3.Connection, layer: str, period_key: str) -> dict[str, int]:
    bucket = BUCKET_OF_LAYER[layer]
    plan = plans.get(conn, period_key)["buckets"]
    actual = stats.period_summary(conn, period_key)["allocation"]["actual"]
    return {"month_saved": int(actual.get(bucket) or 0), "plan_monthly": int(plan.get(bucket) or 0)}


def _series(conn: sqlite3.Connection, layer: str, flows: list[tuple[str, int]], rate: float,
            since: dict[int, tuple[str, bool]]) -> list[dict[str, Any]]:
    """過去每個月底一點：累計投入、基準紅線（影子池）、實際（投入＋起算之後實現的損益，持股用成本）。
    起算那天已經持有的股票算在投入裡，所以實際＝投入＋之後的損益，跟「目前已有」對得起來。"""
    if not flows:
        return []
    first = date.fromisoformat(flows[0][0])
    today = clock.today()
    ids = list(since)
    results: list[tuple[str, int]] = []
    total = 0
    for r, realized, _before, _book in trades.walk(trades.rows(conn, account_ids=ids)):
        day, inclusive = since[r["account_id"]]
        if r["date"] < day or (not inclusive and r["date"] == day):
            continue
        total += r["net"] if r["action"] in ("dividend", "fee", "other") else realized
        results.append((r["date"], total))
    points, d = [], first
    while True:
        y, m = (d.year + (d.month // 12), d.month % 12 + 1)
        month_end = date(y, m, 1) - timedelta(days=1)
        points.append(min(month_end, today))
        if month_end >= today:
            break
        d = month_end + timedelta(days=1)
    pot, fi, ri, put_in, result, out = bench.Pot(rate), 0, 0, 0, 0, []
    for pt in points:
        while fi < len(flows) and flows[fi][0] <= pt.isoformat():
            pot.move(date.fromisoformat(flows[fi][0]), flows[fi][1])
            put_in += flows[fi][1]
            fi += 1
        while ri < len(results) and results[ri][0] <= pt.isoformat():
            result = results[ri][1]
            ri += 1
        out.append({"date": pt.isoformat(), "put_in": put_in, "bench": round(pot.at(pt)), "actual": put_in + result})
    return out[-61:]


def _with_bench(conn: sqlite3.Connection, books_list: list[dict[str, Any]], rate: float) -> list[dict[str, Any]]:
    marks = books_bench(conn, books_list, rate)
    return [{**b, "bench": marks.get(b["id"])} for b in books_list]


def book_detail(conn: sqlite3.Connection, ref: Any) -> dict[str, Any]:
    """點進一倉：它的結果、規矩、跟基準比、還在場上的（成本）。明細與個股比較是另外分頁的 API（book=）。"""
    b = books.book_view(conn, ref)
    layer = b.get("layer") or ""
    cfg = bench.config(conn)
    pos = positions_view(conn, layer, b["id"]) if layer in books.LAYERS else {"items": []}
    derivatives = conn.execute("SELECT COUNT(*) FROM trades WHERE book_id = ? AND instrument <> 'stock'", (b["id"],)).fetchone()[0]
    return {"book": {**b, "bench": books_bench(conn, [b], cfg["rate"]).get(b["id"]), "derivatives": derivatives},
            "positions": pos, "bench": cfg,
            "daily": (1 + cfg["rate"]) ** (1 / 365) - 1}


def layer_view(conn: sqlite3.Connection, layer: str, period_key: str) -> dict[str, Any]:
    cfg = bench.config(conn)
    v = layer_value(conn, layer)
    ids = [a["id"] for a in books.layer_accounts(conn, layer)]
    flows, since = _flows_since(conn, ids)
    saved = _saved(conn, layer, period_key)
    cmp = bench.compare(flows, v["value"], cfg["rate"]) if flows and v["cash_known"] else None
    now = v["value"]
    monthly = saved["plan_monthly"] or saved["month_saved"]
    out = {"value": v, **saved, "monthly_used": monthly, "bench": {**cfg, "compare": cmp,
           "blueprint": bench.blueprint(now, monthly, cfg["rate"], cfg["low"]),
           "series": _series(conn, layer, flows, cfg["rate"], since),
           "future": bench.red_line(now, monthly, cfg["rate"]), "future_low": bench.red_line(now, monthly, cfg["low"])},
           "books": _with_bench(conn, books.list_books(conn, layer=layer), cfg["rate"]),
           "archived": conn.execute("SELECT COUNT(*) FROM books b JOIN accounts a ON a.id = b.account_id "
                                    "WHERE a.asset_class = ? AND b.status = 'archived'", (layer,)).fetchone()[0],
           "trades_count": trades.count(conn, accounts=[a["name"] for a in books.layer_accounts(conn, layer)])}
    if layer == "trading":
        out["month"] = books.month_status(conn)
        out["violations"] = books.violations(conn, layer="trading", limit=50)
    return out


def walk_handover(source):
    """跟 trades.walk 一樣一列一列走，另外給「交接」：一檔還在場上的股票，被另一倉的交易動到（例：2025 投資買、
    2026 投資賣），在那一天用成本交給新的那一倉——舊的倉拿回成本、新的倉放進成本。這樣每一倉跟基準比才公平
    （不然舊倉看起來全賠、新倉看起來白賺；reviewer 抓到，每一檔跨年的持股都會遇到）。
    給 (那一列, 實現的損益, 之前的股數, 部位, 交接或 None)；交接＝(舊倉, 新倉, 成本，放空是負的)。"""
    book: dict[tuple[int, str], trades.Position] = {}
    for r in source:
        k = (r["account_id"], trades.key_of(r))
        pos = book.get(k)
        if pos is None:
            pos = book[k] = trades.Position(symbol=r["symbol"], name=r["name"], account_id=r["account_id"],
                                            instrument=r["instrument"], multiplier=r["multiplier"])
        hand = None
        if (r["action"] in ("buy", "sell") and pos.shares and pos.book_id and r["book_id"]
                and pos.book_id != r["book_id"]):
            hand = (pos.book_id, r["book_id"], pos.cost if pos.shares > 0 else -pos.cost)
        before = pos.shares
        realized = trades.apply(pos, r)
        if r["action"] in ("buy", "sell"):
            pos.book_id = r["book_id"]
        yield r, realized, before, book, hand


def _walk_scope(conn: sqlite3.Connection, layer: str, book: dict[str, Any] | None):
    """走哪些交易：模擬倉只走自己的；不然走這一層所有帳戶的真交易（平均成本跨倉不斷）。"""
    if book and book["kind"] == "sim":
        return trades.rows(conn, book_id=book["id"])
    return trades.rows(conn, account_ids=[a["id"] for a in books.layer_accounts(conn, layer)])


def _end_of(last_date: str, still_open: bool) -> date:
    """跟基準比到哪一天：還在場上＝今天；已經出清＝最後一筆那天（之後錢拿走了，不再算它輸贏）。"""
    return clock.today() if still_open else date.fromisoformat(last_date)


def symbol_compare(conn: sqlite3.Connection, layer: str, *, use_quotes: bool = False, offset: int = 0, limit: int = 20,
                   show_hidden: bool = False, book_id: int | None = None) -> dict[str, Any]:
    """個股 vs 基準：每一檔一列，不管輸贏一直列著（按 ✕ 收起來的除外）。還在場上的在前面、新動過的在上面。
    給 book_id＝只看那一倉裡的交易。基準尺度照「天」滾：(1＋年率)^(持有天數/365)＝每天 (1＋年率)^(1/365)−1。"""
    cfg = bench.config(conn)
    book = books.get(conn, book_id) if book_id is not None else None
    per: dict[str, list[tuple[str, int]]] = {}
    first: dict[str, str] = {}
    final: dict[tuple[int, str], trades.Position] = {}
    for r, _realized, _before, final, hand in walk_handover(_walk_scope(conn, layer, book)):
        k = trades.key_of(r)
        if book is not None and hand and book["id"] in hand[:2]:          # 這一倉把持股交出去／接進來（用成本）
            per.setdefault(k, []).append((r["date"], -hand[2] if hand[0] == book["id"] else hand[2]))
            first.setdefault(k, r["date"])
        if book is not None and r["book_id"] != book["id"]:
            continue
        per.setdefault(k, []).append((r["date"], bench.trade_flow(r)))
        first.setdefault(k, r["date"])
    hidden = set(settings.get(conn, "piggy.hidden", []) or [])
    prices = quotes.latest(conn, [p.symbol for p in final.values() if p.shares > 0]) if use_quotes else {}
    merged: dict[str, dict[str, Any]] = {}                    # 同一檔在這一層好幾個帳戶：剩下的加起來比
    for (_acc, key), pos in final.items():
        if key not in per or key.upper() == cfg["symbol"].upper() or pos.symbol.upper() == cfg["symbol"].upper():
            continue
        if pos.instrument != "stock":                          # 期貨選擇權：名目金額不是投入的錢，不跟基準比
            continue
        mine = book is None or pos.book_id == book["id"]      # 還在場上的算給最後動到它的那一倉
        q = prices.get((pos.symbol or "").upper())
        remaining = 0 if not mine else (round(pos.shares * q["price"] * pos.multiplier) if q and pos.shares > 0
                                        else (pos.cost if pos.shares > 0 else -pos.cost if pos.shares < 0 else 0))
        m = merged.setdefault(key, {"symbol": pos.symbol, "name": pos.name, "shares": 0, "remaining": 0, "last_date": "", "priced_at": None})
        m["shares"] += pos.shares if mine else 0
        m["remaining"] += remaining
        m["last_date"] = max(m["last_date"], per[key][-1][0])
        m["name"] = m["name"] or pos.name
        if q and pos.shares > 0 and mine:
            m["priced_at"] = q["as_of"]
    items = []
    closed_on = (date.fromisoformat(book["closed_at"]) if book is not None and book["status"] != "open" and book.get("closed_at")
                 else None)
    for key, m in merged.items():
        still = m["shares"] != 0
        end = _end_of(m["last_date"], still)
        if closed_on and end > closed_on:
            end = closed_on                                   # 收掉的倉：每一檔也比到收倉那天，加起來才跟這一倉對得上
        c = bench.compare(sorted(per.get(key, [])), m["remaining"], cfg["rate"], today=end)
        items.append({"key": key, "symbol": m["symbol"], "name": m["name"], "shares": m["shares"], "open": still,
                      "last_date": m["last_date"], "since": first.get(key), "until": end.isoformat(),
                      "days": (end - date.fromisoformat(first.get(key) or end.isoformat())).days,
                      "priced_at": m["priced_at"], "hidden": f"{layer}:{key}" in hidden, **{k: v for k, v in c.items() if k != "since"}})
    shown = [i for i in items if show_hidden or not i["hidden"]]
    shown.sort(key=lambda i: i["last_date"], reverse=True)    # 新動過的在上面……
    shown.sort(key=lambda i: not i["open"])                   # ……還在場上的排最前面（sort 是穩定的）
    limit = max(1, min(int(limit), 100))
    return {"rate": cfg["rate"], "daily": (1 + cfg["rate"]) ** (1 / 365) - 1, "symbol": cfg["symbol"],
            "total": len(shown), "hidden": sum(1 for i in items if i["hidden"]),
            "beat": sum(1 for i in shown if i["beat"]), "diff": sum(i["diff"] for i in shown),
            "actual_gain": sum(i["actual_gain"] for i in shown), "shadow_gain": sum(i["shadow_gain"] for i in shown),
            "items": shown[offset:offset + limit], "offset": offset, "limit": limit}


def books_bench(conn: sqlite3.Connection, books_list: list[dict[str, Any]], rate: float) -> dict[int, dict[str, Any]]:
    """每一倉自己跟基準比：這一倉的買進＝投入、賣出股利＝拿回來，剩下的＝這一倉在場上的（成本）。
    收掉的倉比到收倉那天（之後錢已經拿走了）。一次走過，不是每一倉走一次。"""
    flows: dict[int, list[tuple[str, int]]] = {b["id"]: [] for b in books_list}
    real = {b["account_id"] for b in books_list if b["kind"] != "sim"}
    sources = [trades.rows(conn, book_id=b["id"]) for b in books_list if b["kind"] == "sim"]
    if real:
        sources.append(trades.rows(conn, account_ids=sorted(real)))
    for src in sources:
        for r, _realized, _before, _book, hand in walk_handover(src):
            if hand:                                          # 換倉：舊倉拿回成本、新倉放進成本
                if hand[0] in flows:
                    flows[hand[0]].append((r["date"], -hand[2]))
                if hand[1] in flows:
                    flows[hand[1]].append((r["date"], hand[2]))
            if r["book_id"] in flows and r["instrument"] == "stock":   # 期貨選擇權不比
                flows[r["book_id"]].append((r["date"], bench.trade_flow(r)))
    out = {}
    for b in books_list:
        f = sorted(flows[b["id"]], key=lambda x: x[0])
        if not f:
            continue
        end = clock.today() if b["status"] == "open" else date.fromisoformat(b.get("closed_at") or f[-1][0])
        c = bench.compare(f, int(b.get("in_market") or 0), rate, today=end)
        out[b["id"]] = {"shadow_gain": c["shadow_gain"], "actual_gain": c["actual_gain"], "diff": c["diff"], "beat": c["beat"],
                        "put_in": c["put_in"], "until": end.isoformat()}
    return out


def hide(w: Writer, layer: str, key: str, hidden: bool = True) -> list[str]:
    if layer not in books.LAYERS:
        raise InvalidInput(f"撲滿沒有「{layer}」這一層可以收起來")
    cur = list(settings.get(w.conn, "piggy.hidden", []) or [])
    tag = f"{layer}:{key}"
    if hidden and tag not in cur:
        cur.append(tag)
    if not hidden and tag in cur:
        cur.remove(tag)
    settings.put(w, "piggy.hidden", cur)
    return cur


def positions_view(conn: sqlite3.Connection, layer: str, book_id: int | None = None) -> dict[str, Any]:
    """部位狀況：還在場上的（持股、放空）＋出清的合計；平常一律用成本（價格要按了才看，見 quotes）。
    給 book_id＝只看算在那一倉的（最後動到它的那一倉）。"""
    book = books.get(conn, book_id) if book_id is not None else None
    if book and book["kind"] == "sim":
        p = trades.positions(conn, book_id=book["id"])
    else:
        names = _layer_names(conn, layer)
        p = trades.positions(conn, accounts=names) if names else trades.positions(conn, accounts=[])
        if book:
            p["items"] = [i for i in p["items"] if i["book_id"] == book["id"]]
            p["holding_cost"] = sum(i["cost"] for i in p["items"] if i["shares"] > 0)
            p["short_proceeds"] = sum(i["cost"] for i in p["items"] if i["shares"] < 0)
            for k in ("realized", "dividends", "fees"):            # 這一倉自己的（不是整層的）
                p[k] = sum(i[k] for i in p["items"])
    held = [i for i in p["items"] if i["shares"]]
    closed = [i for i in p["items"] if not i["shares"]]
    bk = {b["id"]: b["name"] for b in books.list_books(conn, layer=layer, include_archived=True)}
    for i in held:
        i["book"] = bk.get(i["book_id"])
    return {"items": held, "holding_cost": p["holding_cost"], "short": p["short_proceeds"],
            "realized": p["realized"], "dividends": p["dividends"], "fees": p["fees"],
            "closed_count": len(closed), "closed_realized": sum(i["realized"] for i in closed)}


# ---------------------------------------------------------------- 整包

def piggy(conn: sqlite3.Connection, period_key: str | None = None, level: str | None = None) -> dict[str, Any]:
    """撲滿首頁（level 不給）或某一層（level＝cash／invest／trading／property）。"""
    if level and level not in refdata.ASSET_CLASSES:
        raise InvalidInput(f"撲滿沒有「{level}」這一層", hint="、".join(refdata.ASSET_CLASSES))
    nav = views.period_nav(conn, period_key)
    key = nav["period"]["key"]
    all_levels = levels(conn)
    cash = cash_view(conn, key)
    big = {"cash": cash["leftover"]}
    for lay in ("invest", "trading"):
        lv = [x for x in all_levels if x["key"] == lay][0]
        if lv["count"]:
            big[lay] = layer_value(conn, lay)["value"]
    for lv in all_levels:
        lv["big"] = big.get(lv["key"], lv["total"])
    out: dict[str, Any] = {**nav, "levels": all_levels, "level": None, "pending": books.pending(conn),
                           "bench": bench.config(conn), "tithe_rate": books.tithe_rate(conn),
                           "month_max_loss": books.month_limit(conn)}
    s = invest.summary(conn)
    if s["count"]:                                            # 舊的「每月損益」（舊試算表匯入的）：有資料才給
        out["legacy_invest"] = {"total": s["total"], "count": s["count"], "months": s["months"][-6:]}
    if not level:
        return out
    out["level"] = [x for x in all_levels if x["key"] == level][0]
    if level == "cash":
        out["cash"] = cash
    elif level in books.LAYERS:
        out["layer"] = layer_view(conn, level, key)
    return out


def brief(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """總覽上那一張「撲滿」：待處理（捐贈、該收的倉、本月停止）＋投資與操盤目前 vs 基準紅線。沒東西就 None。"""
    pend = books.pending(conn)
    cfg = bench.config(conn)
    lines = []
    for lay in ("invest", "trading"):
        accs = books.layer_accounts(conn, lay)
        if not accs:
            continue
        v = layer_value(conn, lay)
        flows = _flows(conn, [a["id"] for a in accs])
        cmp = bench.compare(flows, v["value"], cfg["rate"]) if flows and v["cash_known"] else None
        lines.append({"layer": lay, "name": books.LAYERS[lay], "value": v["value"], "cash_known": v["cash_known"],
                      "bench": cmp["shadow"] if cmp else None, "diff": cmp["diff"] if cmp else None})
    m = books.month_status(conn) if books.layer_accounts(conn, "trading") else None
    stopped = m if m and m["stopped"] else None
    p = periods.current(conn)
    vio = conn.execute("SELECT COUNT(*) FROM trades t JOIN books b ON b.id = t.book_id WHERE t.violation IS NOT NULL "
                       "AND b.kind = 'real' AND b.status <> 'archived' AND t.date BETWEEN ? AND ?",
                       (p.start.isoformat(), p.end.isoformat())).fetchone()[0]
    if not pend and not lines and not stopped:
        return None
    return {"pending": pend, "lines": lines, "rate": cfg["rate"], "symbol": cfg["symbol"], "month_stopped": stopped,
            "violations": vio}
