"""adapter：已實現損益——拿來核對帳本的賣出，當沖補進操盤的倉。

描述檔（kind＝realized）除了共同的欄位，還要：

    columns     一定要：date、type、shares、price、income（賣出淨收）、cost（買進成本）、pnl（損益）；
                商品二選一：product（照 product.pattern 拆）或 symbol（＋選填 name）
    product     columns 有 product 時要寫（同成交明細）
    subtotal_markers  選填：小計／合計那一列裡會出現的字（那一列不算成交，拿來當總數）
    day_trade_type    選填：「當沖」那一種的類別字；不寫＝不補當沖
    day_trade_tax_rate 選填：當沖賣出的交易稅率（拿來把費用拆成手續費與稅；例 0.0015）；不寫＝費用全部算手續費

這種表只有**賣出**（成交日、股數、成交價、淨收、成本、損益），沒有買進那一筆。規則（跟格式無關）：
- 帳本已經有的賣出（同一天、同一檔、同股數、同價格）：核對淨收一不一樣。
- 當沖：買賣同一天、成本就是那天買進的錢——補成一組買＋賣記進給的操盤倉（book）。
- 其他帳本沒有的賣出：**不記**（沒有買進紀錄，記了會變成放空），列成「等買進紀錄」的清單，附帳本那天之前的持股。
- 重跑不會多：source＝描述檔的 source_tag。
"""
from __future__ import annotations

from typing import Any

from ... import db
from ...errors import InvalidInput
from .. import books, refdata, trades
from . import formats
from .trades import default_account

KIND = "realized"
TITLE = "已實現損益"
REQUIRED = ("date", "type", "shares", "price", "income", "cost", "pnl")
OPTIONAL = ("product", "symbol", "name")
OPTIONS = ("account", "book")


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
    if raw.get("subtotal_markers") is not None and not formats.strings(raw["subtotal_markers"]):
        out.append("subtotal_markers 要是字的清單（或不寫）")
    if raw.get("day_trade_type") is not None and not (isinstance(raw["day_trade_type"], str) and raw["day_trade_type"].strip()):
        out.append("day_trade_type 要是一個字（或不寫）")
    rate = raw.get("day_trade_tax_rate")
    if rate is not None and (not isinstance(rate, (int, float)) or isinstance(rate, bool) or not 0 <= rate < 0.1):
        out.append("day_trade_tax_rate 要是 0～0.1 之間的數字（或不寫）")
    return out


def read(fmt: formats.Format, head: list[str], body: list[list[str]], file: str) -> dict[str, Any]:
    markers = tuple(fmt.raw.get("subtotal_markers") or ())
    rows, total = [], None
    for n, r in enumerate(body, start=1):
        cell = formats.cell_of(fmt, head, r)
        where = f"第 {n} 列"
        if not cell("date").strip():
            if markers and any(m in c for c in r for m in markers):
                total = {"income": formats.num(cell("income"), where), "cost": formats.num(cell("cost"), where),
                         "pnl": formats.num(cell("pnl"), where)}
            continue
        if "product" in fmt.columns:
            code, name = formats.product(fmt, cell("product"), where)
        else:
            code, name = cell("symbol").strip(), cell("name").strip()
        rows.append({"date": formats.day(fmt, cell("date"), where), "symbol": code, "name": name,
                     "kind": cell("type").replace(" ", ""), "shares": formats.whole(cell("shares"), where),
                     "price": formats.price(cell("price"), where), "income": formats.whole(cell("income"), where),
                     "cost": formats.num(cell("cost"), where), "pnl": formats.whole(cell("pnl"), where)})
    if not rows:
        raise InvalidInput("已實現損益裡沒有成交的列")
    rows.sort(key=lambda x: x["date"])
    return {"file": file, "rows": rows, "total": total}


def run(conn: db.Connection, fmt: formats.Format, data: dict[str, Any], *, account: Any = None, book: Any = None,
        dry_run: bool = False, actor: str = "import", **_ignored: Any) -> dict[str, Any]:
    rows = data["rows"]
    day_type = (fmt.raw.get("day_trade_type") or "").replace(" ", "") or None
    report: dict[str, Any] = {"format": fmt.id, "kind": KIND, "file": data["file"], "dry_run": dry_run, "rows": len(rows),
                              "first": rows[0]["date"], "last": rows[-1]["date"], "total": data["total"], "matched": 0,
                              "income_off": [], "day_trades": [], "waiting": [], "created": 0, "replayed": 0}
    with db.write(conn, actor=actor, source=fmt.source_tag, command=f"import_statement {fmt.id}", dry_run=dry_run) as w:
        acc = refdata.resolve_account(w.conn, account) if account not in (None, "") else default_account(w.conn, "invest")
        target = books.resolve(w.conn, book) if book not in (None, "") else None
        sells = [dict(r) for r in w.conn.execute(
            "SELECT id, date, symbol, shares, price, net, source FROM trades WHERE action = 'sell' AND date BETWEEN ? AND ?",
            (rows[0]["date"], rows[-1]["date"]))]
        used: set[int] = set()
        waiting = []
        for r in rows:
            hit = next((s for s in sells if s["id"] not in used and s["date"] == r["date"] and s["symbol"] == r["symbol"]
                        and s["shares"] == r["shares"] and abs(float(s["price"] or 0) - float(r["price"])) < 0.0001), None)
            if hit:
                used.add(hit["id"])
                report["matched"] += 1
                if hit["net"] != r["income"]:
                    report["income_off"].append({**r, "ledger_net": hit["net"], "trade_id": hit["id"]})
                continue
            if day_type and r["kind"] == day_type:
                report["day_trades"].append(r)
                if target is not None:
                    for t in _day_trade(w, fmt, r, target):
                        report["created" if t["status"] == "created" else "replayed"] += 1
                continue
            waiting.append(r)
        if target is not None and report["day_trades"]:
            books.resettle(w, target["id"])
            b = books.book_view(w.conn, target["id"])
            report["book"] = {"id": b["id"], "name": b["name"], "status": b["status_name"], "result": b["result"],
                              "trades": b["trades"], "first": b["first"], "last": b["last"]}
        report["account"] = acc["name"]
        report["waiting"] = _held_before(w.conn, acc["id"], waiting)
        report["waiting_summary"] = {"rows": len(waiting), "income": sum(r["income"] for r in waiting),
                                     "cost": round(sum(r["cost"] for r in waiting)), "pnl": sum(r["pnl"] for r in waiting),
                                     "short": sum(1 for r in report["waiting"] if r["held"] < r["shares"])}
        report["batch_id"] = None if dry_run or not w.changes else w.batch_id
    return report


def _day_trade(w: db.Writer, fmt: formats.Format, r: dict[str, Any], book: dict[str, Any]) -> list[dict[str, Any]]:
    """當沖一組：買進＝成本（含手續費），賣出＝淨收（費用照描述檔的稅率拆成稅＋其餘手續費）。"""
    rate = float(fmt.raw.get("day_trade_tax_rate") or 0)
    gross_sell = round(r["shares"] * float(r["price"]))
    costs = gross_sell - r["income"]
    tax = min(int(gross_sell * rate), max(costs, 0))
    cost = int(round(r["cost"]))
    note = "已實現損益表的當沖：買進只有成本（含手續費），單價＝成本 ÷ 股數"
    ref = f"{r['date']}:{r['symbol']}:{r['shares']}:{r['price']}"
    buy = trades.add(w, date=r["date"], action="buy", symbol=r["symbol"], name=r["name"], shares=r["shares"],
                     price=f"{cost / r['shares']:.4f}" if r["shares"] else "0", gross=cost, fee=0, tax=0, note=note,
                     source=fmt.source_tag, source_ref=f"{ref}:buy", book=book["id"], day_trade=True, check=False,
                     allow_closed=True)
    sell = trades.add(w, date=r["date"], action="sell", symbol=r["symbol"], name=r["name"], shares=r["shares"],
                      price=r["price"], gross=gross_sell, fee=max(costs - tax, 0), tax=tax, net=r["income"],
                      note=f"對帳單算的損益 {r['pnl']:+,}", source=fmt.source_tag, source_ref=f"{ref}:sell", book=book["id"],
                      day_trade=True, check=False, allow_closed=True)
    return [buy, sell]


def _held_before(conn: db.Connection, account_id: int, waiting: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每一筆等著的賣出：帳本在那天之前持有幾股（不夠＝少了買進紀錄）。一次走過帳戶的交易（cursor）。"""
    out: list[dict[str, Any]] = []
    held: dict[str, int] = {}
    pending = sorted(waiting, key=lambda x: x["date"])
    k = 0
    for r in trades.rows(conn, account_ids=[account_id]):
        while k < len(pending) and r["date"] >= pending[k]["date"]:
            out.append({**pending[k], "held": held.get(pending[k]["symbol"], 0)})
            k += 1
        if r["action"] in ("buy", "sell"):
            held[trades.key_of(r)] = held.get(trades.key_of(r), 0) + (r["shares"] if r["action"] == "buy" else -r["shares"])
    out += [{**x, "held": held.get(x["symbol"], 0)} for x in pending[k:]]
    return out
