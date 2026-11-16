"""倉：撲滿裡投資／資金操盤的每一筆錢的規矩——操作金額、可虧損、單次可虧損、捐贈。

設計：每一倉（或每一期）有操作金額、可虧損、單次可虧損三個數字。可虧損可以是整倉的錢；單次可虧損用來算
一次能下多少手數。可虧損用完就記一筆「停止」，之後若還有操作就記成違規，檢討時直接列出來。
這裡是規矩的程式判斷。

- 母＝帳戶（`accounts.asset_class`＝invest／trading），子＝倉。種類：真倉 real、模擬倉 sim（不算進真的數字）、歷史倉 history。
- 投資一年一倉（「2026 投資」，1/1～12/31）；操盤的倉自己開，沒指定就放進最新開著的那一倉。
- 收倉：結果寫死（已實現＋股利＋費用），有賺就算捐贈＝結果×比例。捐贈不寫進記帳（那是另一份金流，不跟日常帳混在一起）。
- 違規三種（倉停止還進場、本月停止還進場、單筆賠超過單次可虧損）：照樣記，但那一筆帶 violation。
- 「刪掉」＝封存（archived），資料不消失。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .. import clock
from ..db import Writer, row_dict
from ..errors import InvalidInput, NotFound
from . import periods, refdata, settings, trades
from .transactions import parse_amount, parse_date

LAYERS = {"invest": "投資", "trading": "資金操盤"}
KINDS = {"real": "真倉", "sim": "模擬倉", "history": "歷史倉"}
STATUS_NAMES = {"open": "進行中", "closed": "收倉", "archived": "封存"}
FIELDS = ("name", "start_date", "end_date", "capital", "max_loss", "per_trade_loss", "tithe_rate", "note")
TITHE_DEFAULT = 0.1


def _book_sql(where: str = "") -> str:
    return ("SELECT b.*, a.name AS account, a.asset_class AS layer FROM books b JOIN accounts a ON a.id = b.account_id"
            + (f" WHERE {where}" if where else ""))


def _row(conn: sqlite3.Connection, book_id: int) -> dict[str, Any] | None:
    return row_dict(conn.execute(_book_sql("b.id = ?"), (book_id,)).fetchone())


def get(conn: sqlite3.Connection, book_id: int) -> dict[str, Any]:
    b = _row(conn, int(book_id))
    if b is None:
        raise NotFound(f"找不到倉 #{book_id}")
    return b


def resolve(conn: sqlite3.Connection, ref: Any, *, account_id: int | None = None) -> dict[str, Any]:
    """倉：給數字＝id；給名字＝那個名字（同名在不同帳戶要給帳戶）。"""
    text = str(ref).strip().lstrip("#")
    if text.isdigit():
        return get(conn, int(text))
    where, args = "b.name = ?", [text]
    if account_id is not None:
        where += " AND b.account_id = ?"
        args.append(account_id)
    found = [row_dict(r) for r in conn.execute(_book_sql(where), args)]
    if not found:
        raise NotFound(f"找不到倉「{text}」", hint="用倉的編號；有哪些倉在撲滿那一層的「設定」")
    if len(found) > 1:
        raise InvalidInput(f"有 {len(found)} 個倉叫「{text}」", hint="用編號：" + "、".join(f"#{b['id']}（{b['account']}）" for b in found))
    return found[0]


def layer_accounts(conn: sqlite3.Connection, layer: str) -> list[dict[str, Any]]:
    return [a for a in refdata.accounts(conn) if (a.get("asset_class") or "") == layer]


def tithe_rate(conn: sqlite3.Connection, b: dict[str, Any] | None = None) -> float:
    if b and b.get("tithe_rate") is not None:
        return float(b["tithe_rate"])
    return float(settings.get(conn, "tithe.rate", TITHE_DEFAULT))


def _rate(value: Any, label: str = "捐贈比例") -> float | None:
    if value in (None, ""):
        return None
    try:
        v = float(str(value).strip().rstrip("%"))
    except ValueError:
        raise InvalidInput(f"{label}要是數字", fields={"tithe_rate": "數字"}) from None
    if str(value).strip().endswith("%") or v > 1:
        v = v / 100                                           # 「10」「10%」都當成 10%
    if not 0 <= v <= 1:
        raise InvalidInput(f"{label}要在 0～100% 之間")
    return round(v, 4)


def _money(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    if str(value).strip() in ("0", "0.0"):
        return 0
    n = parse_amount(value, label)
    if n < 0:
        raise InvalidInput(f"{label}不能是負的")
    return n


def _clean(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if k == "name":
            v = (v or "").strip()
            if not v or len(v) > 30:
                raise InvalidInput("倉的名字要 1～30 個字", fields={"name": "長度"})
        elif k in ("start_date", "end_date"):
            v = parse_date(v, k, allow_future=True) if v not in (None, "") else None
            if k == "start_date" and v is None:
                raise InvalidInput("開始日不能空白")
        elif k == "capital":
            v = _money(v, "操作金額") or 0
        elif k in ("max_loss", "per_trade_loss"):
            v = _money(v, "可虧損" if k == "max_loss" else "單次可虧損")
        elif k == "tithe_rate":
            v = _rate(v)
        elif k == "note":
            v = (v or "").strip()[:4000]
        out[k] = v
    if out.get("start_date") and out.get("end_date") and out["end_date"] < out["start_date"]:
        raise InvalidInput("結束日不能比開始日早")
    return out


def create(w: Writer, *, account: str | int | None = None, layer: str | None = None, name: str | None = None,
           kind: str = "real", start: str | None = None, end: str | None = None, capital: Any = 0,
           max_loss: Any = None, per_trade_loss: Any = None, tithe_rate: Any = None, note: str = "") -> dict[str, Any]:
    """開一倉。帳戶給名字或 id；只給 layer 就用那一層的第一個帳戶。"""
    if kind not in KINDS:
        raise InvalidInput(f"倉的種類只有：{'、'.join(f'{k}（{v}）' for k, v in KINDS.items())}")
    if account not in (None, ""):
        acc = refdata.account_by_id(w.conn, int(account)) if isinstance(account, int) else refdata.resolve_account(w.conn, str(account))
    elif layer:
        accs = layer_accounts(w.conn, layer)
        if not accs:
            raise NotFound(f"還沒有帳戶算「{LAYERS.get(layer, layer)}」", hint="到設定的帳戶把一個證券帳戶的「算撲滿哪一層」選這一層")
        acc = accs[0]
    else:
        raise InvalidInput("要給帳戶或哪一層", hint="給 account（證券帳戶）或 level（invest／trading）")
    if (acc.get("asset_class") or "") not in LAYERS:
        raise InvalidInput(f"{acc['name']} 不算投資或資金操盤，不能開倉", hint="到設定的帳戶改它的「算撲滿哪一層」")
    today = clock.today().isoformat()
    start_d = start or today
    if not name:
        base = (f"{start_d[:4]} 投資" if acc["asset_class"] == "invest" else f"{start_d[:7]} 操盤")
        if kind == "sim":
            base = "模擬 " + base
        name, n = base, 2                                      # 同名的收掉了又自動開：加編號，不要擋住記帳
        while w.conn.execute("SELECT 1 FROM books WHERE account_id = ? AND name = ?", (acc["id"], name)).fetchone():
            name, n = f"{base} ({n})", n + 1
    data = _clean(w.conn, {"name": name, "start_date": start_d, "end_date": end, "capital": capital, "max_loss": max_loss,
                           "per_trade_loss": per_trade_loss, "tithe_rate": tithe_rate, "note": note})
    if w.conn.execute("SELECT 1 FROM books WHERE account_id = ? AND name = ?", (acc["id"], data["name"])).fetchone():
        raise InvalidInput(f"{acc['name']} 已經有一倉叫「{data['name']}」")
    row = {"account_id": acc["id"], "kind": kind, "status": "open", **data,
           "batch_id": w.batch_id, "created_at": w.now, "updated_at": w.now}
    bid = w.conn.execute(f"INSERT INTO books ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values())).lastrowid
    after = row_dict(w.conn.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone())
    w.audit("create", "books", bid, None, after)
    return get(w.conn, bid)


def _update(w: Writer, b: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    before = row_dict(w.conn.execute("SELECT * FROM books WHERE id = ?", (b["id"],)).fetchone())
    real = {k: v for k, v in changes.items() if before.get(k) != v}
    if not real:
        return get(w.conn, b["id"])
    real["updated_at"] = w.now
    w.conn.execute(f"UPDATE books SET {', '.join(f'{k} = ?' for k in real)} WHERE id = ?", (*real.values(), b["id"]))
    after = row_dict(w.conn.execute("SELECT * FROM books WHERE id = ?", (b["id"],)).fetchone())
    w.audit("update", "books", b["id"], before, after)
    return get(w.conn, b["id"])


def update(w: Writer, ref: Any, **fields: Any) -> dict[str, Any]:
    """改倉的名字、日期、操作金額、可虧損、單次可虧損、捐贈比例、筆記。給 None 的不動；要清掉給空字串。"""
    b = resolve(w.conn, ref)
    wanted = {k: v for k, v in fields.items() if k in FIELDS and v is not None}
    if not wanted:
        return b
    changes = _clean(w.conn, wanted)
    if "name" in changes and changes["name"] != b["name"] and w.conn.execute(
            "SELECT 1 FROM books WHERE account_id = ? AND name = ? AND id <> ?", (b["account_id"], changes["name"], b["id"])).fetchone():
        raise InvalidInput(f"{b['account']} 已經有一倉叫「{changes['name']}」")
    if b["status"] != "open" and "tithe_rate" in changes:
        rate = changes["tithe_rate"] if changes["tithe_rate"] is not None else tithe_rate(w.conn)
        changes["tithe_amount"] = 0 if b["kind"] in ("sim", "history") else _tithe_of(b["result"], rate)
    out = _update(w, b, changes)
    if {"max_loss", "per_trade_loss"} & set(changes):
        recheck(w, account_id=b["account_id"])            # 規矩改了：這一倉的違規照新的數字重新看
    return out


def _tithe_of(result: int | None, rate: float) -> int:
    """捐贈＝賺的 × 比例，四捨五入到元（Python 的 round 是銀行家捨入：116.5 會變 116，錢不能這樣算）。"""
    value = Decimal(max(int(result or 0), 0)) * Decimal(str(rate))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def close(w: Writer, ref: Any, *, day: str | None = None) -> dict[str, Any]:
    """收倉：結果寫死（這一倉實現的損益＋股利＋費用），有賺就算好捐贈。模擬倉也算結果，但捐贈一律 0。"""
    b = resolve(w.conn, ref)
    if b["status"] != "open":
        raise InvalidInput(f"倉「{b['name']}」已經{STATUS_NAMES[b['status']]}了")
    s = stats(w.conn, [b])[b["id"]]
    result = s["result"]
    tithe = 0 if b["kind"] == "sim" else _tithe_of(result, tithe_rate(w.conn, b))
    when = parse_date(day, "date") if day else clock.today().isoformat()
    changes = {"status": "closed", "result": result, "tithe_amount": tithe, "closed_at": when}
    if not b["end_date"] or b["end_date"] > when:
        changes["end_date"] = when
    if b["kind"] == "history":
        changes["tithe_amount"] = 0                            # 歷史倉的捐贈在記帳工具之外已經處理過了
    if b["tithe_done_at"] and changes["tithe_amount"] > (b["tithe_amount"] or 0):
        changes["tithe_done_at"] = None                        # 重新打開後又多賺了：多出來的還要給
    out = _update(w, b, changes)
    return {**out, "open_positions": s["open"]}


def reopen(w: Writer, ref: Any) -> dict[str, Any]:
    b = resolve(w.conn, ref)
    if b["status"] == "open":
        return b
    # 捐贈的金額與打勾留著：已經給過的不會因為重新打開又要再給一次（再收倉時多賺的才要補）
    return _update(w, b, {"status": "open", "result": None, "closed_at": None, "archived_at": None})


def archive(w: Writer, ref: Any, *, undo: bool = False) -> dict[str, Any]:
    """封存（「刪掉」也是這個）：從清單收起來，資料不消失。還開著的倉先收倉再封存。"""
    b = resolve(w.conn, ref)
    if undo:
        if b["status"] != "archived":
            return b
        return _update(w, b, {"status": "closed" if b["closed_at"] else "open", "archived_at": None})
    if b["status"] == "archived":
        return b
    if b["status"] == "open":
        close(w, b["id"])
        b = get(w.conn, b["id"])
    return _update(w, b, {"status": "archived", "archived_at": clock.today().isoformat()})


def tithe(w: Writer, ref: Any, *, rate: Any = None, done: bool | None = None) -> dict[str, Any]:
    """捐贈：改比例（金額跟著算）、打勾（done=True 記今天）、取消打勾（done=False）。只有收倉了才有捐贈。"""
    b = resolve(w.conn, ref)
    if b["status"] == "open":
        raise InvalidInput(f"倉「{b['name']}」還沒收倉，收倉之後才算捐贈")
    changes: dict[str, Any] = {}
    if rate not in (None, ""):
        r = _rate(rate)
        changes["tithe_rate"] = r
        changes["tithe_amount"] = 0 if b["kind"] in ("sim", "history") else _tithe_of(b["result"], r)
    if done is True:
        changes["tithe_done_at"] = b["tithe_done_at"] or clock.today().isoformat()
    elif done is False:
        changes["tithe_done_at"] = None
    return _update(w, b, changes) if changes else b


# ---------------------------------------------------------------- 把交易搬到別的倉

def move_trades(w: Writer, to: Any, *, from_books: list[Any] | None = None, since: str | None = None,
                until: str | None = None, layer: str | None = None) -> dict[str, Any]:
    """把一段時間的交易搬進另一倉（例：某段時間之後買賣的股票改歸到「長期投資」那一倉）。
    只搬同一個母帳戶的（倉掛在帳戶底下）；from_books 不給＝那個帳戶的所有倉（模擬倉除外）。每一筆都有 audit，可以整批撤銷。
    搬完：收掉的倉重新結算（結果、捐贈照新的算，收倉日不變）、違規從最早那天重新看。"""
    target = resolve(w.conn, to)
    acc = target["account_id"]
    src_ids = [resolve(w.conn, x)["id"] for x in from_books] if from_books else [
        r["id"] for r in w.conn.execute("SELECT id FROM books WHERE account_id = ? AND kind <> 'sim'", (acc,))]
    src_ids = [i for i in src_ids if i != target["id"]]
    if not src_ids:
        return {"moved": 0, "to": target["name"], "from": []}
    sql = (f"SELECT * FROM trades WHERE account_id = ? AND book_id IN ({', '.join('?' * len(src_ids))})")
    args: list[Any] = [acc, *src_ids]
    if since:
        sql += " AND date >= ?"
        args.append(parse_date(since, "since", allow_future=True))
    if until:
        sql += " AND date <= ?"
        args.append(parse_date(until, "until", allow_future=True))
    moved, touched, first = 0, set(), None
    for r in [row_dict(x) for x in w.conn.execute(sql + " ORDER BY date, id", args)]:
        w.conn.execute("UPDATE trades SET book_id = ? WHERE id = ?", (target["id"], r["id"]))
        w.audit("update", "trades", r["id"], r, {**r, "book_id": target["id"]})
        touched.add(r["book_id"])
        first = first or r["date"]
        moved += 1
    if not moved:
        return {"moved": 0, "to": target["name"], "from": []}
    for bid in touched | {target["id"]}:                      # 收掉的倉：照新的交易重新結算（收倉日不變）
        resettle(w, bid, extend=False)
    recheck(w, since=first, account_id=acc)
    return {"moved": moved, "to": target["name"], "from": [get(w.conn, i)["name"] for i in sorted(touched)], "since": first}


def resettle(w: Writer, ref: Any, *, extend: bool = True) -> dict[str, Any]:
    """收掉的倉補記或搬走交易之後：結果、捐贈照現在的交易重新算。extend＝補進來的交易比收倉日晚，
    收倉日（和結束日）跟著延到最後一筆（匯入補遺用；搬交易時收倉日不動）。開著的倉不用做。"""
    b = resolve(w.conn, ref)
    if b["status"] == "open" or not b["closed_at"]:
        return b
    s = stats(w.conn, [b])[b["id"]]
    changes: dict[str, Any] = {"result": s["result"],
                               "tithe_amount": 0 if b["kind"] in ("sim", "history") else _tithe_of(s["result"], tithe_rate(w.conn, b))}
    last = w.conn.execute("SELECT MAX(date) FROM trades WHERE book_id = ?", (b["id"],)).fetchone()[0]
    if extend and last and last > b["closed_at"]:
        changes["closed_at"] = last
        if b["end_date"] and b["end_date"] < last:
            changes["end_date"] = last
    return _update(w, b, changes)


def covering(conn: sqlite3.Connection, account_id: int, day: str) -> dict[str, Any] | None:
    """這個帳戶哪一倉的期間蓋到這一天（真倉、歷史倉都算，模擬倉、封存的不算；好幾倉都蓋到就挑開始日最晚的）。
    匯入整段對帳單時用（照對帳單重建好幾年的交易，每一筆歸進期間蓋到它的那一倉）。"""
    row = conn.execute(_book_sql("b.account_id = ? AND b.kind <> 'sim' AND b.status <> 'archived' AND b.start_date <= ? "
                                 "AND (b.end_date IS NULL OR b.end_date >= ?) ORDER BY b.start_date DESC, b.id DESC LIMIT 1"),
                       (account_id, day, day)).fetchone()
    return row_dict(row) if row is not None and not isinstance(row, dict) else row


# ---------------------------------------------------------------- 自動挑倉

def pick(w: Writer, acc: dict[str, Any], day: str) -> dict[str, Any] | None:
    """記交易沒給倉：投資＝那一年的倉（沒有就開「YYYY 投資」）；操盤＝最新開著的真倉（沒有就開「YYYY-MM 操盤」）。
    帳戶不算投資／操盤（例如沒分層的證券戶）就不掛倉。"""
    layer = acc.get("asset_class") or ""
    if layer not in LAYERS:
        return None
    if layer == "invest":
        year = day[:4]
        row = w.conn.execute(_book_sql("b.account_id = ? AND b.kind = 'real' AND b.start_date <= ? "
                                       "AND (b.end_date IS NULL OR b.end_date >= ?) ORDER BY b.start_date DESC, b.id DESC LIMIT 1"),
                             (acc["id"], day, day)).fetchone()
        if row is None:
            name = f"{year} 投資"
            same = row_dict(w.conn.execute(_book_sql("b.account_id = ? AND b.name = ?"), (acc["id"], name)).fetchone())
            if same:
                row = same
            else:
                return create(w, account=acc["id"], name=name, start=f"{year}-01-01", end=f"{year}-12-31")
        b = row_dict(row) if not isinstance(row, dict) else row
        if b["status"] != "open":
            raise InvalidInput(f"倉「{b['name']}」已經{STATUS_NAMES[b['status']]}了，{day} 的交易要記進去先重新打開",
                               hint=f"用 /money_pbook_close {b['id']} reopen 重新打開，或在 book 欄位指定別的倉")
        return b
    row = w.conn.execute(_book_sql("b.account_id = ? AND b.kind = 'real' AND b.status = 'open' "
                                   "ORDER BY b.start_date DESC, b.id DESC LIMIT 1"), (acc["id"],)).fetchone()
    if row is not None:
        return row_dict(row)
    return create(w, account=acc["id"], start=day)


# ---------------------------------------------------------------- 每一倉現在怎樣

def _empty(b: dict[str, Any]) -> dict[str, Any]:
    return {"realized": 0, "dividends": 0, "other": 0, "fees": 0, "trades": 0, "violations": 0, "in_market": 0,
            "open": 0, "loss_streak_max": 0, "stopped_on": None, "first": None, "last": None,
            "costs": {"turnover": 0, "fee": 0, "sell_turnover": 0, "tax": 0, "lots": 0, "dfee": 0, "dtax": 0, "notional": 0}}


def stats(conn: sqlite3.Connection, books_list: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """每一倉：已實現、股利、費用、筆數、違規、在場上（成本）、結果、可虧損用掉多少、停止了沒。

    真倉／歷史倉：照帳戶一路走（平均成本跨倉），賣出的損益記給那一筆的倉；模擬倉只走自己的交易。"""
    out = {b["id"]: _empty(b) for b in books_list}
    meta = {b["id"]: b for b in books_list}
    real_accounts = {b["account_id"] for b in books_list if b["kind"] != "sim"}
    sources = [trades.rows(conn, book_id=b["id"]) for b in books_list if b["kind"] == "sim"]
    if real_accounts:
        sources.append(trades.rows(conn, account_ids=real_accounts))
    for src in sources:
        final: dict[str, trades.Position] = {}
        for r, realized, _before, final in trades.walk(src):
            s = out.get(r["book_id"])
            if s is None:
                continue
            s["trades"] += 1
            s["fees"] += r["fee"] + r["tax"]
            s["violations"] += 1 if r["violation"] else 0
            s["first"] = s["first"] or r["date"]
            s["last"] = r["date"]
            if r["action"] in ("buy", "sell"):
                c = s["costs"]
                if r["instrument"] == "stock":
                    c["turnover"] += r["gross"]
                    c["fee"] += r["fee"]
                    if r["action"] == "sell":
                        c["sell_turnover"] += r["gross"]
                        c["tax"] += r["tax"]
                elif r["gross"]:                              # 到期歸零那一筆（0 元）不算成交的口數
                    c["lots"] += r["shares"]
                    c["dfee"] += r["fee"]
                    c["dtax"] += r["tax"]
                    c["notional"] += r["gross"]
            if r["action"] == "dividend":
                s["dividends"] += r["net"]
            elif r["action"] in ("fee", "other"):
                s["other"] += r["net"]
            else:
                s["realized"] += realized
            b = meta[r["book_id"]]
            net = s["realized"] + s["dividends"] + s["other"]
            if b.get("max_loss") and s["stopped_on"] is None and -net >= b["max_loss"]:
                s["stopped_on"] = r["date"]                    # 停止是黏住的：之後賺回來也不會解除（規矩：賠到這裡就停）
        for pos in final.values():
            if pos.shares and pos.book_id in out:
                out[pos.book_id]["in_market"] += pos.cost if pos.shares > 0 else -pos.cost
                out[pos.book_id]["open"] += 1
    for bid, s in out.items():
        b = meta[bid]
        s["result"] = s["realized"] + s["dividends"] + s["other"]
        s["loss_used"] = max(-s["result"], 0)
        s["stopped"] = bool(b.get("max_loss")) and s["stopped_on"] is not None
        s["loss_left"] = None if not b.get("max_loss") else (0 if s["stopped"] else max(b["max_loss"] - s["loss_used"], 0))
        s["value"] = (b.get("capital") or 0) + s["result"]     # 現值（持股用成本）：操作金額＋結果
        s["cash"] = s["value"] - s["in_market"]                # 手上的現金（買進去的錢暫時「不見」，賣回來才回來）
        s["costs"] = _cost_summary(s["costs"])
    return out


def _cost_summary(c: dict[str, int]) -> dict[str, Any]:
    """這一倉付了多少手續費、交易稅，佔成交金額多少（股票）；期貨選擇權每口多少。"""
    out: dict[str, Any] = {"turnover": c["turnover"], "fee": c["fee"], "tax": c["tax"], "sell_turnover": c["sell_turnover"],
                           "fee_rate": round(c["fee"] / c["turnover"], 6) if c["turnover"] else None,
                           "fee_discount": round(c["fee"] / c["turnover"] / trades.FEE_LIST_RATE, 3) if c["turnover"] else None,
                           "tax_rate": round(c["tax"] / c["sell_turnover"], 6) if c["sell_turnover"] else None}
    if c["lots"]:
        out["derivatives"] = {"lots": c["lots"], "fee": c["dfee"], "tax": c["dtax"], "fee_per_lot": round(c["dfee"] / c["lots"], 2),
                              "tax_rate": round(c["dtax"] / c["notional"], 8) if c["notional"] else None}
    return out


def list_books(conn: sqlite3.Connection, *, layer: str | None = None, account_id: int | None = None,
               include_archived: bool = False, kinds: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    where, args = [], []
    if layer:
        where.append("a.asset_class = ?")
        args.append(layer)
    if account_id is not None:
        where.append("b.account_id = ?")
        args.append(account_id)
    if not include_archived:
        where.append("b.status <> 'archived'")
    if kinds:
        where.append(f"b.kind IN ({', '.join('?' * len(kinds))})")
        args += list(kinds)
    sql = _book_sql(" AND ".join(where)) + " ORDER BY CASE b.status WHEN 'open' THEN 0 ELSE 1 END, " \
          "CASE b.kind WHEN 'real' THEN 0 WHEN 'sim' THEN 1 ELSE 2 END, b.start_date DESC, b.id DESC"
    found = [row_dict(r) for r in conn.execute(sql, args)]
    st = stats(conn, found)
    return [present(conn, b, st[b["id"]]) for b in found]


def present(conn: sqlite3.Connection, b: dict[str, Any], s: dict[str, Any] | None = None) -> dict[str, Any]:
    s = s if s is not None else stats(conn, [b])[b["id"]]
    rate = tithe_rate(conn, b)
    live = dict(s)
    if b["status"] != "open" and b["result"] is not None:      # 收倉的結果是寫死的；之後補記的只看 result_now
        live["result_now"] = live["result"]
        live["result"] = b["result"]
    return {**b, **live, "kind_name": KINDS[b["kind"]], "status_name": STATUS_NAMES[b["status"]],
            "layer_name": LAYERS.get(b.get("layer") or "", ""), "tithe_rate_used": rate,
            "tithe_pending": bool(b["status"] != "open" and b["kind"] == "real" and (b["tithe_amount"] or 0) > 0
                                  and not b["tithe_done_at"]),
            "rules_missing": bool(b["status"] == "open" and b.get("layer") == "trading" and b["kind"] != "history"
                                  and not b.get("max_loss")),
            "max_shares_hint": None}


def book_view(conn: sqlite3.Connection, ref: Any) -> dict[str, Any]:
    return present(conn, resolve(conn, ref))


# ---------------------------------------------------------------- 違規

def month_limit(conn: sqlite3.Connection) -> int:
    return int(settings.get(conn, "trading.month_max_loss", 0) or 0)


def month_status(conn: sqlite3.Connection, day: str | None = None) -> dict[str, Any]:
    """這一期（起算日算的那一期）所有操盤真倉加起來賠了多少、本月可虧損打完了沒。"""
    p = periods.period_for(conn, date.fromisoformat(day or clock.today().isoformat()))
    limit = month_limit(conn)
    accounts = [a["id"] for a in layer_accounts(conn, "trading")]
    net = 0
    stopped_on = None
    for r, realized, _before, _book in trades.walk(trades.rows(conn, account_ids=accounts)):
        if not p.start.isoformat() <= r["date"] <= p.end.isoformat():
            continue
        net += r["net"] if r["action"] in ("dividend", "fee", "other") else realized
        if limit and stopped_on is None and -net >= limit:
            stopped_on = r["date"]                             # 黏住的：這一期後來賺回來也還是停止
    stopped = bool(limit) and stopped_on is not None
    return {"period": p.key, "limit": limit, "loss": max(-net, 0), "result": net,
            "left": None if not limit else (0 if stopped else max(limit - max(-net, 0), 0)),
            "stopped": stopped, "stopped_on": stopped_on}


def _kinds(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    return {r["id"]: row_dict(r) for r in conn.execute(_book_sql())}


def _judge(state: dict[str, Any], b: dict[str, Any], r: Any, realized: int, before: int, month: int,
           month_state: dict[str, Any] | None) -> str | None:
    """一筆照規矩看：進場（含翻空翻多）時倉或本月已經停止＝違規；實現的虧損超過單次可虧損＝違規。"""
    if r["action"] not in ("buy", "sell") or b["kind"] == "history":
        return None
    if trades.opens(before, r):
        if b.get("max_loss") and state["stopped_on"]:
            return f"倉「{b['name']}」可虧損 {b['max_loss']:,} 在 {state['stopped_on']} 打完了，停止之後又進場"
        if month and month_state and month_state["stopped_on"]:
            return f"本月可虧損 {month:,} 在 {month_state['stopped_on']} 打完了，停止之後又進場"
    if realized < 0 and b.get("per_trade_loss") and -realized > b["per_trade_loss"]:
        return f"這一筆賠 {-realized:,}，超過單次可虧損 {b['per_trade_loss']:,}"
    return None


def recheck(w: Writer, *, since: str | None = None, account_id: int | None = None) -> int:
    """照時間從頭走一遍，把 since 那天（含）之後每一筆的違規重新判斷、寫回去（有變才寫）。
    補記比較早的交易、刪掉一筆、撤銷、改了倉的可虧損之後都要叫——不然後面那幾筆的違規還是舊的。
    violation 是「算出來的」欄位：不寫 audit（撤銷之後會再叫一次這裡重算），不然補記一筆會讓比較早的批次變成不能撤銷。
    停止是黏住的：倉賠到可虧損、本月賠到本月可虧損，之後賺回來也不會解除。回傳改了幾筆。"""
    conn = w.conn
    meta = _kinds(conn)
    month = month_limit(conn)
    trading = {a["id"] for a in layer_accounts(conn, "trading")}
    period_of: dict[str, tuple[str, str, str]] = {}

    def period(day: str) -> tuple[str, str, str]:
        if day not in period_of:
            p = periods.period_for(conn, date.fromisoformat(day))
            period_of[day] = (p.key, p.start.isoformat(), p.end.isoformat())
        return period_of[day]

    changed = 0
    real_accounts = None if account_id is None else (
        sorted(trading | {account_id}) if account_id in trading else [account_id])
    sources = [trades.rows(conn, account_ids=real_accounts)]
    sources += [trades.rows(conn, book_id=bid) for bid, b in meta.items() if b["kind"] == "sim"
                and (account_id is None or b["account_id"] == account_id)]
    for src in sources:
        state: dict[int, dict[str, Any]] = {}
        months: dict[str, dict[str, Any]] = {}
        for r, realized, before, _book in trades.walk(src):
            b = meta.get(r["book_id"]) if r["book_id"] else None
            if b is None:
                continue
            st = state.setdefault(b["id"], {"net": 0, "stopped_on": None})
            in_month = month and b["kind"] == "real" and r["account_id"] in trading
            ms = months.setdefault(period(r["date"])[0], {"net": 0, "stopped_on": None}) if in_month else None
            want = _judge(st, b, r, realized, before, month, ms)
            if (since is None or r["date"] >= since) and (r["violation"] or None) != want:
                conn.execute("UPDATE trades SET violation = ? WHERE id = ?", (want, r["id"]))
                changed += 1
            delta = r["net"] if r["action"] in ("dividend", "fee", "other") else realized
            st["net"] += delta
            if b.get("max_loss") and st["stopped_on"] is None and -st["net"] >= b["max_loss"]:
                st["stopped_on"] = r["date"]
            if ms is not None:
                ms["net"] += delta
                if ms["stopped_on"] is None and -ms["net"] >= month:
                    ms["stopped_on"] = r["date"]
    return changed


def violations(conn: sqlite3.Connection, *, layer: str | None = None, book_id: int | None = None,
               since: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    sql = ("SELECT t.* FROM trades t JOIN books b ON b.id = t.book_id JOIN accounts a ON a.id = t.account_id "
           "WHERE t.violation IS NOT NULL AND b.status <> 'archived'")
    args: list[Any] = []
    if layer:
        sql += " AND a.asset_class = ?"
        args.append(layer)
    if book_id is not None:
        sql += " AND t.book_id = ?"
        args.append(book_id)
    if since:
        sql += " AND t.date >= ?"
        args.append(since)
    sql += " ORDER BY t.date DESC, t.id DESC LIMIT ?"
    args.append(limit)
    return [trades.present(conn, row_dict(r)) for r in conn.execute(sql, args)]


# ---------------------------------------------------------------- 待處理（總覽、燈號、週報）

def giving_on(conn: sqlite3.Connection) -> bool:
    """開了「捐贈」選配沒有（giving 那一格開著）。帳本裡沒有這一格就當成開著（不藏使用者自己的資料）。"""
    row = conn.execute("SELECT active FROM buckets WHERE id = 'giving'").fetchone()
    return True if row is None else bool(row["active"])


def pending(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """要使用者動手的：捐贈還沒做的（只有開了「捐贈」選配才列）、到期該收的倉。模擬倉不算。"""
    today = clock.today().isoformat()
    out = []
    giving = "" if giving_on(conn) else " AND 0"
    for r in conn.execute(_book_sql("b.status = 'closed' AND b.kind = 'real' AND b.tithe_amount > 0 "
                                    f"AND b.tithe_done_at IS NULL{giving} ORDER BY b.closed_at, b.id")):
        b = row_dict(r)
        out.append({"kind": "tithe", "book_id": b["id"], "book": b["name"], "layer": b["layer"],
                    "amount": b["tithe_amount"], "result": b["result"], "rate": tithe_rate(conn, b),
                    "closed_at": b["closed_at"]})
    for r in conn.execute(_book_sql("b.status = 'open' AND b.kind = 'real' AND b.end_date IS NOT NULL "
                                    "AND b.end_date < ? ORDER BY b.end_date, b.id"), (today,)):
        b = row_dict(r)
        out.append({"kind": "close", "book_id": b["id"], "book": b["name"], "layer": b["layer"], "end_date": b["end_date"]})
    return out
