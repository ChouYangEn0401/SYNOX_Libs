"""撲滿：現金層剩下的、目前已有、基準尺度（影子池、藍圖）、個股比較與收起來、塞進來的價格、
監督（捐贈、違規、停止）、轉進操盤帳戶算進「資金操盤」那一格、撲滿整包。"""
from datetime import date

import pytest

from money_engine import db
from money_engine.errors import InvalidInput, NotFound
from money_engine.services import (allowance, balances, bench, books, home, ingest, piggy, plans, quotes, refdata,
                                   settings, stats, trades)

INV = "證券-投資"
OP = "證券-操盤"


def transfer(conn, amount, to, day, desc="存錢"):
    ingest.run(conn, {"records": [{"type": "transfer", "amount": amount, "date": day, "from": "生活帳戶", "to": to,
                                   "purpose": "investing", "desc": desc}]})


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name=INV, kind="securities", asset_class="invest")
        refdata.add_account(w, name=OP, kind="securities", asset_class="trading")
    yield c
    c.close()


def test_shadow_pot_math():
    """一年前放 100,000、年 20% → 影子池 120,000；實際剩 110,000 就輸 10,000。"""
    c = bench.compare([("2036-10-10", 100000)], 110000, 0.2, today=date(2037, 10, 10))
    assert c["shadow"] == 120000 and c["diff"] == -10000 and not c["beat"]
    assert c["actual_gain"] == 10000 and c["shadow_gain"] == 20000
    # 中途拿回 50,000：池子少 50,000 之後再長
    c = bench.compare([("2036-10-10", 100000), ("2037-04-10", -50000)], 70000, 0.2, today=date(2037, 10, 10))
    assert c["taken"] == 50000 and abs(c["shadow"] - (100000 * 1.2 - 50000 * 1.2 ** (183 / 365))) < 2


def test_blueprint_and_red_line():
    bp = bench.blueprint(100000, 5000, 0.2, 0.1)
    assert [b["years"] for b in bp] == [1, 3, 5]
    assert 185000 < bp[0]["bench"] < 186000                 # 120,000＋每月 5,000 一年滾到約 65,300
    assert bp[0]["low"] < bp[0]["bench"] and bp[2]["put_in"] == 100000 + 5000 * 60
    line = bench.red_line(100000, 0, 0.2, start=date(2037, 10, 10), months=12)
    assert line[0]["value"] == 100000 and abs(line[-1]["value"] - 120000) < 2


def test_layer_value_cash_known_only_after_a_transfer(conn):
    with db.write(conn) as w:
        trades.add(w, date="2036-06-01", action="buy", symbol="TEST1", shares=10, price=500, account=INV)
    v = piggy.layer_value(conn, "invest")
    assert v["holding_cost"] == 5000 and not v["cash_known"] and v["value"] == 5000   # 以前的交易：現金不知道，只算持股
    transfer(conn, 20000, INV, "2037-10-01")
    with db.write(conn) as w:
        trades.add(w, date="2037-10-02", action="buy", symbol="TEST2", shares=100, price=150, account=INV)
    v = piggy.layer_value(conn, "invest")
    # 現金＝轉進來 20,000 − 10/2 買 TEST2 的 15,000；持股成本＝TEST1 的 5,000＋TEST2 的 15,000
    assert v["cash_known"] and v["cash"] == 5000 and v["value"] == 25000
    with db.write(conn) as w:
        balances.set_snapshot(w, account=INV, balance=6000, as_of="2037-10-05")
        trades.add(w, date="2037-10-06", action="sell", symbol="TEST2", shares=100, price=160, account=INV)
    v = piggy.layer_value(conn, "invest")
    assert v["cash"] == 6000 + 16000 and v["holding_cost"] == 5000


def test_layer_compare_counts_old_holdings_as_put_in(conn):
    """記帳開始之前就持有的股票在起算那天算成「投入」——不然會被當成賺來的（投入只算轉進來的錢，會顯示大贏）。"""
    with db.write(conn) as w:
        trades.add(w, date="2036-06-01", action="buy", symbol="TEST1", shares=10, price=500, account=INV)
    transfer(conn, 20000, INV, "2037-10-01")
    lay = piggy.layer_view(conn, "invest", "2037-10")
    c = lay["bench"]["compare"]
    assert c["put_in"] == 25000 and c["remaining"] == 25000 and -150 < c["diff"] < 0   # 9 天、年 20%：基準多長 113
    assert lay["bench"]["series"][-1]["put_in"] == 25000 and lay["bench"]["series"][-1]["actual"] == 25000
    assert lay["bench"]["future"][0]["date"] == "2037-10-10"                 # 往後的期望從今天出發


def test_symbol_compare_hide_and_quotes(conn):
    with db.write(conn) as w:
        settings.put(w, "bench.symbol", "TEST0")                             # 拿來比的基準代號（預設空白）
        trades.add(w, date="2036-10-10", action="buy", symbol="TEST1", name="範例股", shares=100, price=1000, account=INV)
        trades.add(w, date="2036-10-10", action="buy", symbol="TEST0", shares=100, price=100, account=INV)
        trades.add(w, date="2036-10-10", action="buy", symbol="TEST2", shares=100, price=100, account=INV)
        trades.add(w, date="2037-04-10", action="sell", symbol="TEST2", shares=100, price=150, account=INV)
    c = piggy.symbol_compare(conn, "invest")
    keys = [i["key"] for i in c["items"]]
    assert "TEST0" not in keys and keys == ["TEST1", "TEST2"]   # 基準自己不比；還在場上的排前面
    held = c["items"][0]
    assert held["remaining"] == 100000 and held["shadow"] == 120000 and not held["beat"]   # 用成本：沒漲就輸基準
    sold = c["items"][1]
    # TEST2：放 10,000、半年後拿回 15,000 → 實際賺 5,000；換成基準半年只長到約 10,950，拿 15,000 走還欠 → 贏
    assert sold["actual_gain"] == 5000 and sold["beat"] and sold["remaining"] == 0
    with db.write(conn) as w:
        quotes.push(w, {"TEST1": 1500}, as_of="2037-10-10 13:30")
    priced = piggy.symbol_compare(conn, "invest", use_quotes=True)["items"][0]
    assert priced["remaining"] == 150000 and priced["beat"] and priced["priced_at"] == "2037-10-10 13:30"
    with db.write(conn) as w:
        piggy.hide(w, "invest", "TEST1")
    c = piggy.symbol_compare(conn, "invest")
    assert [i["key"] for i in c["items"]] == ["TEST2"] and c["hidden"] == 1
    assert piggy.symbol_compare(conn, "invest", show_hidden=True)["total"] == 2


def test_quotes_latest_and_parse(conn):
    with db.write(conn) as w:
        quotes.push(w, quotes.parse_pairs(["TEST1=1,050", "TEST2:172.5"]), as_of="2037-10-09")
        quotes.push(w, {"TEST1": "1060"}, as_of="2037-10-10 09:00")
    got = quotes.latest(conn, ["TEST1", "TEST2", "TEST9"])
    assert got["TEST1"]["price"] == 1060 and got["TEST2"]["price"] == 172.5 and "TEST9" not in got
    with pytest.raises(Exception):
        quotes.parse_pairs(["TEST1"])


def test_cash_leftover_and_mark(conn):
    with db.write(conn) as w:
        balances.set_snapshot(w, account="生活帳戶", balance=50000, as_of="2037-10-01")
        plans.set_plan(w, "2037-10", income_expected=45000, buckets={"fixed": 8000})
    v = piggy.cash_view(conn)
    assert v["balance"] == 50000 and v["reserved"] >= 8000
    assert v["leftover"] == v["balance"] - v["reserved"] - v["living_left"] - v["card_keep"]
    with db.write(conn) as w:
        first = piggy.mark(w)
    assert first["status"] == "created" and first["amount"] == v["leftover"]
    with db.write(conn) as w:
        again = piggy.mark(w)
        assert again["status"] == "same" and w.changes == 0      # 沒變就不寫，不會每天多一個批次


def test_transfer_into_trading_account_counts_for_trading_bucket(conn):
    with db.write(conn) as w:
        plans.set_bucket(w, "trading", active=True)
    transfer(conn, 3000, OP, "2037-10-05", "停損額")
    transfer(conn, 5000, INV, "2037-10-05")
    actual = stats.period_summary(conn, "2037-10")["allocation"]["actual"]
    assert actual["trading"] == 3000 and actual["invest"] == 5000


def test_supervision_items(conn):
    with db.write(conn) as w:
        b = books.create(w, account=OP, name="贏", max_loss=5000)
        trades.add(w, date="2037-10-05", action="buy", symbol="TEST2", shares=100, price=100, book="贏")
        trades.add(w, date="2037-10-06", action="sell", symbol="TEST2", shares=100, price=130, book="贏")
        books.close(w, b["id"])
        books.create(w, account=OP, name="沒規矩")
    keys = {i["key"]: i for i in allowance.check(conn)}
    assert "捐贈還有 300" in keys["tithe"]["text"] and keys["rules"]["level"] == "warn"
    with db.write(conn) as w:
        books.update(w, "沒規矩", max_loss=100)
        trades.add(w, date="2037-10-07", action="buy", symbol="TEST3", shares=10, price=100, book="沒規矩")
        trades.add(w, date="2037-10-07", action="sell", symbol="TEST3", shares=10, price=80, book="沒規矩")
        trades.add(w, date="2037-10-08", action="buy", symbol="TEST3", shares=10, price=80, book="沒規矩")
    keys = {i["key"]: i for i in allowance.check(conn)}
    assert keys["violation"]["level"] == "bad" and "stopped" in keys
    brief = piggy.brief(conn)
    assert brief["violations"] == 1 and [p["book"] for p in brief["pending"]] == ["贏"]


def test_tithe_rate_and_done_and_sim_violations_stay_out_of_overview(conn):
    """收倉後改捐贈比例、標成做完；模擬倉的違規不算進總覽。"""
    with db.write(conn) as w:
        b = books.create(w, account=OP, name="贏", max_loss=5000)
        trades.add(w, date="2037-10-05", action="buy", symbol="TEST2", shares=100, price=100, book="贏")
        trades.add(w, date="2037-10-06", action="sell", symbol="TEST2", shares=100, price=130, book="贏")
        books.close(w, b["id"])
        books.create(w, account=OP, name="試", kind="sim", max_loss=100)
        trades.add(w, date="2037-10-05", action="buy", symbol="TEST3", shares=10, price=50, book="試")
        trades.add(w, date="2037-10-06", action="sell", symbol="TEST3", shares=10, price=30, book="試")
        trades.add(w, date="2037-10-07", action="buy", symbol="TEST4", shares=10, price=30, book="試")   # 模擬倉違規
    with db.write(conn) as w:
        books.tithe(w, b["id"], rate="15%")
    assert books.book_view(conn, b["id"])["tithe_amount"] == 450
    with db.write(conn) as w:
        books.tithe(w, b["id"], done=True)
    assert books.book_view(conn, b["id"])["tithe_done_at"]
    assert piggy.brief(conn)["violations"] == 0


def test_piggy_end_to_end(conn):
    d = piggy.piggy(conn)
    assert [lv["key"] for lv in d["levels"]] == ["cash", "invest", "trading", "property"]
    assert {lv["key"]: lv for lv in d["levels"]}["trading"]["name"] == "資金操盤"
    with db.write(conn) as w:
        b = books.create(w, layer="trading", name="十月", capital=10000, max_loss=1000, per_trade_loss=300)
    b = books.book_view(conn, b["id"])
    assert b["account"] == OP and b["loss_left"] == 1000
    with db.write(conn) as w:
        t = trades.add(w, date="2037-10-05", action="buy", symbol="TEST2", shares=100, price=100, time="09:05",
                       stop_price=97, account=OP)
        assert t["book"] == "十月" and t["time"] == "09:05" and w.batch_id
        t = trades.add(w, date="2037-10-05", action="sell", symbol="TEST2", shares=100, price=95, day_trade=True, account=OP)
    assert "超過單次可虧損" in t["violation"]
    names = [a["name"] for a in books.layer_accounts(conn, "trading")]
    page = trades.all_trades(conn, accounts=names, limit=1, offset=0, with_realized=True)
    assert trades.count(conn, accounts=names) == 2 and page[0]["realized"] == -500
    one = piggy.piggy(conn, level="trading")["layer"]
    assert one["books"][0]["violations"] == 1 and one["month"]["loss"] == 500
    assert quotes.latest(conn, ["TEST2"]) == {}
    with db.write(conn) as w:
        books.close(w, b["id"])
    closed = books.book_view(conn, b["id"])
    assert closed["status"] == "closed" and closed["result"] == -500 and closed["tithe_amount"] == 0
    with db.write(conn) as w:
        settings.put(w, "bench.rate", settings.coerce(conn, "bench.rate", "15%"))
        settings.put(w, "trading.month_max_loss", settings.coerce(conn, "trading.month_max_loss", 2000))
    assert bench.config(conn)["rate"] == 0.15 and books.month_limit(conn) == 2000
    with pytest.raises(NotFound):
        with db.write(conn) as w:
            books.close(w, 999)
    with pytest.raises(InvalidInput):
        piggy.piggy(conn, level="nope")
    with db.write(conn) as w:
        books.archive(w, b["id"])
    assert books.get(conn, b["id"])["status"] == "archived"
    assert home.home(conn)["piggy"] is not None


def test_cash_view_splits_card_keep_into_bill_and_next(conn):
    from money_engine.services import cardcash
    with db.write(conn) as w:
        refdata.add_account(w, name="範例卡A", kind="credit_card", card_closing_day=31, card_due_day=16, pay_from_account_id=2)
    ingest.run(conn, {"records": [{"type": "expense", "amount": 900, "date": "2037-10-05", "account": "範例卡A", "desc": "晚餐"}]})
    v = piggy.cash_view(conn)
    assert v["card_items"] and v["card_items"][0]["account"] == "範例卡A"
    c = v["card_items"][0]
    assert c["bill"] + c["next"] == c["keep"] and v["card_keep"] == sum(x["keep"] for x in v["card_items"])
    assert c["next"] == 900
    assert cardcash.all_cards(conn)


def test_book_level_bench_compare_and_detail(conn):
    with db.write(conn) as w:
        old = books.create(w, account=OP, name="舊", start="2036-01-01", max_loss=5000)
        trades.add(w, date="2036-01-10", action="buy", symbol="TEST2", shares=100, price=100, book="舊")
        trades.add(w, date="2036-07-10", action="sell", symbol="TEST2", shares=100, price=105, book="舊")
        books.close(w, old["id"], day="2036-07-10")
        new = books.create(w, account=OP, name="新", max_loss=5000)
        trades.add(w, date="2037-10-01", action="buy", symbol="TEST3", shares=10, price=50, book="新")
    lay = piggy.layer_view(conn, "trading", "2037-10")
    marks = {b["name"]: b["bench"] for b in lay["books"]}
    assert marks["舊"]["until"] == "2036-07-10"                 # 收掉的倉比到收倉那天，不是今天
    assert marks["舊"]["actual_gain"] == 500 and not marks["舊"]["beat"]   # 半年 +5%，基準尺度約 +9.5%
    d = piggy.book_detail(conn, new["id"])
    assert d["book"]["name"] == "新" and [i["symbol"] for i in d["positions"]["items"]] == ["TEST3"]
    assert 0.0004 < d["daily"] < 0.0006                         # 年 20% ＝ 每天約 0.05%
    c = piggy.symbol_compare(conn, "trading", book_id=old["id"])
    # 2036 是閏年：1/10 到 7/10 是 182 天
    assert [i["key"] for i in c["items"]] == ["TEST2"] and c["items"][0]["days"] == 182 and c["items"][0]["until"] == "2036-07-10"
    assert piggy.positions_view(conn, "trading", old["id"])["items"] == []


def test_price_need_lists_what_is_held(conn):
    with db.write(conn) as w:
        trades.add(w, date="2037-10-01", action="buy", symbol="TEST1", name="範例股", shares=10, price=1000, account=INV)
        trades.add(w, date="2037-10-01", action="buy", symbol="TEST2", shares=5, price=100, account=OP)
        trades.add(w, date="2037-10-02", action="sell", symbol="TEST2", shares=5, price=100, account=OP)
        quotes.push(w, {"TEST1": 1050}, as_of="2037-10-09 13:30")
    items = quotes.needed(conn)
    assert [i["symbol"] for i in items] == ["TEST1"]
    assert items[0]["last_at"] == "2037-10-09 13:30" and items[0]["layers"] == ["invest"]
    b = books.resolve(conn, "2037 投資")
    assert piggy.book_detail(conn, b["id"])["book"]["name"] == "2037 投資"
    assert piggy.symbol_compare(conn, "invest", book_id=b["id"])["total"] == 1


def test_position_handed_over_between_books_at_cost(conn):
    """2036 投資買、2037 投資賣：用成本交接——舊倉拿回成本（沒賺沒賠）、新倉從成本算起（不然舊倉全賠、新倉白賺）。"""
    with db.write(conn) as w:
        trades.add(w, date="2036-03-03", action="buy", symbol="TEST1", shares=100, price=1000, account=INV)
        trades.add(w, date="2037-02-02", action="sell", symbol="TEST1", shares=100, price=1200, account=INV)
    old, new = books.resolve(conn, "2036 投資"), books.resolve(conn, "2037 投資")
    marks = piggy.books_bench(conn, [books.book_view(conn, old["id"]), books.book_view(conn, new["id"])], 0.2)
    assert marks[old["id"]]["actual_gain"] == 0 and marks[old["id"]]["put_in"] == 100000
    assert marks[new["id"]]["actual_gain"] == 20000 and marks[new["id"]]["put_in"] == 100000
    a = piggy.symbol_compare(conn, "invest", book_id=old["id"])["items"][0]
    b = piggy.symbol_compare(conn, "invest", book_id=new["id"])["items"][0]
    assert a["actual_gain"] == 0 and a["days"] == 336 and b["actual_gain"] == 20000 and b["days"] == 0
