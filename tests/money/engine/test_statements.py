"""對帳單匯入（engine/services/statements）：描述檔＋通用讀取器＋adapter。

資料全是假的：假代號（TST1…）、假名字（範例甲…）、假金額、假的年份（2037）。
重點：
- 內建的範例描述檔讀得了 html 成交明細、期貨選擇權日報表、已實現損益；照寫的數字、全部核對、重跑不會多。
- **換一份描述檔就能讀另一種格式**（CSV、自己取的英文欄位名、自己加的契約）：證明程式裡沒寫死任何券商的格式。
- 描述檔有錯不猜：列出問題、那一份不能用；檔案只能在自己的資料夾裡。
"""
import json

import pytest

from money_engine import db
from money_engine.errors import InvalidInput, NotFound
from money_engine.services import books, refdata, statements, trades
from money_engine.services.statements import formats

INV, OP = "證券-投資", "證券-操盤"

# ── 假的對帳單 ────────────────────────────────────────────────────────────────────

TR_HEAD = ("<tr><th>帳號</th><th>交易日期</th><th>櫃員序號</th><th>市場別</th><th>類別</th><th>商品名稱</th><th>數額</th>"
           "<th>單價</th><th>幣別</th><th>成交金額</th><th>手續費</th><th>交易稅</th><th>應收付金額</th></tr>")


def tr(day, seq, kind, name, shares, price, gross, fee, tax, net):
    return (f"<tr><td>證</td><td>{day}</td><td>{seq}</td><td>上市</td><td>{kind}</td><td>{name}</td><td>{shares}</td>"
            f"<td>{price}</td><td>TWD</td><td>{gross}</td><td>{fee}</td><td>{tax}</td><td>{net}</td></tr>")


def sub(gross, fee, tax, net):
    return f"<tr><td>小計</td><td>TWD</td><td>{gross}</td><td>{fee}</td><td>{tax}</td><td>0</td><td></td><td>{net}</td></tr>"


TRADES_HTML = "<html><body><table id='grid'>" + TR_HEAD + "".join([
    tr("2037/09/01", "A1", "零股買進", "範例甲(TST1)", "100", "50.00", "5,000", "7", "0", "-5,007"), sub("5,000", "7", "0", "-5,007"),
    tr("2037/09/03", "A2", "零股賣出", "範例甲(TST1)", "100", "60.00", "6,000", "8", "18", "5,974"),
    tr("2037/09/03", "S1", "現股當沖", "範例乙(TST2)", "1,000", "0.00", "0", "0", "0", "-86"),
    tr("2037/09/03", "S1", "現買當沖", "範例乙(TST2)", "1,000", "20.00", "20,000", "28", "0", "0"),
    tr("2037/09/03", "S1", "現賣當沖", "範例乙(TST2)", "1,000", "20.00", "20,000", "28", "30", "0"),
    sub("46,000", "64", "48", "5,888"),
]) + "</table></body></html>"

# 同樣兩筆（不含當沖），自己整理成 CSV（內建的 trades_csv 描述檔）
TRADES_CSV = ("日期,代號,名稱,買賣,股數,價格,價金,手續費,交易稅,淨收付\r\n"
              "2037-09-01,TST1,範例甲,買進,100,50,5000,7,0,-5007\r\n"
              "2037-09-03,TST1,範例甲,賣出,100,60,6000,8,18,\r\n")


def fut_row(day, oc, product, buy, sell, price, fee, tax, offset, premium):
    first = f"<input value=\"{day}\">" if day else ""
    return (f"<tr><td>{first}</td><td>電子單</td><td>{oc}</td><td>{product}</td><td>{buy}</td><td>{sell}</td><td>{price}</td>"
            f"<td>{fee}</td><td>{tax}</td><td>{offset}</td><td>{premium}</td></tr>")


def fut_sum(label, buy, sell, fee, tax, offset, premium):
    return (f"<tr><td>{label}</td><td></td><td></td><td></td><td>{buy}</td><td>{sell}</td><td></td><td>{fee}</td><td>{tax}</td>"
            f"<td>{offset}</td><td>{premium}</td></tr>")


FUTURES_HTML = ("<html><body><table>"
                "<tr><td>報告書日期</td><td>下單方式</td><td>新/平倉</td><td>商品名稱</td><td>買進口數</td><td>賣出口數</td>"
                "<td>成交價</td><td>手續費</td><td>期交稅</td><td>沖銷損益</td><td>權利金收支</td></tr>"
                + fut_row("2037/07/16", "", "TMF  203708", "3", "", "20,000.33", "60", "24", "0", "0")
                + fut_row("", "平倉", "TMF  203708", "", "3", "20,050.00", "60", "24", "1,491", "0")
                + fut_sum("合計", "3", "3", "120", "48", "1,491", "0")
                + fut_row("2037/07/20", "", "TXO  203707 買權 20000", "3", "", "10.00", "60", "6", "0", "-1,500")
                + fut_row("", "平倉", "TXO  203707 買權 20000", "", "2", "20.00", "40", "4", "0", "2,000")
                + fut_sum("合計", "3", "2", "100", "10", "0", "500")
                + fut_sum("總計", "6", "5", "220", "58", "1,491", "500")
                + "</table></body></html>")

REALIZED_HTML = ("<html><body><table>"
                 "<tr><th>商品名稱</th><th>成交日期</th><th>交易類別</th><th>股數</th><th>成交價</th><th>帳面收入</th>"
                 "<th>投資成本</th><th>損益</th><th>報酬率</th><th>幣別</th></tr>"
                 "<tr><td>範例甲(TST1)</td><td>2037/09/03</td><td>零股賣出</td><td>100</td><td>60.00</td><td>5,974</td>"
                 "<td>5,007</td><td>967</td><td>19%</td><td>台幣</td></tr>"
                 "<tr><td>範例乙(TST2)</td><td>2037/09/10</td><td>現股當沖</td><td>1,000</td><td>21.00</td><td>20,940</td>"
                 "<td>20,028</td><td>912</td><td>4%</td><td>台幣</td></tr>"
                 "<tr><td>範例丙(TST3)</td><td>2037/09/12</td><td>零股賣出</td><td>10</td><td>30.00</td><td>299</td>"
                 "<td>250</td><td>49</td><td>19%</td><td>台幣</td></tr>"
                 "<tr><td></td><td></td><td></td><td></td><td>小計</td><td>27,213</td><td>25,285</td><td>1,928</td><td></td><td>台幣</td></tr>"
                 "</table></body></html>")


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name=INV, kind="securities", asset_class="invest")
        refdata.add_account(w, name=OP, kind="securities", asset_class="trading")
        books.create(w, account=INV, name="2037 投資", start="2037-01-01", end="2037-12-31")
        books.create(w, account=OP, name="操盤練習", kind="history", start="2037-01-01")
    yield c
    c.close()


def put(home, name, text, encoding="utf-8"):
    path = home / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    return name


def put_format(home, raw):
    return put(home, f"formats/{raw['id']}.json", json.dumps(raw, ensure_ascii=False))


def builtin(fid):
    return json.loads(next(t for f, t in statements.files.builtin_formats() if f == f"{fid}.json"))


# ── 內建的描述檔 ─────────────────────────────────────────────────────────────────

def test_builtin_formats_are_all_valid_and_name_no_broker():
    found = statements.catalog()
    assert found["broken"] == []
    ids = {f.id for f in found["formats"]}
    assert {"tw_trades_html", "tw_futures_html", "tw_realized_html", "trades_csv"} <= ids
    assert {f.kind for f in found["formats"]} == set(statements.ADAPTERS)


def test_trades_html_are_recorded_as_written_and_checked(conn, home):
    name = put(home, "trades.html", TRADES_HTML)
    r = statements.run(conn, name, book="操盤練習")
    assert r["format"] == "tw_trades_html"                                     # 沒指定：照表頭自己認
    assert r["created"] == 4 and r["skipped_day_trades"] == 0
    c = r["checks"]
    assert (c["days"], c["days_off"], c["day_trades"], c["day_trades_off"], c["rows_off"]) == (2, [], 1, [], [])
    pos = {p["symbol"]: p for p in trades.positions(conn, account=INV)["items"]}
    assert pos["TST1"]["shares"] == 0 and pos["TST1"]["realized"] == 5974 - 5007
    dt = books.book_view(conn, "操盤練習")
    assert dt["result"] == -86                                                 # 當沖兩腳加起來＝當沖那一列
    again = statements.run(conn, name, book="操盤練習")
    assert again["created"] == 0 and again["replayed"] == 4 and again["batch_id"] is None


def test_day_trade_legs_are_skipped_without_a_book_and_existing_rows_are_not_doubled(conn, home):
    with db.write(conn) as w:
        trades.add(w, date="2037-09-01", action="buy", symbol="TST1", name="範例甲", shares=100, price=50, fee=7,
                   account=INV, check=False)
    r = statements.run(conn, put(home, "trades.html", TRADES_HTML))
    assert r["already"] == 1 and r["created"] == 1 and r["skipped_day_trades"] == 2 and r["differ"] == []


def test_subtotal_that_does_not_add_up_is_listed(conn, home):
    broken = TRADES_HTML.replace(sub("5,000", "7", "0", "-5,007"), sub("5,000", "9", "0", "-5,009"))
    r = statements.run(conn, put(home, "trades.html", broken), dry_run=True)
    assert [d["date"] for d in r["checks"]["days_off"]] == ["2037-09-01"] and r["batch_id"] is None
    assert trades.positions(conn, account=INV)["items"] == []                  # 試算：什麼都沒寫


def test_csv_descriptor_reads_the_same_trades(conn, home):
    """換一份描述檔（內建的 trades_csv）就讀 CSV：同樣兩筆、同樣的結果。"""
    r = statements.run(conn, put(home, "mine.csv", TRADES_CSV, "utf-8-sig"))
    assert r["format"] == "trades_csv" and r["created"] == 2 and r["checks"]["rows_off"] == []
    pos = {p["symbol"]: p for p in trades.positions(conn, account=INV)["items"]}
    assert pos["TST1"]["shares"] == 0 and pos["TST1"]["realized"] == 5974 - 5007   # 淨收付沒寫：照買賣算


def test_own_descriptor_with_other_column_names(conn, home):
    """自己寫一份描述檔：英文欄位、別的日期寫法、別的買賣字——程式一行都不用改。"""
    put_format(home, {
        "schema": 1, "id": "my_export", "kind": "trades", "title": "範例英文匯出", "source": "csv",
        "header": ["Trade Date", "Ticker", "Side", "Qty", "Px"],
        "columns": {"date": "Trade Date", "symbol": "Ticker", "type": "Side", "shares": "Qty", "price": "Px",
                    "fee": "Commission"},
        "date_formats": ["%d.%m.%Y"],
        "types": {"B": {"action": "buy"}, "S": {"action": "sell"}}})
    name = put(home, "export.csv", "Trade Date,Ticker,Side,Qty,Px,Commission\n01.09.2037,TST5,B,10,12.5,1\n"
                                   "02.09.2037,TST5,S,4,13,1\n")
    assert "my_export" in {f.id for f in statements.catalog()["formats"]}
    r = statements.run(conn, name)
    assert r["format"] == "my_export" and r["created"] == 2
    p = trades.positions(conn, account=INV)["items"][0]
    assert (p["symbol"], p["shares"]) == ("TST5", 6)


def test_own_descriptor_with_same_id_replaces_the_builtin(home):
    raw = builtin("trades_csv")
    raw["title"] = "自己改過的"
    put_format(home, raw)
    f = statements.get("trades_csv")
    assert f.origin == "user" and f.title == "自己改過的"


def test_broken_descriptor_is_listed_with_every_problem_and_never_guessed(conn, home):
    put_format(home, {"schema": 1, "id": "bad_one", "kind": "trades", "title": "壞的", "source": "csv",
                      "header": ["Date"], "columns": {"date": "Date", "shares": "Qty"}, "types": {"B": {"action": "keep"}}})
    put(home, "formats/not_json.json", "{oops")
    broken = {b["file"]: b["problems"] for b in statements.catalog()["broken"]}
    assert set(broken) == {"bad_one.json", "not_json.json"}
    assert any("type" in p for p in broken["bad_one.json"]) and any("要列在 header" in p for p in broken["bad_one.json"])
    assert any("product" in p for p in broken["bad_one.json"]) and any("types.B" in p for p in broken["bad_one.json"])
    with pytest.raises(InvalidInput):
        statements.get("bad_one")
    with pytest.raises(NotFound):
        statements.get("no_such_format")


def test_descriptor_problems_cover_every_kind():
    adapters = statements.ADAPTERS
    for fid in ("tw_trades_html", "tw_futures_html", "tw_realized_html"):
        raw = builtin(fid)
        raw["columns"].pop("date")
        fmt, problems = formats.parse(json.dumps(raw), origin="user", file="x.json", adapters=adapters)
        assert fmt is None and any("columns 少了 date" in p for p in problems), fid
    raw = builtin("tw_futures_html")
    raw["futures"]["contracts"]["TMF"]["multiplier"] = 0
    raw["options"]["pattern"] = "(?P<code>x"
    _, problems = formats.parse(json.dumps(raw), origin="user", file="x.json", adapters=adapters)
    assert any("futures.contracts" in p for p in problems) and any("options.pattern" in p for p in problems)


def test_unknown_file_and_files_outside_own_folder(conn, home):
    with pytest.raises(InvalidInput):
        statements.run(conn, put(home, "random.csv", "a,b\n1,2\n"))           # 認不出來：不猜
    with pytest.raises(InvalidInput) as no:
        statements.run(conn, "../outside.html")
    assert str(home) not in no.value.message
    with pytest.raises(NotFound):
        statements.run(conn, "nope.html")


def test_wrong_options_are_refused(conn, home):
    with pytest.raises(InvalidInput):
        statements.run(conn, put(home, "trades.html", TRADES_HTML), close=True)  # 成交明細沒有「收倉」


# ── 期貨選擇權 ───────────────────────────────────────────────────────────────────

def test_futures_statement_checks_every_contract(conn, home):
    name = put(home, "futures.html", FUTURES_HTML)
    r = statements.run(conn, name, book="操盤練習", close=True)
    assert r["format"] == "tw_futures_html"
    assert r["check"]["total_ok"] and r["check"]["contracts_off"] == []
    assert r["rounding"] == [{"symbol": "TMF203708", "name": "微型臺指 2037/08", "amount": 1}]   # 均價零頭：以對帳單為準
    assert r["expired"] == [{"symbol": "TXO203707C20000", "name": "臺指選 TXO 2037/07 買權 20000", "lots": 1,
                             "expiry": "2037-07-15", "last_trade": "2037-07-20"}]
    b = books.resolve(conn, "操盤練習")
    assert b["result"] == 1491 + 500 - 220 - 58
    again = statements.run(conn, name, book="操盤練習")
    assert again["created"] == 0 and again["batch_id"] is None
    assert books.resolve(conn, "操盤練習")["result"] == b["result"]


def test_futures_need_a_trading_book(conn, home):
    name = put(home, "futures.html", FUTURES_HTML)
    with pytest.raises(InvalidInput):
        statements.run(conn, name)
    with pytest.raises(InvalidInput):
        statements.run(conn, name, book="2037 投資")


def test_contracts_and_multipliers_come_from_the_descriptor(conn, home):
    """契約乘數不在程式裡：自己的描述檔多一個假契約 FAKE（每點 5 元），就認得它。"""
    with pytest.raises(InvalidInput) as no:
        statements.run(conn, put(home, "f.html", FUTURES_HTML.replace("TMF  203708", "FAKE 203708")), book="操盤練習")
    assert "描述檔" in no.value.hint
    raw = builtin("tw_futures_html")
    raw["id"] = "my_futures"
    raw["futures"]["contracts"] = {"FAKE": {"name": "範例期貨", "multiplier": 5}}
    raw["futures"]["pattern"] = "^(?P<code>[A-Z]{2,4})\\s*(?P<ym>\\d{6})$"
    raw.pop("options")
    put_format(home, raw)
    text = (FUTURES_HTML.replace("TMF  203708", "FAKE 203708").split("<tr><td><input value=\"2037/07/20\">")[0]
            + "</table></body></html>")
    r = statements.run(conn, put(home, "f2.html", text), fmt_name="my_futures", book="操盤練習")
    t = [x for x in trades.rows(conn, book_id=books.resolve(conn, "操盤練習")["id"])]
    assert r["created"] == 2 and {x["multiplier"] for x in t} == {5}
    assert t[0]["gross"] == round(20000.33 * 3 * 5)


# ── 已實現損益 ───────────────────────────────────────────────────────────────────

def test_realized_checks_sells_adds_day_trades_and_lists_missing_buys(conn, home):
    with db.write(conn) as w:
        trades.add(w, date="2037-09-01", action="buy", symbol="TST1", name="範例甲", shares=100, price=50, fee=7,
                   account=INV, check=False)
        trades.add(w, date="2037-09-03", action="sell", symbol="TST1", name="範例甲", shares=100, price=60, fee=8, tax=18,
                   account=INV, check=False)
    name = put(home, "realized.html", REALIZED_HTML)
    r = statements.run(conn, name, book="操盤練習")
    assert r["format"] == "tw_realized_html"
    assert r["matched"] == 1 and r["income_off"] == []
    assert [x["symbol"] for x in r["day_trades"]] == ["TST2"] and r["created"] == 2
    assert r["book"]["result"] == 912
    sell = [x for x in trades.rows(conn, book_id=books.resolve(conn, "操盤練習")["id"]) if x["action"] == "sell"][0]
    assert (sell["fee"], sell["tax"]) == (29, 31)                              # 稅率在描述檔：21,000 × 0.0015
    assert [(x["symbol"], x["held"]) for x in r["waiting"]] == [("TST3", 0)]  # 沒有買進紀錄：不記、列出來
    assert r["total"] == {"income": 27213.0, "cost": 25285.0, "pnl": 1928.0}
    again = statements.run(conn, name, book="操盤練習")
    assert again["created"] == 0 and again["batch_id"] is None and again["matched"] == 2


def test_account_must_be_clear_when_there_are_several(conn, home):
    with db.write(conn) as w:
        refdata.add_account(w, name="證券-投資二", kind="securities", asset_class="invest")
    name = put(home, "realized.html", REALIZED_HTML)
    with pytest.raises(InvalidInput):
        statements.run(conn, name)
    assert statements.run(conn, name, account=INV, dry_run=True)["account"] == INV
