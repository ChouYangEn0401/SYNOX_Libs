"""對帳單的交易明細（倉、做空、違規）：買、賣、股利、手續費，算得出每一檔的持股、平均成本、已實現損益。

使用情境：把投資帳戶的對帳單統一管理，之後可以做分析與建議，也可以由使用者傳對帳單來自動記錄。

**為什麼獨立一張表，不記進 transactions**：投資帳戶*裡面*的買賣不是錢包的錢在動——錢包只看
「匯錢進投資帳戶」「從投資帳戶領回來」那兩筆轉帳。對帳單是那個帳戶裡面的明細，角色跟
信用卡帳單（`card_statements`）一樣：看得到細節、對得起來，但不會重複算一次錢。

- `net`＝這一筆讓券商帳戶的錢變動多少（正＝進來、負＝出去）。沒給就自己算：
  買＝−(價金＋手續費＋交易稅)、賣＝價金−手續費−交易稅、股利＝價金−費用。給了就照給的（使用者寫多少就是多少）。
- 成本用**移動平均**（單純記錄進出與平均價位，不做試算表那種「整合」）；
  賣出照當下的平均成本算已實現損益。**做空**也算得出來：沒有持股時先賣＝放空（持股變負的），之後買回時結算。
- 持股以**帳戶**為單位一路算下去（同一檔跨年不會被倉切斷）；每一筆賣出實現的損益記到那一筆所在的**倉**（books）。
  模擬倉的交易只在模擬倉裡自己算，不進帳戶的持股。
- 一次走過一個帳戶的交易用 cursor 一列一列處理（`walk`），不把整張表載進記憶體（記憶體要控制好）。
- 重送同一張對帳單不會變兩筆：`source`＋`source_ref` 唯一（跟 ingest 的冪等鍵一樣）。
- 報稅或對券商數字時以**券商的對帳單為準**，這裡是給自己看趨勢用的。
- **期貨、選擇權**：`instrument`＝future／option、`multiplier`＝每點多少錢；shares＝口數、price＝點數、
  gross＝名目金額（點數 × 口數 × 每點）。成本、損益照樣用 gross 算，所以引擎不用分股票或期貨；只有顯示單位（口）、
  平均價（換回點數）、跟基準比（名目金額不是投入的錢，不比）不一樣。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from ..db import Writer, row_dict
from ..errors import InvalidInput, NotFound
from . import refdata, settings
from .transactions import parse_amount, parse_date

ACTIONS = ("buy", "sell", "dividend", "fee", "other")
ACTION_NAMES = {"buy": "買進", "sell": "賣出", "dividend": "股利", "fee": "費用", "other": "其他"}
INSTRUMENTS = {"stock": "股票", "future": "期貨", "option": "選擇權"}
FEE_LIST_RATE = 0.001425                                  # 股票手續費的牌告費率 0.1425%（拿來算「打幾折」）


def cost_rates(r: dict[str, Any]) -> dict[str, Any]:
    """這一筆的手續費、交易稅佔成交金額多少（交易明細要能算出手續費、交易稅等比率）。
    股票：手續費率、幾折（÷ 0.1425%）、交易稅率（賣出才有：一般 0.3%、當沖 0.15%、ETF 0.1%）；期貨選擇權：每口多少。"""
    gross, fee, tax = int(r.get("gross") or 0), int(r.get("fee") or 0), int(r.get("tax") or 0)
    if r.get("action") not in ("buy", "sell") or not gross:
        return {"fee_rate": None, "tax_rate": None, "fee_discount": None, "fee_per_lot": None}
    if (r.get("instrument") or "stock") != "stock":
        lots = int(r.get("shares") or 0)
        return {"fee_rate": round(fee / gross, 8), "tax_rate": round(tax / gross, 8), "fee_discount": None,
                "fee_per_lot": round(fee / lots, 2) if lots else None}
    return {"fee_rate": round(fee / gross, 6), "tax_rate": round(tax / gross, 6) if r["action"] == "sell" else None,
            "fee_discount": round(fee / gross / FEE_LIST_RATE, 3), "fee_per_lot": None}


def unit_of(instrument: str | None) -> str:
    """數量的單位：股票「股」、期貨選擇權「口」。"""
    return "股" if (instrument or "stock") == "stock" else "口"
# 同一天的順序：有時間照時間；沒寫時間的那一筆「接在它之前記的那幾筆後面」（借同一天、同帳戶、比它早記的最晚時間），
# 再照記進來的順序。這樣「09:05 買、沒寫時間的賣」「13:00 的回補先記、09:05 的放空後記」都排得對。
SAME_DAY = ("COALESCE(t.time, (SELECT MAX(t2.time) FROM trades t2 WHERE t2.account_id = t.account_id "
            "AND t2.date = t.date AND t2.id < t.id), '')")
ORDER = f"t.date, {SAME_DAY}, t.id"
ORDER_NEWEST = f"t.date DESC, {SAME_DAY} DESC, t.id DESC"
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
PAGE_MAX = 200                                            # 一次最多回幾筆（網站一頁 50）


def _account(conn: sqlite3.Connection, name: str | None) -> dict[str, Any]:
    """券商帳戶：給了就用、沒給用設定的 invest.default_account、再沒有就第一個證券帳戶。"""
    if name:
        return refdata.resolve_account(conn, name)
    chosen = settings.get(conn, "invest.default_account")
    if chosen:
        return refdata.resolve_account(conn, chosen)
    for a in refdata.accounts(conn):
        if a["kind"] == "securities":
            return a
    raise NotFound("找不到證券帳戶", hint="先在設定頁加一個證券帳戶，或在 account 欄位指定")


def _number(value: Any, label: str) -> int:
    return 0 if value in (None, "", 0, "0") else parse_amount(value, label)


def _time(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    m = TIME_RE.match(text)
    if not m:
        raise InvalidInput(f"時間「{text}」看不懂", hint="寫成 HH:MM，例 09:30", fields={"time": "HH:MM"})
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _price(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        p = float(str(value).replace(",", ""))
    except ValueError:
        raise InvalidInput(f"價格「{value}」看不懂", fields={"price": "數字"}) from None
    if p < 0:
        raise InvalidInput("價格不能是負的", fields={"price": "不能是負的"})
    return f"{p:.4f}".rstrip("0").rstrip(".")


def add(w: Writer, *, date: str, action: str, symbol: str = "", name: str = "", shares: Any = 0,
        price: Any = None, gross: Any = None, fee: Any = 0, tax: Any = 0, net: Any = None,
        account: str | None = None, note: str = "", source: str = "manual",
        source_ref: str | None = None, book: Any = None, time: Any = None, day_trade: bool = False,
        stop_price: Any = None, check: bool = True, allow_closed: bool = False, instrument: str = "stock",
        multiplier: Any = 1) -> dict[str, Any]:
    """記一筆對帳單上的交易。金額一律整數元；股數是整數股（零股也照樣填股數）。
    期貨、選擇權：instrument＝future／option，shares＝口數、price＝點數、multiplier＝每點多少錢。

    倉（book）：給了就用（帳戶跟著倉走）；沒給就照帳戶那一層自動挑（books.pick：投資＝那一年的倉、操盤＝最新開著的倉）。
    check＝要不要判斷違規（匯入歷史資料時不判斷）；allow_closed＝收倉的倉也能補記（匯入補遺用）。"""
    from . import books
    if action not in ACTIONS:
        raise InvalidInput(f"動作「{action}」不對", hint="、".join(f"{k}（{v}）" for k, v in ACTION_NAMES.items()))
    day = parse_date(date, "date")
    target = books.resolve(w.conn, book) if book not in (None, "") else None
    if target and account and refdata.resolve_account(w.conn, account)["id"] != target["account_id"]:
        raise InvalidInput(f"倉「{target['name']}」在 {target['account']}，不是 {account}")
    acc = refdata.account_by_id(w.conn, target["account_id"]) if target else _account(w.conn, account)
    instrument = (instrument or "stock").strip()
    if instrument not in INSTRUMENTS:
        raise InvalidInput(f"商品種類「{instrument}」不對", hint="、".join(f"{k}（{v}）" for k, v in INSTRUMENTS.items()))
    mult = int(multiplier or 1)
    if mult < 1:
        raise InvalidInput("每點多少錢要是正整數", fields={"multiplier": "例：10、50"})
    if instrument == "stock" and mult != 1:
        raise InvalidInput("股票不用乘數（一股就是一股）", hint="期貨、選擇權才給 instrument 和 multiplier")
    if instrument != "stock" and mult == 1:
        raise InvalidInput(f"{INSTRUMENTS[instrument]}要寫每點多少錢", hint="看那一檔的契約規格，例：10、50、200")
    shares_n = int(shares or 0)
    if shares_n < 0:
        raise InvalidInput("股數不能是負的", fields={"shares": "不能是負的"})
    fee_n, tax_n = _number(fee, "fee"), _number(tax, "tax")
    price_s = _price(price)
    gross_n = _number(gross, "gross")
    if not gross_n and price_s is not None and shares_n:
        gross_n = round(float(price_s) * shares_n * mult)
    if net in (None, ""):
        if action == "buy":
            net_n = -(gross_n + fee_n + tax_n)
        elif action in ("sell", "dividend"):
            net_n = gross_n - fee_n - tax_n
        else:
            net_n = -(fee_n + tax_n)
    else:
        net_n = 0 if int(net) == 0 else int(parse_amount(abs(int(net)), "net")) * (1 if int(net) >= 0 else -1)
    if source_ref:
        same = row_dict(w.conn.execute("SELECT * FROM trades WHERE source = ? AND source_ref = ?",
                                       (source, source_ref)).fetchone())
        if same:
            return {**present(w.conn, same), "status": "replayed"}
    if target is None:
        target = books.pick(w, acc, day)
    elif target["status"] != "open" and not allow_closed:
        raise InvalidInput(f"倉「{target['name']}」已經{books.STATUS_NAMES[target['status']]}了，要記進去先重新打開",
                           hint=f"用 /money_pbook_close {target['id']} reopen 重新打開")
    data = {"date": day, "time": _time(time), "account_id": acc["id"], "book_id": target["id"] if target else None,
            "symbol": (symbol or "").strip(), "name": (name or "").strip(),
            "action": action, "shares": shares_n, "price": price_s,
            "gross": gross_n, "fee": fee_n, "tax": tax_n, "net": net_n, "note": (note or "").strip()[:2000],
            "day_trade": int(bool(day_trade)), "stop_price": _price(stop_price), "instrument": instrument, "multiplier": mult,
            "source": source, "source_ref": source_ref, "batch_id": w.batch_id, "created_at": w.now}
    tid = w.conn.execute(f"INSERT INTO trades ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                         tuple(data.values())).lastrowid
    if check and target:
        # 這一筆（和它之後的，補記比較早的交易時）照規矩重新看一次；這一筆自己的值放在 create 的 audit 裡
        books.recheck(w, since=day, account_id=acc["id"])
    after = row_dict(w.conn.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone())
    w.audit("create", "trades", tid, None, after)
    return {**present(w.conn, after), "status": "created"}


def delete(w: Writer, trade_id: int) -> dict[str, Any]:
    before = row_dict(w.conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone())
    if before is None:
        raise NotFound(f"找不到交易明細 #{trade_id}")
    w.conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    w.audit("delete", "trades", trade_id, before, None)
    from . import books
    books.recheck(w, since=before["date"], account_id=before["account_id"])
    return present(w.conn, before)


def present(conn: sqlite3.Connection, r: dict[str, Any]) -> dict[str, Any]:
    acc = refdata.account_by_id(conn, r["account_id"]) if r.get("account_id") else None
    book = conn.execute("SELECT name, kind FROM books WHERE id = ?", (r["book_id"],)).fetchone() if r.get("book_id") else None
    return {**r, "account": acc["name"] if acc else None, "layer": (acc or {}).get("asset_class") or None,
            "book": book["name"] if book else None, "book_kind": book["kind"] if book else None,
            "action_name": ACTION_NAMES.get(r["action"], r["action"]), "unit": unit_of(r.get("instrument")), **cost_rates(r)}


# ---------------------------------------------------------------- 一次走過：持股、平均成本、已實現

@dataclass
class Position:
    symbol: str
    name: str
    account_id: int | None = None
    shares: int = 0          # 負的＝放空中
    cost: int = 0            # 做多＝買進成本；放空＝賣出拿到的錢（都是正的）
    realized: int = 0
    dividends: int = 0
    fees: int = 0
    trades: int = 0
    book_id: int | None = None   # 最後動到這一檔的倉（「在場上」算給它）
    last_date: str = ""
    instrument: str = "stock"
    multiplier: int = 1          # 期貨選擇權：每點多少錢（平均價換回點數）

    def as_dict(self, key: str) -> dict[str, Any]:
        avg = round(self.cost / abs(self.shares) / self.multiplier, 4) if self.shares else None
        return {"key": key, "symbol": self.symbol, "name": self.name, "shares": self.shares, "cost": self.cost,
                "instrument": self.instrument, "multiplier": self.multiplier, "unit": unit_of(self.instrument),
                "avg_price": avg, "side": "short" if self.shares < 0 else ("long" if self.shares else None),
                "realized": self.realized, "dividends": self.dividends, "fees": self.fees, "trades": self.trades,
                "book_id": self.book_id, "last_date": self.last_date, "account_id": self.account_id}


def key_of(r: dict[str, Any] | sqlite3.Row) -> str:
    return r["symbol"] or r["name"] or "（沒寫代號）"


def apply(pos: Position, r: dict[str, Any] | sqlite3.Row) -> int:
    """把一筆交易加進這一檔，回傳這一筆實現的損益（買進開倉、加碼＝0）。"""
    pos.trades += 1
    pos.fees += r["fee"] + r["tax"]
    pos.last_date = r["date"]
    if r["name"] and not pos.name:
        pos.name = r["name"]
    action, qty = r["action"], r["shares"]
    if action == "buy":
        spent, realized = r["gross"] + r["fee"] + r["tax"], 0
        if pos.shares < 0 and qty:                            # 回補放空
            cover = min(qty, -pos.shares)
            back = round(pos.cost * cover / -pos.shares)      # 這幾股當初賣到的錢
            part = round(spent * cover / qty)
            realized = back - part
            pos.shares += cover
            pos.cost = 0 if pos.shares == 0 else pos.cost - back
            qty, spent = qty - cover, spent - part
        if qty:
            pos.shares += qty
            pos.cost += spent
        pos.realized += realized
        return realized
    if action == "sell":
        got, realized = r["gross"] - r["fee"] - r["tax"], 0
        if pos.shares > 0 and qty:                            # 賣掉持股
            close = min(qty, pos.shares)
            out = round(pos.cost * close / pos.shares)
            part = round(got * close / qty)
            realized = part - out
            pos.shares -= close
            pos.cost = 0 if pos.shares == 0 else pos.cost - out
            qty, got = qty - close, got - part
        if qty:                                               # 沒有持股還賣＝放空
            pos.shares -= qty
            pos.cost += got
        pos.realized += realized
        return realized
    if action == "dividend":
        pos.dividends += r["net"]
        return 0
    pos.realized += r["net"]                                  # 費用、其他：直接算進損益
    return r["net"]


def opens(pos_shares: int, r: dict[str, Any] | sqlite3.Row) -> bool:
    """這一筆是不是「進場」（開倉、加碼、放空，或賣超過持股＝翻空、買超過放空＝翻多）；剛好離場、回補不是。"""
    if r["action"] == "buy":
        return pos_shares >= 0 or r["shares"] > -pos_shares
    if r["action"] == "sell":
        return pos_shares <= 0 or r["shares"] > pos_shares
    return False


def rows(conn: sqlite3.Connection, *, account_ids: Iterable[int] | None = None, book_id: int | None = None,
         until_id: int | None = None) -> Iterator[sqlite3.Row]:
    """照時間順序一列一列給（cursor，不整包讀進來）。給 book_id＝只看那一倉；不然是帳戶的真交易（模擬倉不算）。"""
    sql = "SELECT t.* FROM trades t LEFT JOIN books b ON b.id = t.book_id"
    where, args = [], []
    if book_id is not None:
        where.append("t.book_id = ?")
        args.append(book_id)
    else:
        if account_ids is not None:
            ids = list(account_ids)
            if not ids:
                return iter(())
            where.append(f"t.account_id IN ({', '.join('?' * len(ids))})")
            args += ids
        where.append("(b.kind IS NULL OR b.kind <> 'sim')")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return conn.execute(sql + f" ORDER BY {ORDER}", args)


def walk(source: Iterable[sqlite3.Row]) -> Iterator[tuple[sqlite3.Row, int, int, dict[tuple[int, str], Position]]]:
    """一列一列走：給 (那一列, 這一筆實現的損益, 這一筆之前那一檔的股數, 目前所有部位)。
    部位以 (帳戶, 代號) 分開——投資帳戶的台積電跟操盤帳戶的台積電是兩回事，一起走也不會混在一起。"""
    book: dict[tuple[int, str], Position] = {}
    for r in source:
        k = (r["account_id"], key_of(r))
        pos = book.get(k)
        if pos is None:
            pos = book[k] = Position(symbol=r["symbol"], name=r["name"], account_id=r["account_id"],
                                     instrument=r["instrument"], multiplier=r["multiplier"])
        before = pos.shares
        realized = apply(pos, r)
        if r["action"] in ("buy", "sell"):
            pos.book_id = r["book_id"]
        yield r, realized, before, book


def _accounts_of(conn: sqlite3.Connection, account: str | None = None, accounts: Iterable[str] | None = None) -> list[int] | None:
    if account:
        return [refdata.resolve_account(conn, account)["id"]]
    if accounts is not None:
        return [refdata.resolve_account(conn, a)["id"] for a in accounts]
    return None


def positions(conn: sqlite3.Connection, *, account: str | None = None, accounts: Iterable[str] | None = None,
              book_id: int | None = None) -> dict[str, Any]:
    """每一檔現在剩多少股、平均成本多少、已經實現多少損益、領過多少股利（移動平均）。"""
    final: dict[tuple[int, str], Position] = {}
    source = rows(conn, book_id=book_id) if book_id is not None else rows(conn, account_ids=_accounts_of(conn, account, accounts))
    for _r, _realized, _before, book in walk(source):
        final = book
    items = [p.as_dict(k[1]) for k, p in final.items()]
    items.sort(key=lambda x: (x["shares"] == 0, -abs(x["cost"]) if x["shares"] else 0, x["key"]))
    longs = [i for i in items if i["shares"] > 0]
    shorts = [i for i in items if i["shares"] < 0]
    return {"items": items,
            "holding_cost": sum(i["cost"] for i in longs),
            "short_proceeds": sum(i["cost"] for i in shorts),
            "open": len(longs) + len(shorts),
            "realized": sum(i["realized"] for i in items),
            "dividends": sum(i["dividends"] for i in items),
            "fees": sum(i["fees"] for i in items)}


def all_trades(conn: sqlite3.Connection, *, account: str | None = None, accounts: Iterable[str] | None = None,
               symbol: str | None = None, book_id: int | None = None, limit: int = 100, offset: int = 0,
               with_realized: bool = False) -> list[dict[str, Any]]:
    """交易明細，新的在上面，一頁 limit 筆。with_realized＝每一筆附上它實現的損益（要走過整個帳戶一次）。"""
    limit = max(1, min(int(limit), PAGE_MAX))
    sql, args, where = "SELECT t.* FROM trades t", [], []
    ids = _accounts_of(conn, account, accounts)
    if ids is not None:
        if not ids:
            return []
        where.append(f"t.account_id IN ({', '.join('?' * len(ids))})")
        args += ids
    if symbol:
        where.append("(t.symbol = ? OR t.name = ?)")
        args += [symbol, symbol]
    if book_id is not None:
        where.append("t.book_id = ?")
        args.append(book_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {ORDER_NEWEST} LIMIT ? OFFSET ?"
    args += [limit, max(0, int(offset))]
    page = [present(conn, row_dict(r)) for r in conn.execute(sql, args)]
    if with_realized and page:
        want = {t["id"]: t for t in page}
        sims = {t["book_id"] for t in page if t["book_kind"] == "sim"}
        sources = [rows(conn, book_id=b) for b in sims]
        real_accounts = {t["account_id"] for t in page if t["book_kind"] != "sim"}
        if real_accounts:
            sources.append(rows(conn, account_ids=real_accounts))
        for src in sources:
            for r, realized, _before, _book in walk(src):
                if r["id"] in want:
                    want[r["id"]]["realized"] = realized
    return page


def count(conn: sqlite3.Connection, *, accounts: Iterable[str] | None = None, book_id: int | None = None) -> int:
    sql, args, where = "SELECT COUNT(*) FROM trades t", [], []
    ids = _accounts_of(conn, None, accounts)
    if ids is not None:
        if not ids:
            return 0
        where.append(f"t.account_id IN ({', '.join('?' * len(ids))})")
        args += ids
    if book_id is not None:
        where.append("t.book_id = ?")
        args.append(book_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return conn.execute(sql, args).fetchone()[0]


def text_positions(data: dict[str, Any]) -> str:
    if not data["items"]:
        return "還沒有對帳單的交易明細。"
    lines = []
    for i in data["items"]:
        label = f"{i['symbol']} {i['name']}".strip() or i["key"]
        if i["shares"] > 0:
            lines.append(f"{label}：{i['shares']:,} {i['unit']}，平均成本 {i['avg_price']}，投入 {i['cost']:,}"
                         + (f"，已實現 {i['realized']:+,}" if i["realized"] else ""))
        elif i["shares"] < 0:
            lines.append(f"{label}：放空 {-i['shares']:,} {i['unit']}，平均賣價 {i['avg_price']}"
                         + (f"，已實現 {i['realized']:+,}" if i["realized"] else ""))
    closed = [i for i in data["items"] if not i["shares"]]
    if closed:
        lines.append(f"已出清 {len(closed)} 檔，合計已實現 {sum(i['realized'] for i in closed):+,}")
    lines.append(f"合計：持股成本 {data['holding_cost']:,}、已實現 {data['realized']:+,}、"
                 f"股利 {data['dividends']:,}、手續費與稅 {data['fees']:,}")
    return "\n".join(lines)
