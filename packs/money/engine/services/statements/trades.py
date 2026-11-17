"""adapter：股票成交明細（買、賣、當沖每一腳），照寫的數字記。

描述檔（kind＝trades）除了共同的欄位，還要：

    columns     一定要：date、type、shares、price；商品二選一：product（「名字(代號)」一格，照 product.pattern 拆）
                或 symbol（＋選填 name）。選填：seq（同一天的單號）、gross、fee、tax、net
    types       「類別」那一格的字 → {"action": "buy"|"sell", "day_trade": true|false}
    product     columns 有 product 時要寫：{"pattern": 有 (?P<code>…)(?P<name>…) 的正規表示式}
    day_trade_sum  選填：當沖那一組「淨收付」那一列的類別字（那一列不記，拿來核對每一腳加起來）
    subtotal    選填：每天的小計列 {"marker": "小計", "at": 0, "cells": {"gross": 2, "fee": 3, "tax": 4, "net": -1}}
                （marker 出現在第 at 格的列＝小計；cells＝成交金額、手續費、稅、淨收付在第幾格，負數從後面數）

規則（跟格式無關，寫在程式裡）：
- 淨收付：買＝−(價金＋手續費＋稅)、賣＝價金−手續費−稅；檔裡有 net 就照寫的，跟算的不一樣列在 checks.rows_off。
  當沖的腳 net 寫 0 的（淨收付寫在當沖那一列），照買賣算。
- 帳本已經有的（同一天、同一檔、同買賣、同股數、同價格）不再記；金額不一樣列在 differ。
- 當沖的腳記進給的操盤倉（day_trades_book）；沒給就跳過、數在 skipped_day_trades。其他記進投資帳戶，倉照日期挑
  （期間蓋到那一天的倉，收掉的也算；都沒有才照平常自動挑）。
- 重跑不會多：source＝描述檔的 source_tag，source_ref＝「日期:單號:第幾腳」（沒有單號就用第幾列）。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ... import db
from ...errors import InvalidInput
from .. import books, refdata, trades
from . import formats

KIND = "trades"
TITLE = "成交明細"
REQUIRED = ("date", "type", "shares", "price")
OPTIONAL = ("product", "symbol", "name", "seq", "gross", "fee", "tax", "net")
OPTIONS = ("account", "book")
ACTIONS = ("buy", "sell")


def check(raw: dict) -> list[str]:
    out: list[str] = []
    cols = raw.get("columns") if isinstance(raw.get("columns"), dict) else {}
    header = [h.replace(" ", "") for h in raw.get("header") or [] if isinstance(h, str)]
    item = "product" if "product" in cols else ("symbol" if "symbol" in cols else None)
    if item is None:
        out.append("columns 要有 product（名字與代號在同一格）或 symbol（代號自己一格）")
    elif str(cols[item]).replace(" ", "") not in header:
        out.append(f"columns.{item}「{cols[item]}」要列在 header 裡")
    if item == "product":
        out += formats.product_problems(raw)
    types = raw.get("types")
    if not isinstance(types, dict) or not types:
        out.append("types 要寫：類別的字 → {\"action\": \"buy\" 或 \"sell\", \"day_trade\": true/false}")
    else:
        for word, spec in types.items():
            if not isinstance(spec, dict) or spec.get("action") not in ACTIONS \
                    or not isinstance(spec.get("day_trade", False), bool):
                out.append(f"types.{word} 要是 {{\"action\": \"buy\" 或 \"sell\", \"day_trade\": true/false}}")
    dts = raw.get("day_trade_sum")
    if dts is not None and (not isinstance(dts, str) or not dts.strip()):
        out.append("day_trade_sum 要是一個字（或不寫）")
    sub = raw.get("subtotal")
    if sub is not None:
        cells = sub.get("cells") if isinstance(sub, dict) else None
        if not isinstance(sub, dict) or not isinstance(sub.get("marker"), str) or not isinstance(sub.get("at", 0), int) \
                or not isinstance(cells, dict) or set(cells) != {"gross", "fee", "tax", "net"} \
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in cells.values()):
            out.append("subtotal 要是 {\"marker\": \"小計\", \"at\": 0, \"cells\": {\"gross\": 2, \"fee\": 3, \"tax\": 4, \"net\": -1}}（或不寫）")
    return out


def _net(action: str, gross: int, fee: int, tax: int) -> int:
    return -(gross + fee + tax) if action == "buy" else gross - fee - tax


def read(fmt: formats.Format, head: list[str], body: list[list[str]], file: str) -> dict[str, Any]:
    raw = fmt.raw
    types = raw["types"]
    dts = (raw.get("day_trade_sum") or "").strip() or None
    sub = raw.get("subtotal")
    rows: list[dict[str, Any]] = []
    sums: list[dict[str, Any]] = []
    checks: dict[str, Any] = {"days": 0, "days_off": [], "day_trades": 0, "day_trades_off": [], "rows_off": []}
    day_rows: list[dict[str, Any]] = []
    day_sums: list[dict[str, Any]] = []
    legs: dict[tuple[str, str], int] = defaultdict(int)
    for n, r in enumerate(body, start=1):
        if sub and len(r) > sub.get("at", 0) and r[sub.get("at", 0)].replace(" ", "") == sub["marker"]:
            _check_day(sub, r, day_rows, day_sums, checks, n)
            day_rows, day_sums = [], []
            continue
        cell = formats.cell_of(fmt, head, r)
        where = f"第 {n} 列"
        if not cell("date").strip():
            continue
        day = formats.day(fmt, cell("date"), where)
        kind = cell("type").replace(" ", "")
        if "product" in fmt.columns:
            code, name = formats.product(fmt, cell("product"), where)
        else:
            code, name = cell("symbol").strip(), cell("name").strip()
        base = {"date": day, "symbol": code, "name": name, "seq": cell("seq").strip() or str(n),
                "shares": formats.whole(cell("shares"), where), "gross": formats.whole(cell("gross"), where),
                "fee": formats.whole(cell("fee"), where), "tax": formats.whole(cell("tax"), where),
                "net": formats.whole(cell("net"), where) if cell("net").strip() else None, "kind": kind, "line": n}
        if dts and kind == dts:
            day_sums.append(base)
            sums.append(base)
            continue
        if kind not in types:
            raise InvalidInput(f"{where}（{day}）「{kind}」這種交易還不會記",
                               hint="描述檔的 types 認得的：" + "、".join([*types, *([dts] if dts else [])]))
        action, dt = types[kind]["action"], bool(types[kind].get("day_trade", False))
        price = formats.price(cell("price"), where)
        if not base["gross"] and base["shares"]:
            base["gross"] = round(float(price) * base["shares"])
        legs[(day, base["seq"])] += 1
        row = {**base, "action": action, "day_trade": dt, "price": price,
               "ref": f"{day}:{base['seq']}:{legs[(day, base['seq'])]}"}
        want = _net(action, row["gross"], row["fee"], row["tax"])
        if row["net"] is None or (dt and row["net"] == 0):
            row["net"] = want                                   # 沒寫，或當沖的腳寫 0：照買賣算
        elif row["net"] != want:
            checks["rows_off"].append({k: row[k] for k in ("date", "symbol", "action", "gross", "fee", "tax", "net")}
                                      | {"computed": want})
        day_rows.append(row)
        rows.append(row)
    if sub and (day_rows or day_sums):
        checks["days_off"].append({"date": (day_rows or day_sums)[0]["date"], "why": "最後一天沒有小計"})
    if not dts:
        checks.pop("day_trades")
        checks.pop("day_trades_off")
    if not sub:
        checks.pop("days")
        checks.pop("days_off")
    if not rows:
        raise InvalidInput("成交明細裡沒有成交的列")
    rows.sort(key=lambda x: x["date"])                         # 同一天照檔案裡的順序（sort 是穩定的）
    return {"file": file, "rows": rows, "day_trade_sums": sums, "checks": checks}


def _check_day(sub: dict, subtotal: list[str], rows: list[dict[str, Any]], sums: list[dict[str, Any]],
               checks: dict[str, Any], n: int) -> None:
    """小計＝那天的列加起來（成交金額、手續費、稅算每一腳；淨收付算非當沖的列＋當沖那一列）；
    每一組當沖：那天那一檔的腳加起來＝當沖那一列。"""
    if not rows and not sums:
        return
    cells = sub["cells"]
    try:
        want = [formats.whole(subtotal[cells[k]], f"第 {n} 列（小計）") for k in ("gross", "fee", "tax", "net")]
    except IndexError:
        raise InvalidInput(f"第 {n} 列（小計）的格子比描述檔寫的少", hint="看描述檔的 subtotal.cells") from None
    net = sum(r["net"] for r in rows if not r["day_trade"]) + sum(s["net"] or 0 for s in sums)
    got = [sum(r["gross"] for r in rows), sum(r["fee"] for r in rows), sum(r["tax"] for r in rows), net]
    checks["days"] += 1
    day = (rows or sums)[0]["date"]
    if got != want:
        checks["days_off"].append({"date": day, "statement": want, "rows": got})
    for s in sums:
        checks["day_trades"] += 1
        legs = sum(r["net"] for r in rows if r["day_trade"] and r["symbol"] == s["symbol"])
        if legs != (s["net"] or 0):
            checks["day_trades_off"].append({"date": day, "symbol": s["symbol"], "statement": s["net"], "legs": legs})


def default_account(conn, layer: str) -> dict[str, Any]:
    """沒指定帳戶：這一層（invest／trading）只有一個證券帳戶就用它；沒有或好幾個就要指定。"""
    found = [a for a in books.layer_accounts(conn, layer) if a.get("kind") == "securities"]
    if len(found) == 1:
        return found[0]
    name = books.LAYERS[layer]
    if not found:
        raise InvalidInput(f"還沒有「{name}」那一層的證券帳戶", hint="先在設定頁加一個證券帳戶，選它算撲滿哪一層")
    raise InvalidInput(f"「{name}」那一層有 {len(found)} 個證券帳戶，要指定哪一個",
                       hint="account=" + "、".join(a["name"] for a in found))


def run(conn: db.Connection, fmt: formats.Format, data: dict[str, Any], *, account: Any = None, book: Any = None,
        dry_run: bool = False, actor: str = "import", **_ignored: Any) -> dict[str, Any]:
    rows = data["rows"]
    source = fmt.source_tag
    report: dict[str, Any] = {"format": fmt.id, "kind": KIND, "file": data["file"], "dry_run": dry_run, "rows": len(rows),
                              "first": rows[0]["date"], "last": rows[-1]["date"], "checks": data["checks"], "created": 0,
                              "replayed": 0, "already": 0, "differ": [], "by_book": defaultdict(int),
                              "skipped_day_trades": 0}
    with db.write(conn, actor=actor, source=source, command=f"import_statement {fmt.id}", dry_run=dry_run) as w:
        inv = refdata.resolve_account(w.conn, account) if account not in (None, "") else default_account(w.conn, "invest")
        dt_book = books.resolve(w.conn, book) if book not in (None, "") else None
        accounts = [inv["id"]] + ([dt_book["account_id"]] if dt_book else [])
        marks = ", ".join("?" * len(accounts))
        existing = [dict(r) for r in w.conn.execute(
            f"SELECT id, date, symbol, action, shares, price, gross, fee, tax, net, source, source_ref FROM trades "
            f"WHERE account_id IN ({marks}) AND date BETWEEN ? AND ? AND action IN ('buy', 'sell')",
            (*accounts, rows[0]["date"], rows[-1]["date"]))]
        used: set[int] = set()
        touched: set[int] = set()
        for r in rows:
            mine = next((e for e in existing if e["source"] == source and e["source_ref"] == r["ref"]), None)
            same = mine or next((e for e in existing if e["id"] not in used and e["date"] == r["date"]
                                 and e["symbol"] == r["symbol"] and e["action"] == r["action"] and e["shares"] == r["shares"]
                                 and abs(float(e["price"] or 0) - float(r["price"])) < 1e-6), None)
            if same:
                used.add(same["id"])
                report["replayed" if mine else "already"] += 1
                if (same["gross"], same["fee"], same["tax"], same["net"]) != (r["gross"], r["fee"], r["tax"], r["net"]):
                    report["differ"].append({k: r[k] for k in ("date", "symbol", "action", "shares", "price", "gross", "fee",
                                                               "tax", "net")}
                                            | {"ledger": {k: same[k] for k in ("id", "gross", "fee", "tax", "net")}})
                continue
            if r["day_trade"] and dt_book is None:
                report["skipped_day_trades"] += 1
                continue
            t = trades.add(w, date=r["date"], action=r["action"], symbol=r["symbol"], name=r["name"], shares=r["shares"],
                           price=r["price"], gross=r["gross"], fee=r["fee"], tax=r["tax"], net=r["net"],
                           account=None if r["day_trade"] else inv["name"],
                           book=dt_book["id"] if r["day_trade"] else (books.covering(w.conn, inv["id"], r["date"]) or {}).get("id"),
                           day_trade=r["day_trade"], source=source, source_ref=r["ref"], check=False, allow_closed=True)
            report["created" if t["status"] == "created" else "replayed"] += 1
            report["by_book"][t["book"] or "（沒有倉）"] += 1
            if t.get("book_id"):
                touched.add(t["book_id"])
        for bid in touched:
            books.resettle(w, bid)                            # 收掉的倉補記：重算、收倉日延到最後一筆
        books.recheck(w, since=rows[0]["date"])
        held = trades.positions(w.conn, account=inv["name"])["items"]
        report["account"] = inv["name"]
        report["short"] = [{"symbol": i["symbol"], "name": i["name"], "shares": i["shares"]} for i in held if i["shares"] < 0]
        report["holding"] = [{"symbol": i["symbol"], "name": i["name"], "shares": i["shares"], "cost": i["cost"]}
                             for i in held if i["shares"] > 0]
        report["batch_id"] = None if dry_run or not w.changes else w.batch_id
    report["by_book"] = dict(report["by_book"])
    return report
