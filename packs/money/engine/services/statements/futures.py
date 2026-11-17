"""adapter：期貨、選擇權的日報表——每一列成交進一倉（資金操盤那一層）。

描述檔（kind＝futures）除了共同的欄位，還要：

    columns     一定要：date、product、buy_lots、sell_lots、price、fee、tax、offset_pnl（平倉損益）、premium（權利金收支）；
                選填：open_close
    futures     {"pattern": 有 (?P<code>…)(?P<ym>…) 的正規表示式, "contracts": {代號: {"name": 名字, "multiplier": 每點多少錢}}}
    options     選填：{"pattern": 有 code、ym、side、strike 四個具名群組, "sides": {字: "C" 或 "P"}, "name": 名字,
                "multiplier": 每點多少錢, "contracts": {代號: {"weekday": 0～6（0＝週一）, "nth": 那個月第幾個}}}
    total_marker  選填：「總計」列的字（第一格開頭是它）：拿來對整份的口數、費用、損益
    skip_markers  選填：要跳過的列（第一格開頭是它，例：每天的「合計」）
    rounding    選填：{"per_row": 1}——成交價是平均價、只到幾位小數時，契約歸零後跟對帳單的平倉損益差在「列數 × per_row」以內，
                就補一筆零頭（以對帳單為準）；不寫＝不補
    tolerance   選填：逐契約核對時差多少算對得上（預設 1）

規則（跟格式無關，寫在程式裡）：
- 每一列＝一筆交易：shares＝口數、price＝點數、gross＝點數 × 口數 × 每點；選擇權的權利金照對帳單（不一樣就寫進備註）。
- 一天之內的列不一定照時間排，單筆損益可能跟對帳單不一樣，但**每個契約歸零後一定對得上**：逐契約核對（期貨看平倉損益、
  選擇權看權利金收支，都是扣費用前），再對總計列。
- 選擇權買了沒賣掉（或賣了沒買回）＝放到到期：在到期日記一筆 0 元的「到期」歸零（價內到期有拿到錢要另外補，回報裡列出來）。
- 重跑不會多：source＝描述檔的 source_tag，source_ref＝「日期:那天第幾列」；到期「expiry:代號」、零頭「rounding:代號」。
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from ... import db
from ...errors import InvalidInput
from .. import books, refdata, trades
from . import formats

KIND = "futures"
TITLE = "期貨選擇權日報表"
REQUIRED = ("date", "product", "buy_lots", "sell_lots", "price", "fee", "tax", "offset_pnl", "premium")
OPTIONAL = ("open_close",)
OPTIONS = ("book", "close")
TOTALS = ("buy_lots", "sell_lots", "fee", "tax", "offset_pnl", "premium")


def _positive_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def check(raw: dict) -> list[str]:
    out: list[str] = []
    fut = raw.get("futures")
    if not isinstance(fut, dict):
        out.append("futures 要寫：{\"pattern\": …, \"contracts\": {代號: {\"name\": …, \"multiplier\": …}}}")
    else:
        out += formats.pattern_problem(fut.get("pattern"), "futures.pattern", ("code", "ym"))
        c = fut.get("contracts")
        if not isinstance(c, dict) or not c or not all(
                isinstance(v, dict) and isinstance(v.get("name"), str) and _positive_int(v.get("multiplier")) for v in c.values()):
            out.append("futures.contracts 要是 {代號: {\"name\": 名字, \"multiplier\": 正整數}}")
    opt = raw.get("options")
    if opt is not None:
        if not isinstance(opt, dict):
            out.append("options 要是物件（或不寫）")
        else:
            out += formats.pattern_problem(opt.get("pattern"), "options.pattern", ("code", "ym", "side", "strike"))
            sides = opt.get("sides")
            if not isinstance(sides, dict) or not sides or not all(v in ("C", "P") for v in sides.values()):
                out.append("options.sides 要是 {字: \"C\" 或 \"P\"}")
            if not isinstance(opt.get("name"), str) or not _positive_int(opt.get("multiplier")):
                out.append("options 要有 name 與 multiplier（正整數）")
            c = opt.get("contracts")
            if not isinstance(c, dict) or not c or not all(
                    isinstance(v, dict) and isinstance(v.get("weekday"), int) and 0 <= v["weekday"] <= 6
                    and isinstance(v.get("nth"), int) and 1 <= v["nth"] <= 5 for v in c.values()):
                out.append("options.contracts 要是 {代號: {\"weekday\": 0～6, \"nth\": 1～5}}")
    for key in ("total_marker",):
        if raw.get(key) is not None and not (isinstance(raw[key], str) and raw[key].strip()):
            out.append(f"{key} 要是一個字（或不寫）")
    if raw.get("skip_markers") is not None and not formats.strings(raw["skip_markers"]):
        out.append("skip_markers 要是字的清單（或不寫）")
    rnd = raw.get("rounding")
    if rnd is not None and not (isinstance(rnd, dict) and isinstance(rnd.get("per_row"), int) and rnd["per_row"] >= 0):
        out.append("rounding 要是 {\"per_row\": 整數}（或不寫）")
    tol = raw.get("tolerance", 1)
    if not isinstance(tol, int) or isinstance(tol, bool) or tol < 0:
        out.append("tolerance 要是 0 以上的整數")
    return out


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> str | None:
    """那個月第 nth 個星期幾（0＝週一）；沒有就 None。國定假日順延不算（日報表看不出來）。"""
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    d += timedelta(weeks=nth - 1)
    return d.isoformat() if d.month == month else None


def product(fmt: formats.Format, text: str) -> dict[str, Any]:
    """商品那一格 → 代號、名字、期貨或選擇權、每點多少錢、到期日（選擇權）。規則全在描述檔。"""
    t = " ".join((text or "").split())
    fut = fmt.raw["futures"]
    m = re.match(fut["pattern"], t)
    if m and m["code"] in fut["contracts"]:
        spec, ym = fut["contracts"][m["code"]], m["ym"]
        return {"symbol": f"{m['code']}{ym}", "name": f"{spec['name']} {ym[:4]}/{ym[4:]}", "instrument": "future",
                "multiplier": spec["multiplier"], "expiry": None}
    opt = fmt.raw.get("options")
    m = re.match(opt["pattern"], t) if opt else None
    if m and m["code"] in opt["contracts"] and m["side"] in opt["sides"]:
        ym, cp = m["ym"], opt["sides"][m["side"]]
        strike = m["strike"].rstrip("0").rstrip(".") if "." in m["strike"] else m["strike"]
        spec = opt["contracts"][m["code"]]
        try:
            expiry = _nth_weekday(int(ym[:4]), int(ym[4:]), spec["weekday"], spec["nth"])
        except ValueError:
            raise InvalidInput(f"商品「{t}」的年月看不懂") from None
        return {"symbol": f"{m['code']}{ym}{cp}{strike}", "name": f"{opt['name']} {m['code']} {ym[:4]}/{ym[4:]} {m['side']} {strike}",
                "instrument": "option", "multiplier": opt["multiplier"], "expiry": expiry}
    known = list(fut["contracts"]) + (list(opt["contracts"]) if opt else [])
    raise InvalidInput(f"商品「{t}」看不懂", hint="描述檔認得的代號：" + "、".join(known)
                       + "（要加的話寫進描述檔的 futures.contracts 或 options.contracts）")


def read(fmt: formats.Format, head: list[str], body: list[list[str]], file: str) -> dict[str, Any]:
    raw = fmt.raw
    total_marker = (raw.get("total_marker") or "").strip()
    skips = tuple(s.strip() for s in raw.get("skip_markers") or ())
    rows: list[dict[str, Any]] = []
    total = None
    day = None
    order: dict[str, int] = defaultdict(int)
    for n, r in enumerate(body, start=1):
        cell = formats.cell_of(fmt, head, r)
        where = f"第 {n} 列"
        first = cell("date").strip()
        if total_marker and first.startswith(total_marker):
            total = {k: formats.num(cell(k), where) for k in TOTALS}
            continue
        if skips and first.startswith(skips):
            continue
        if first:
            day = formats.day(fmt, first, where)
        if not cell("product").strip():
            continue
        if day is None:
            raise InvalidInput("日報表第一列沒有日期", hint="存網頁時要包含日期那一欄")
        order[day] += 1
        buy, sell = formats.whole(cell("buy_lots"), where), formats.whole(cell("sell_lots"), where)
        if bool(buy) == bool(sell):
            raise InvalidInput(f"{where}（{day}）「{cell('product')}」同一列買賣口數都有（或都沒有），看不懂")
        rows.append({"date": day, "n": order[day], "product": cell("product"),
                     "action": "buy" if buy else "sell", "lots": buy or sell, "price": formats.price(cell("price"), where),
                     "fee": formats.whole(cell("fee"), where), "tax": formats.whole(cell("tax"), where),
                     "offset_pnl": formats.whole(cell("offset_pnl"), where), "premium": formats.whole(cell("premium"), where)})
    if not rows:
        raise InvalidInput("日報表裡沒有成交的列")
    return {"file": file, "rows": rows, "total": total}


def run(conn: db.Connection, fmt: formats.Format, data: dict[str, Any], *, book: Any = None, close: bool = False,
        dry_run: bool = False, actor: str = "import", **_ignored: Any) -> dict[str, Any]:
    if book in (None, ""):
        raise InvalidInput("期貨選擇權要指定記進哪一倉", hint="book=倉的編號（資金操盤那一層的倉）", fields={"book": "必填"})
    rows = data["rows"]
    source = fmt.source_tag
    report: dict[str, Any] = {"format": fmt.id, "kind": KIND, "file": data["file"], "dry_run": dry_run, "rows": len(rows),
                              "created": 0, "replayed": 0, "first": rows[0]["date"], "last": rows[-1]["date"], "notes": []}
    with db.write(conn, actor=actor, source=source, command=f"import_statement {fmt.id}", dry_run=dry_run) as w:
        target = books.resolve(w.conn, book)
        acc = refdata.account_by_id(w.conn, target["account_id"])
        if acc.get("asset_class") != "trading":
            raise InvalidInput(f"倉「{target['name']}」在 {acc['name']}，不是資金操盤那一層的帳戶",
                               hint="期貨選擇權記進資金操盤的倉")
        was_closed = target["status"] != "open"
        per: dict[str, dict[str, Any]] = {}
        for r in rows:
            p = product(fmt, r["product"])
            c = per.setdefault(p["symbol"], {**p, "trades": 0, "net_lots": 0, "offset_pnl": 0, "premium": 0, "last": r["date"]})
            c["trades"] += 1
            c["net_lots"] += r["lots"] if r["action"] == "buy" else -r["lots"]
            c["offset_pnl"] += r["offset_pnl"]
            c["premium"] += r["premium"]
            c["last"] = r["date"]
            gross = round(float(r["price"]) * r["lots"] * p["multiplier"])
            note = ""
            if p["instrument"] == "option" and abs(r["premium"]) != gross:
                note = f"權利金照對帳單 {abs(r['premium']):,}（點數 × 口數 × {p['multiplier']} ＝ {gross:,}）"
                gross = abs(r["premium"])
            t = trades.add(w, date=r["date"], action=r["action"], symbol=p["symbol"], name=p["name"], shares=r["lots"],
                           price=r["price"], gross=gross or None, fee=r["fee"], tax=r["tax"], note=note,
                           source=source, source_ref=f"{r['date']}:{r['n']}", book=target["id"], check=False,
                           allow_closed=True, instrument=p["instrument"], multiplier=p["multiplier"])
            report["created" if t["status"] == "created" else "replayed"] += 1
        expired, still_open = [], []
        for c in per.values():
            if not c["net_lots"]:
                continue
            if c["instrument"] == "option" and c["expiry"]:
                t = trades.add(w, date=c["expiry"], action="sell" if c["net_lots"] > 0 else "buy", symbol=c["symbol"],
                               name=c["name"], shares=abs(c["net_lots"]), price=0, gross=None, fee=0, tax=0,
                               note="到期：日報表沒有結算金額，先當作歸零（價內到期有拿到錢的話要補）",
                               source=source, source_ref=f"expiry:{c['symbol']}", book=target["id"], check=False,
                               allow_closed=True, instrument="option", multiplier=c["multiplier"])
                report["created" if t["status"] == "created" else "replayed"] += 1
                expired.append({"symbol": c["symbol"], "name": c["name"], "lots": c["net_lots"], "expiry": c["expiry"],
                                "last_trade": c["last"]})
            else:
                still_open.append({"symbol": c["symbol"], "name": c["name"], "lots": c["net_lots"]})
        report["expired"], report["open"] = expired, still_open
        report["rounding"] = _rounding(w, fmt, target, per)
        report["contracts"], report["check"] = _verify(w.conn, fmt, target["id"], per, data["total"], rows)
        last = w.conn.execute("SELECT MAX(date) FROM trades WHERE book_id = ?", (target["id"],)).fetchone()[0]
        if was_closed:
            books.resettle(w, target["id"])
        elif close and not still_open:
            books.close(w, target["id"], day=last)
        elif close:
            report["notes"].append("還有期貨沒平倉，沒有收倉")
        b = books.book_view(w.conn, target["id"])
        report["book"] = {"id": b["id"], "name": b["name"], "status": b["status_name"], "result": b["result"],
                          "trades": b["trades"], "first": b["first"], "last": b["last"]}
        report["batch_id"] = None if dry_run or not w.changes else w.batch_id
    return report


def _rounding(w: db.Writer, fmt: formats.Format, target: dict[str, Any], per: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """期貨契約歸零了、跟對帳單的平倉損益只差均價四捨五入的零頭：補一筆零頭，以對帳單為準（描述檔沒寫 rounding＝不補）。"""
    rule = fmt.raw.get("rounding")
    if not rule:
        return []
    pos = {i["symbol"]: i for i in trades.positions(w.conn, book_id=target["id"])["items"]}
    out = []
    for sym, c in sorted(per.items()):
        i = pos.get(sym)
        if c["instrument"] != "future" or not i or i["shares"]:
            continue
        diff = c["offset_pnl"] - (int(i["realized"]) + int(i["fees"]))
        if not diff or abs(diff) > c["trades"] * rule["per_row"]:
            continue
        trades.add(w, date=c["last"], action="other", symbol=sym, name=c["name"], net=diff,
                   note=f"零頭：日報表的成交價是平均價，點數 × 口數 × {c['multiplier']} 跟對帳單的平倉損益差 {diff:+}，照對帳單的補",
                   source=fmt.source_tag, source_ref=f"rounding:{sym}", book=target["id"], check=False, allow_closed=True,
                   instrument="future", multiplier=c["multiplier"])
        out.append({"symbol": sym, "name": c["name"], "amount": diff})
    return out


def _verify(conn: db.Connection, fmt: formats.Format, book_id: int, per: dict[str, dict[str, Any]],
            total: dict[str, float] | None, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """逐契約：引擎算的（扣費用前）＝對帳單的。期貨看平倉損益、選擇權看權利金收支；還沒歸零的不比。"""
    tolerance = int(fmt.raw.get("tolerance", 1))
    pos = {i["symbol"]: i for i in trades.positions(conn, book_id=book_id)["items"]}
    out, bad = [], []
    for sym, c in sorted(per.items()):
        i = pos.get(sym) or {}
        gross_pnl = int(i.get("realized", 0)) + int(i.get("fees", 0))       # realized 已經扣掉手續費和稅
        want = c["offset_pnl"] if c["instrument"] == "future" else c["premium"]
        flat = not i.get("shares")
        item = {"symbol": sym, "name": c["name"], "instrument": c["instrument"], "trades": c["trades"],
                "statement": want, "computed": gross_pnl, "fees": int(i.get("fees", 0)), "realized": int(i.get("realized", 0)),
                "flat": flat, "ok": (abs(gross_pnl - want) <= tolerance) if flat else None}
        if flat and not item["ok"]:
            bad.append(sym)
        out.append(item)
    sums = {"buy_lots": sum(r["lots"] for r in rows if r["action"] == "buy"),
            "sell_lots": sum(r["lots"] for r in rows if r["action"] == "sell"),
            "fee": sum(r["fee"] for r in rows), "tax": sum(r["tax"] for r in rows),
            "offset_pnl": sum(r["offset_pnl"] for r in rows), "premium": sum(r["premium"] for r in rows)}
    total_ok = total is None or all(int(round(total[k])) == v for k, v in sums.items())
    fut = [c for c in out if c["instrument"] == "future"]
    opt = [c for c in out if c["instrument"] == "option"]
    check = {"rows": sums, "statement_total": total, "total_ok": total_ok, "contracts_off": bad,
             "futures_points": sum(c["computed"] for c in fut), "options_premium": sum(c["computed"] for c in opt),
             "fees_and_tax": sum(c["fees"] for c in out), "result": sum(c["realized"] for c in out)}
    return out, check
