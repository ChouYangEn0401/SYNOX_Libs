"""撲滿（W8）：投資與資金操盤的倉、交易明細、跟基準比、對明細（股數）、塞價格。

沒有證券帳戶、也沒開投資選配時，讀 meta 回 piggy=false，畫面不顯示這個分頁（main.js 的 app.hideTab；W3 的 meta 決定）。
pbook：沒給 id＝開新的一倉；給 id＝改那一倉（含捐贈比例與打勾）。
欄位名是契約（app/js/routes.js 照這些名字送值）：可以加，不要改名或拿掉。

每個 run 只做「字串欄位 → 引擎的參數 → 呼叫 services → 組回覆」；計算全在 engine/services
（piggy、books、bench、quotes、trades、reconcile）。寫入一律經 db.write，回傳帶 batch_id（沒改到東西是 None）與 write_seq。
這裡另外守一條引擎沒有的規矩：**給了倉，就要是這一層的倉**（不然模擬倉會跑到別層顯示）。

捐贈：欄位名 tithe_rate／tithe_amount 沿用引擎（改名會動到資料庫與引擎的 API），畫面上與回覆裡的字一律寫「捐贈」，
而且只有開了「捐贈」選配（giving 那一格開著）時才出現。

基準：不預設任何代號，只給年率；名字自己取（bench_name，存在設定 bench.symbol），沒取就叫「基準」。

金額與日期：全工具共用 bridge/table.py 的換法——畫面送來的已經是最小單位；人打字時照帳本的小數位數轉。
股價、停損價是「每股多少」，照原樣給引擎（引擎收到小數第 4 位）。
模組層級只放常數（隔離規則 4）。
"""

import functools
import re

from ..engine import db
from ..engine.errors import InvalidInput
from ..engine.services import bench, books, piggy as piggy_service, quotes, reconcile, settings, trades
from . import table
from .home import _flag, _text
from .table import CLEAR, WRITE, Cmd, Field, Result, frozen

DOMAIN = "piggy"

BENCH_WORD = "基準"                           # 基準沒取名字時畫面與回覆用的字
LEVELS = tuple(books.LAYERS)                   # invest、trading
LEVEL_WORDS = (("invest", "invest"), ("投資", "invest"), ("trading", "trading"), ("操盤", "trading"),
               ("資金操盤", "trading"))
ACTION_WORDS = (("buy", "buy"), ("買", "buy"), ("買進", "buy"), ("sell", "sell"), ("賣", "sell"), ("賣出", "sell"),
                ("dividend", "dividend"), ("股利", "dividend"), ("配息", "dividend"), ("fee", "fee"), ("費用", "fee"),
                ("other", "other"), ("其他", "other"))
INSTRUMENT_WORDS = (("stock", "stock"), ("股票", "stock"), ("future", "future"), ("期貨", "future"),
                    ("option", "option"), ("選擇權", "option"))
KIND_WORDS = (("real", "real"), ("真倉", "real"), ("sim", "sim"), ("模擬", "sim"), ("模擬倉", "sim"),
              ("history", "history"), ("歷史", "history"), ("歷史倉", "history"))
DO_WORDS = (("close", "close"), ("收倉", "close"), ("reopen", "reopen"), ("重新打開", "reopen"), ("打開", "reopen"),
            ("archive", "archive"), ("封存", "archive"), ("刪掉", "archive"), ("unarchive", "unarchive"),
            ("拿回來", "unarchive"))
DO_NAMES = (("close", "收倉"), ("reopen", "重新打開"), ("archive", "封存"), ("unarchive", "從封存拿回來"))
STATUS_WORDS = (("created", "新的"), ("updated", "更新"), ("same", "一樣"))
PAGE, PAGE_COMPARE = 50, 20
INT_RE = re.compile(r"^\d+$")
FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


# ── 小工具：字串欄位 → 引擎的參數 ────────────────────────────────────────────────────

def _given(values, name):
    return (values or {}).get(name) is not None and _text(values, name) != ""


def _pick(pairs, values, name, hint, required=False):
    text = _text(values, name).lower()
    if not text:
        if required:
            raise InvalidInput(f"要給 {name}", hint=hint, fields={name: "必填"})
        return None
    for word, value in pairs:
        if text == word:
            return value
    raise InvalidInput(f"「{_text(values, name)}」看不懂", hint=hint, fields={name: "看不懂"})


def _level(values, name="level", required=False):
    return _pick(LEVEL_WORDS, values, name, "invest（投資）或 trading（資金操盤）", required)


def _int(values, name, default=None, low=0):
    """整數（股數、編號、第幾筆開始）：收千分位、全形數字、「股」字。"""
    raw = _text(values, name)
    if not raw:
        return default
    t = raw.translate(FULLWIDTH).replace(",", "").replace("，", "").replace("股", "").replace(" ", "")
    if not INT_RE.match(t):
        raise InvalidInput(f"{name}「{raw}」要是整數", fields={name: "整數"})
    n = int(t)
    if n < low:
        raise InvalidInput(f"{name} 不能小於 {low}", fields={name: "太小"})
    return n


def _id(values, name="id"):
    n = _int(values, name)
    if n is None:
        raise InvalidInput(f"要給 {name}（編號）", fields={name: "必填"})
    return n


def _decimal(values, name):
    """股價、停損價：數字字串（引擎自己收到小數第 4 位）；沒給回 None。"""
    raw = _text(values, name).translate(FULLWIDTH).replace(",", "").replace("，", "").replace("元", "")
    if not raw:
        return None
    if not re.fullmatch(r"\d+(\.\d+)?", raw):
        raise InvalidInput(f"{name}「{_text(values, name)}」要寫數字", hint="例：33.1", fields={name: "數字"})
    return raw


def _write(book, command):
    return db.write(book.conn, actor=book.actor, source=book.actor, command=command, dry_run=False)


def _out(bk, batch, **data):
    """寫入的回傳：data＋batch_id（沒改到東西是 None）＋write_seq。（第一個參數不叫 book：data 裡常有 book。）"""
    return {**data, "batch_id": batch, "write_seq": db.write_seq(bk.conn)}


giving_on = books.giving_on                     # 開了「捐贈」選配沒有（引擎判斷；待處理也照它）


def bench_name(conn):
    return str(bench.config(conn)["symbol"] or "") or BENCH_WORD


def book_in(conn, book_id, level):
    """給了倉就要是這一層的倉（不然模擬倉會跑到別層顯示）；沒給回 None。引擎沒有這條，畫面與指令都經這裡。"""
    if book_id is None:
        return None
    b = books.get(conn, book_id)
    if (b.get("layer") or "") != level:
        where = books.LAYERS.get(b.get("layer") or "", "別的地方")
        raise InvalidInput(f"倉「{b['name']}」不在這一層", hint=f"它在{where}", fields={"book": "不在這一層"})
    return book_id


def _signed(book, n):
    n = int(n or 0)
    return ("+" if n > 0 else "") + table.show(book.conn, n)


def _book_line(book, b):
    bits = [f"#{b['id']} {b['name']}（{b['kind_name']}・{b['status_name']}）"]
    if b.get("capital"):
        bits.append(f"操作金額 {table.show(book.conn, b['capital'])}")
    if b.get("max_loss"):
        bits.append(f"可虧損 {table.show(book.conn, b['max_loss'])}")
    if b.get("per_trade_loss"):
        bits.append(f"單次 {table.show(book.conn, b['per_trade_loss'])}")
    if b.get("trades"):
        bits.append(f"結果 {_signed(book, b.get('result'))}")
    return "　".join(bits)


def _giving_line(book, b):
    """收倉有賺的真倉：捐贈多少、做了沒（沒開捐贈選配就不講）。"""
    if not (giving_on(book.conn) and b["status"] != "open" and b["kind"] == "real" and (b.get("tithe_amount") or 0) > 0):
        return ""
    done = f"，{b['tithe_done_at']} 做完" if b.get("tithe_done_at") else "，還沒做"
    return f"捐贈 {table.show(book.conn, b['tithe_amount'])}（{float(b['tithe_rate_used']) * 100:g}%{done}）"


# ── 讀 ─────────────────────────────────────────────────────────────────────────

def read_piggy(book, params):
    """撲滿首頁（level 不給）或某一層（cash／invest／trading／property）。沒有任何投資帳戶也照樣回（全空），不會壞。"""
    out = piggy_service.piggy(book.conn, _text(params, "period") or None, _text(params, "level") or None)
    out["giving"] = giving_on(book.conn)           # 沒開時 pending 本來就不列捐贈（books.pending）
    out["bench_name"] = bench_name(book.conn)
    out["has_layers"] = any(lv["count"] for lv in out["levels"] if lv["key"] in LEVELS)
    return out


def read_trades(book, params):
    """交易明細：一層（或一倉）全部記在一起，新的在上面，一頁 limit 筆；每一筆附它實現的損益。"""
    conn = book.conn
    level = _level(params, required=True)
    book_id = book_in(conn, _int(params, "book"), level)
    symbol = _text(params, "symbol") or None
    offset = _int(params, "offset", 0)
    limit = max(1, min(_int(params, "limit", PAGE), trades.PAGE_MAX))
    names = [a["name"] for a in books.layer_accounts(conn, level)] if book_id is None else None
    items = trades.all_trades(conn, accounts=names, book_id=book_id, symbol=symbol, limit=limit, offset=offset,
                              with_realized=True)
    total = trades.count(conn, accounts=names, book_id=book_id) if not symbol else None
    return {"items": items, "offset": offset, "limit": limit, "total": total,
            "more": len(items) == limit and (total is None or offset + limit < total)}


def read_positions(book, params):
    level = _level(params, required=True)
    return piggy_service.positions_view(book.conn, level, book_in(book.conn, _int(params, "book"), level))


def read_book(book, params):
    """點進一倉：結果、規矩、跟基準比、還在場上的。"""
    out = piggy_service.book_detail(book.conn, _id(params))
    out["giving"] = giving_on(book.conn)
    out["bench_name"] = bench_name(book.conn)
    return out


def read_compare(book, params):
    """個股 vs 基準（不管輸贏一直列著，收起來的除外）。quotes＝用塞進來的價格；book＝只看那一倉。"""
    level = _level(params, required=True)
    out = piggy_service.symbol_compare(
        book.conn, level, use_quotes=_flag(params, "quotes"), offset=_int(params, "offset", 0),
        limit=_int(params, "limit", PAGE_COMPARE), show_hidden=_flag(params, "hidden"),
        book_id=book_in(book.conn, _int(params, "book"), level))
    out["bench_name"] = bench_name(book.conn)
    return out


def read_books(book, params):
    """倉的清單；archived＝只看封存的。"""
    archived = _flag(params, "archived")
    items = books.list_books(book.conn, layer=_level(params), include_archived=archived)
    if archived:
        items = [b for b in items if b["status"] == "archived"]
    return {"books": items}


def read_quotes(book, params):
    """每一檔最新塞進來的價格與時間；沒有的列在 missing。"""
    raw = _text(params, "symbols").replace("，", ",").replace("、", ",")
    asked = [s.strip().upper() for s in raw.split(",") if s.strip()][:200]
    found = quotes.latest(book.conn, asked)
    return {"prices": found, "asked": asked, "missing": [s for s in asked if s not in found]}


def read_quotes_needed(book, params):
    """要塞價格的清單（現在還持有的每一檔）。"""
    return {"items": quotes.needed(book.conn)}


def read_reconcile(book, params):
    """對明細：兩邊的最後核對日＋檢查模式的清單（只讀）。"""
    return reconcile.check(book.conn)


# ── 動作（同時是指令）──────────────────────────────────────────────────────────────

def run_trade_add(book, values):
    conn = book.conn
    action = _pick(ACTION_WORDS, values, "action", "buy（買進）／sell（賣出）／dividend（股利）／fee（費用）／other（其他）",
                   required=True)
    day = table.day_or_none(values, "date", book.today) or book.today.isoformat()
    level = _level(values)
    account = _text(values, "account") or None
    book_id = _int(values, "book")
    if book_id is not None and level:
        book_in(conn, book_id, level)
    if not account and book_id is None and level:
        accs = books.layer_accounts(conn, level)
        if not accs:
            raise InvalidInput(f"還沒有帳戶算「{books.LAYERS[level]}」",
                               hint="到設定的帳戶，把證券帳戶的「算撲滿哪一層」選這一層")
        account = accs[0]["name"]
    instrument = _pick(INSTRUMENT_WORDS, values, "instrument", "stock（股票）／future（期貨）／option（選擇權）") or "stock"
    shares = _int(values, "shares", 0)
    if action in ("buy", "sell") and not shares:
        raise InvalidInput("買賣要寫股數（期貨選擇權寫口數）", fields={"shares": "必填"})
    with _write(book, "trade_add") as w:
        t = trades.add(w, date=day, action=action, symbol=_text(values, "symbol"), name=_text(values, "name"),
                       shares=shares, price=_decimal(values, "price"), gross=table.amount_of(book, values, "gross", zero=True, negative=True),
                       fee=table.amount_of(book, values, "fee", zero=True, negative=True) or 0, tax=table.amount_of(book, values, "tax", zero=True, negative=True) or 0,
                       net=table.amount_of(book, values, "net", zero=True, negative=True), account=account, note=_text(values, "note"),
                       source="synox", book=book_id, time=_text(values, "time") or None,
                       day_trade=_flag(values, "day_trade"), stop_price=_decimal(values, "stop_price"),
                       instrument=instrument, multiplier=_int(values, "multiplier", 1, low=1))
        batch = w.batch_id if w.changes else None
    warnings = [t["violation"]] if t.get("violation") else []
    if t.get("book_id"):
        b = books.book_view(conn, t["book_id"])
        if b["rules_missing"]:
            warnings.append(f"倉「{b['name']}」還沒設可虧損——先設好再下單")
        elif b.get("stopped"):
            warnings.append(f"倉「{b['name']}」的可虧損打完了，這一倉停止")
    unit = t.get("unit") or "股"
    what = " ".join(x for x in (t["action_name"], t["symbol"], t["name"]) if x)
    size = (f" {t['shares']:,} {unit}" + (f" @ {t['price']}" if t.get("price") else "")) if t["shares"] else ""
    where = f"（{t['account']}" + (f"・倉「{t['book']}」" if t.get("book") else "") + "）"
    text = f"📈 記好了：{t['date']} {what}{size}，淨收付 {_signed(book, t['net'])}{where}　編號 {t['id']}"
    return Result(data=_out(book, batch, trade=t, warnings=warnings), text=text, warnings=warnings)


def run_trade_rm(book, values):
    with _write(book, "trade_rm") as w:
        t = trades.delete(w, _id(values))
        batch = w.batch_id
    what = " ".join(x for x in (t["action_name"], t["symbol"], t["name"]) if x)
    return Result(data=_out(book, batch, trade=t),
                  text=f"🗑 刪掉交易明細 #{t['id']}：{t['date']} {what}（可以用 /money_undo 復原）")


BOOK_FIELDS = ("name", "capital", "max_loss", "per_trade_loss", "tithe_rate", "note")
BOOK_EDIT_FIELDS = BOOK_FIELDS + ("start_date", "end_date")
KEEP_WHEN_CLEARED = ("name", "start_date")          # 名字與開始日不能清：給「-」或空白當沒給


def _book_values(book, values, fields):
    """pbook 的欄位 → books.create／update 的參數。「-」＝清掉（引擎收空字串）；金額照帳本的小數位數換。"""
    out = {}
    for key in fields:
        if (values or {}).get(key) is None:
            continue
        raw = _text(values, key)
        if raw in (CLEAR, "") and key in KEEP_WHEN_CLEARED:
            continue
        if raw == CLEAR:
            out[key] = ""
        elif key in ("capital", "max_loss", "per_trade_loss") and raw:
            out[key] = table.amount_of(book, values, key, zero=True, negative=True)
        elif key in ("start_date", "end_date") and raw:
            out[key] = table.day_or_none(values, key, book.today)
        else:
            out[key] = raw
    return out


def run_pbook(book, values):
    conn = book.conn
    giving = giving_on(conn)
    book_id = _int(values, "id")
    data = _book_values(book, values, BOOK_FIELDS if book_id is None else BOOK_EDIT_FIELDS)
    done = _flag(values, "done", None) if _given(values, "done") else None
    if not giving:
        if done is not None or data.get("tithe_rate") not in (None, ""):
            raise InvalidInput("還沒開「捐贈」這個選配功能", hint="到設定頁的選配功能打開「捐贈」")
        data.pop("tithe_rate", None)
    if book_id is None:                                           # 開一倉
        level = _level(values)
        account = _text(values, "account") or None
        if not level and not account:
            raise InvalidInput("開倉要給 level（invest／trading）或 account（證券帳戶）", fields={"level": "必填"})
        if done is not None:
            raise InvalidInput("捐贈要等收倉之後才打勾", fields={"done": "收倉之後"})
        kind = _pick(KIND_WORDS, values, "kind", "real（真倉）／sim（模擬倉）／history（歷史倉）") or "real"
        with _write(book, "pbook") as w:
            b = books.create(w, account=account, layer=level, name=data.get("name"), kind=kind,
                             start=table.day_or_none(values, "start", book.today), end=table.day_or_none(values, "end", book.today),
                             capital=data.get("capital") or 0, max_loss=data.get("max_loss"),
                             per_trade_loss=data.get("per_trade_loss"), tithe_rate=data.get("tithe_rate"),
                             note=data.get("note") or "")
            batch = w.batch_id
        view = books.book_view(conn, b["id"])
        return Result(data=_out(book, batch, book=view), text=f"🐷 開好一倉：{_book_line(book, view)}")
    with _write(book, "pbook") as w:                              # 改一倉
        if data:
            books.update(w, book_id, **data)
        if done is not None:
            books.tithe(w, book_id, done=done)
        batch = w.batch_id if w.changes else None
    view = books.book_view(conn, book_id)
    extra = _giving_line(book, view)
    text = (f"🐷 改好了：{_book_line(book, view)}" if batch else f"沒有要改的：{_book_line(book, view)}") + (
        f"\n{extra}" if extra else "")
    return Result(data=_out(book, batch, book=view), text=text)


def run_pbook_close(book, values):
    book_id = _id(values)
    do = _pick(DO_WORDS, values, "do", "close（收倉）／reopen（重新打開）／archive（封存）／unarchive（拿回來）",
               required=True)
    with _write(book, f"pbook_close {do}") as w:
        if do == "close":
            books.close(w, book_id)
        elif do == "reopen":
            books.reopen(w, book_id)
        else:
            books.archive(w, book_id, undo=(do == "unarchive"))
        batch = w.batch_id if w.changes else None
    view = books.book_view(book.conn, book_id)
    word = next(n for k, n in DO_NAMES if k == do)
    lines = [f"🐷 {view['name']}：{word}了" if batch else f"{view['name']} 本來就是「{view['status_name']}」",
             _book_line(book, view)]
    extra = _giving_line(book, view)
    if extra and do == "close":
        lines.append(extra + "：做完了到「撲滿待處理」打勾")
    return Result(data=_out(book, batch, book=view), text="\n".join(lines))


def run_piggy_hide(book, values):
    layer = _level(values, "layer", required=True)
    key = _text(values, "key")
    if not key:
        raise InvalidInput("要給哪一檔（key）", fields={"key": "必填"})
    hidden = _flag(values, "hidden", True)
    with _write(book, "piggy_hide") as w:
        piggy_service.hide(w, layer, key, hidden)
        batch = w.batch_id if w.changes else None
    word = "收起來了（在撲滿那一層的「設定」拿回來）" if hidden else "拿回來了"
    return Result(data=_out(book, batch), text=f"{key} {word}")


SETTING_KEYS = (("bench_rate", "bench.rate"), ("bench_low", "bench.low"), ("tithe_rate", "tithe.rate"),
                ("month_max_loss", "trading.month_max_loss"))


def run_piggy_set(book, values):
    conn = book.conn
    giving = giving_on(conn)
    with _write(book, "piggy_set") as w:
        for field, key in SETTING_KEYS:
            raw = _text(values, field)
            if not raw or raw == CLEAR or (field == "tithe_rate" and not giving):
                continue
            if field == "month_max_loss":
                raw = str(table.amount_of(book, values, field, zero=True, negative=True) or 0)
            settings.put(w, key, settings.coerce(conn, key, raw))
        if (values or {}).get("bench_name") is not None:
            name = _text(values, "bench_name")
            if name in (CLEAR, ""):
                settings.put(w, "bench.symbol", "")             # 清掉：畫面寫「基準」
            elif len(name) > 20:
                raise InvalidInput("基準的名字最多 20 個字", fields={"bench_name": "太長"})
            else:
                settings.put(w, "bench.symbol", settings.coerce(conn, "bench.symbol", name))
        batch = w.batch_id if w.changes else None
    cfg = bench.config(conn)
    limit = books.month_limit(conn)
    data = _out(book, batch, bench=cfg, bench_name=bench_name(conn), tithe_rate=books.tithe_rate(conn),
                month_max_loss=limit)
    bits = [f"{bench_name(conn)} 年 {cfg['rate'] * 100:g}%", f"保守線 {cfg['low'] * 100:g}%"]
    if giving:
        bits.append(f"捐贈 {books.tithe_rate(conn) * 100:g}%")
    bits.append(f"本月可虧損 {table.show(book.conn, limit) if limit else '沒設'}")
    return Result(data=data, text=("⚙️ 撲滿的設定存好了：" if batch else "撲滿的設定沒有變：") + "、".join(bits))


def run_count(book, values):
    as_of = table.day_or_none(values, "as_of", book.today)
    with _write(book, "count") as w:
        r = reconcile.count(w, account=_text(values, "account"), symbol=_text(values, "symbol"),
                            shares=_int(values, "shares"), as_of=as_of, note=_text(values, "note"), source="synox")
        batch = w.batch_id if w.changes else None
    if r["match"]:
        text = f"✅ {r['symbol']} 對上了：{r['counted']:,} 股（{r['as_of']}）"
    else:
        text = (f"⚠️ {r['symbol']} 記下 {r['counted']:,} 股，跟帳上差 {abs(r['diff']):,}（照交易明細算是 {r['derived']:,} 股）；"
                "多半是漏記買進、賣出、配股或零股")
    return Result(data=_out(book, batch, **r), text=text)


def run_price_push(book, values):
    pairs = quotes.parse_pairs([_text(values, "prices")])
    source = (_text(values, "source") or "synox")[:40]
    with _write(book, "price_push") as w:
        rows = quotes.push(w, pairs, as_of=_text(values, "at") or None, source=source)
        batch = w.batch_id if w.changes else None
    lines = [f"💹 收到 {len(rows)} 檔價格："]
    lines += [f"{r['symbol']} {r['price']}（{r['as_of']}，{next((n for k, n in STATUS_WORDS if k == r['status']), '')}）"
              for r in rows]
    return Result(data=_out(book, batch, items=rows), text="\n".join(lines))


# ── 小數位數不是 0 的帳本：先不開放撲滿 ─────────────────────────────────────────────────
# 引擎算價金（股數×股價）、損益時假設金額是整數元；小數位數 2 的帳本會差 100 倍。
# 這種帳本 meta 的 piggy 回 false（分頁不出現），撲滿的讀與動作一律回白話的錯（對明細卡 reconcile 不受影響）。

WHOLE_ONLY = "撲滿目前只支援整數幣別"


def _whole_only(fn):
    @functools.wraps(fn)
    def guarded(book, values):
        if table.decimals(book.conn):
            raise InvalidInput(WHOLE_ONLY, hint="這本帳的金額有小數；撲滿的價金與損益還只會算整數元，先不開放")
        return fn(book, values)
    return guarded


# ── 表 ─────────────────────────────────────────────────────────────────────────

COMMANDS = (
    Cmd("trade_add", "記一列交易明細（買、賣、股利、費用）", WRITE, [
        Field("date", "哪一天（今天、昨天、1/15、2030-01-15）", required=True),
        Field("action", "buy／sell／dividend／fee／other（也收 買、賣、股利、費用）", required=True),
        Field("level", "哪一層：invest／trading"),
        Field("account", "證券帳戶"),
        Field("book", "記進哪一倉（編號）"),
        Field("time", "幾點"),
        Field("symbol", "代號"),
        Field("name", "名稱"),
        Field("shares", "股數（期貨選擇權填口數）"),
        Field("price", "價格"),
        Field("gross", "價金（不給就用股數×價格）"),
        Field("fee", "手續費"),
        Field("tax", "稅"),
        Field("net", "淨收付（不給就自己算）"),
        Field("note", "備註"),
        Field("day_trade", "當沖（yes／no）"),
        Field("stop_price", "停損價"),
        Field("instrument", "stock／future／option"),
        Field("multiplier", "每點多少錢（期貨選擇權）")], _whole_only(run_trade_add), positional=2, app=True),
    Cmd("trade_rm", "刪一列交易明細（可以整批復原）", WRITE, [Field("id", "哪一列（編號）", required=True)],
        _whole_only(run_trade_rm), positional=1, app=True),
    Cmd("pbook", "開一倉（不給 id），或改一倉：名字、期間、本金、可虧損、捐贈比例與打勾", WRITE, [
        Field("id", "哪一倉（不給＝開新的）"),
        Field("level", "哪一層（開倉用）：invest／trading"),
        Field("account", "證券帳戶（開倉用）"),
        Field("name", "名字"),
        Field("kind", "real／sim／history（開倉用）"),
        Field("start", "開始（開倉用）"),
        Field("end", "結束（開倉用）"),
        Field("start_date", "開始（改倉用）"),
        Field("end_date", "結束（改倉用；- 清掉）"),
        Field("capital", "操作金額"),
        Field("max_loss", "可虧損（- 清掉）"),
        Field("per_trade_loss", "單次可虧損（- 清掉）"),
        Field("tithe_rate", "捐贈比例（例 10%；- 用預設）"),
        Field("done", "捐贈做了（yes／no）"),
        Field("note", "備註")], _whole_only(run_pbook), positional=0, app=True),
    Cmd("pbook_close", "收倉、重新打開、封存、從封存拿回來", WRITE, [
        Field("id", "哪一倉（編號）", required=True),
        Field("do", "close／reopen／archive／unarchive", required=True)], _whole_only(run_pbook_close), positional=2, app=True),
    Cmd("piggy_hide", "跟基準比的表裡收起來／放回來一檔", WRITE, [
        Field("layer", "哪一層：invest／trading", required=True),
        Field("key", "哪一檔", required=True),
        Field("hidden", "收起來（yes／no，預設 yes）")], _whole_only(run_piggy_hide), positional=2, app=True),
    Cmd("piggy_set", "撲滿的設定：基準年率、保守線、捐贈比例、本月可虧損、基準的名字", WRITE, [
        Field("bench_rate", "基準年率（例 10%）"),
        Field("bench_low", "保守線（例 5%）"),
        Field("tithe_rate", "捐贈比例（例 10%）"),
        Field("month_max_loss", "本月可虧損（0＝不設）"),
        Field("bench_name", "基準叫什麼（自己取的名字或代號；那一檔不跟自己比；- 清掉）")], _whole_only(run_piggy_set), app=True),
    Cmd("count", "記下實際股數（對明細；不改交易明細）", WRITE, [
        Field("account", "證券帳戶", required=True),
        Field("symbol", "代號", required=True),
        Field("shares", "股數（0＝已經沒有了）", required=True),
        Field("as_of", "哪一天（預設今天）"),
        Field("note", "備註")], _whole_only(run_count), positional=3, app=True),
    Cmd("price_push", "塞價格（代號=價格，空白分開；這個工具不自己抓股價）", WRITE, [
        Field("prices", "價格（例如 TEST1=101.5 TEST2=33）", required=True),
        Field("at", "什麼時候的價格（YYYY-MM-DD 或 YYYY-MM-DD HH:MM）"),
        Field("source", "從哪裡來")], _whole_only(run_price_push), positional=1, app=True),
)

READS = frozen({**{name: _whole_only(fn) for name, fn in (
    ("piggy", read_piggy), ("piggy_trades", read_trades), ("piggy_positions", read_positions), ("piggy_book", read_book),
    ("piggy_compare", read_compare), ("books", read_books), ("quotes", read_quotes),
    ("quotes_needed", read_quotes_needed))},
    "reconcile": read_reconcile})
EXPORTS = frozen({})
