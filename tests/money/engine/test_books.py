"""倉：自動挑倉、停止與違規三種、做空、模擬倉不算真的、收倉算捐贈、封存、待處理、撤銷。"""
import pytest

from money_engine import db
from money_engine.errors import Conflict, InvalidInput
from money_engine.services import batches, books, refdata, settings, trades

INV = "證券-投資"
OP = "證券-操盤"


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name=INV, kind="securities", asset_class="invest")
        refdata.add_account(w, name=OP, kind="securities", asset_class="trading")
    yield c
    c.close()


def buy(w, sym, shares, price, **kw):
    return trades.add(w, date=kw.pop("date", "2037-10-05"), action="buy", symbol=sym, shares=shares, price=price, **kw)


def sell(w, sym, shares, price, **kw):
    return trades.add(w, date=kw.pop("date", "2037-10-06"), action="sell", symbol=sym, shares=shares, price=price, **kw)


def test_invest_trades_go_into_the_year_book(conn):
    with db.write(conn) as w:
        t = buy(w, "TEST1", 10, 1000, account=INV)
        t2 = buy(w, "TEST1", 10, 1000, account=INV, date="2036-03-01")
    assert t["book"] == "2037 投資" and t2["book"] == "2036 投資"
    b = books.resolve(conn, "2037 投資")
    assert b["start_date"] == "2037-01-01" and b["end_date"] == "2037-12-31" and b["layer"] == "invest"
    # 去年的倉已經過了年底還沒收 → 待處理「該收倉了」
    assert [p["book"] for p in books.pending(conn) if p["kind"] == "close"] == ["2036 投資"]


def test_trading_book_stops_and_records_violations(conn):
    """可虧損打完＝停止；之後再進場記成違規，但離場永遠可以。"""
    with db.write(conn) as w:
        b = books.create(w, account=OP, name="10 月", capital=10000, max_loss=1000, per_trade_loss=600)
        buy(w, "TEST2", 100, 100, fee=0, account=OP)           # 沒指定倉 → 最新開著的那一倉
        r = sell(w, "TEST2", 100, 95, account=OP)              # 賠 500（單次可虧損 600 以內）
        assert r["book"] == "10 月" and r["violation"] is None
        buy(w, "TEST2", 100, 100, date="2037-10-07", account=OP)
        r = sell(w, "TEST2", 100, 93, date="2037-10-07", account=OP)   # 賠 700 > 單次 600 → 違規
        assert "超過單次可虧損" in r["violation"]
        s = books.book_view(conn, b["id"])
        assert s["loss_used"] == 1200 and s["stopped"] and s["loss_left"] == 0
        r = buy(w, "TEST3", 10, 50, date="2037-10-08", account=OP)     # 停止之後又進場 → 違規
        assert "停止之後又進場" in r["violation"]
        r = sell(w, "TEST3", 10, 50, date="2037-10-08", account=OP)    # 離場不算違規
        assert r["violation"] is None
    v = books.violations(conn, layer="trading")
    assert len(v) == 2
    assert books.book_view(conn, b["id"])["violations"] == 2


def test_month_limit_spans_books(conn):
    with db.write(conn) as w:
        settings.put(w, "trading.month_max_loss", 800)
        books.create(w, account=OP, name="A", max_loss=5000)
        buy(w, "TEST2", 100, 100, book="A")
        sell(w, "TEST2", 100, 90, book="A")                    # 這一期賠 1,000 ≥ 800
        books.create(w, account=OP, name="B", max_loss=5000)
        r = buy(w, "TEST4", 10, 30, book="B", date="2037-10-07")
    assert "本月" in r["violation"]
    m = books.month_status(conn)
    assert m["stopped"] and m["loss"] == 1000 and m["left"] == 0


def test_short_selling_and_sim_book_kept_apart(conn):
    with db.write(conn) as w:
        books.create(w, account=OP, name="真的", max_loss=5000)
        sim = books.create(w, account=OP, name="試試", kind="sim", max_loss=100)
        r1 = sell(w, "TEST5", 1000, 50, book="真的", date="2037-10-05")    # 先賣＝放空
        r2 = buy(w, "TEST5", 1000, 48, book="真的", date="2037-10-06")     # 回補賺 2,000
        buy(w, "TEST1", 10, 1000, book="試試")
        sell(w, "TEST1", 10, 900, book="試試")
    assert r1["violation"] is None and r2["violation"] is None
    p = trades.positions(conn, account=OP)
    keys = {i["key"]: i for i in p["items"]}
    assert keys["TEST5"]["shares"] == 0 and keys["TEST5"]["realized"] == 2000
    assert "TEST1" not in keys                                 # 模擬倉不算進帳戶的持股
    s = books.book_view(conn, sim["id"])
    assert s["result"] == -1000 and s["stopped"]
    with db.write(conn) as w:
        out = books.close(w, sim["id"])
    assert out["tithe_amount"] == 0                            # 模擬倉不用捐


def test_close_tithe_and_pending(conn):
    with db.write(conn) as w:
        b = books.create(w, account=OP, name="贏的", max_loss=5000)
        buy(w, "TEST2", 100, 100, account=OP)
        sell(w, "TEST2", 100, 120, account=OP)                 # +2,000
        closed = books.close(w, b["id"])
    assert closed["result"] == 2000 and closed["tithe_amount"] == 200    # 預設 10%
    todo = [p for p in books.pending(conn) if p["kind"] == "tithe"]
    assert todo and todo[0]["amount"] == 200
    with db.write(conn) as w:
        books.tithe(w, b["id"], rate="15%")
        assert books.get(conn, b["id"])["tithe_amount"] == 300
        books.tithe(w, b["id"], done=True)
    assert not [p for p in books.pending(conn) if p["kind"] == "tithe"]
    with pytest.raises(InvalidInput):                          # 收倉之後不能再記進去
        with db.write(conn) as w:
            buy(w, "TEST2", 1, 100, book="贏的")
    with db.write(conn) as w:
        books.reopen(w, b["id"])
    assert books.get(conn, b["id"])["tithe_done_at"] is not None     # 給過的不會因為重新打開就要再給一次
    with db.write(conn) as w:
        books.close(w, b["id"])                                        # 沒多賺：還是做完的
    assert books.get(conn, b["id"])["tithe_done_at"] is not None
    with db.write(conn) as w:
        books.reopen(w, b["id"])
        buy(w, "TEST2", 100, 100, book="贏的", date="2037-10-08")
        sell(w, "TEST2", 100, 110, book="贏的", date="2037-10-09")    # 又賺 1,000
        books.close(w, b["id"])
    again = books.get(conn, b["id"])
    assert again["tithe_amount"] == 450 and again["tithe_done_at"] is None   # 多出來的要補：15% × 3,000


def test_archive_is_soft_and_revert_works(conn):
    with db.write(conn) as w:
        b = books.create(w, account=OP, name="不要了", max_loss=1000)
        buy(w, "TEST2", 10, 100, account=OP)
    with db.write(conn) as w:
        books.archive(w, b["id"])
    assert books.get(conn, b["id"])["status"] == "archived"
    assert not [x for x in books.list_books(conn, layer="trading")]
    assert [x["name"] for x in books.list_books(conn, layer="trading", include_archived=True)] == ["不要了"]
    with db.write(conn) as w:
        t = buy(w, "TEST1", 1, 1000, account=INV)
        batch = w.batch_id
    with db.write(conn) as w:
        batches.revert(w, batch)
    assert not trades.all_trades(conn, symbol="TEST1")
    assert t["book"] == "2037 投資"


def test_rules_missing_flag_and_names(conn):
    with db.write(conn) as w:
        b = books.create(w, account=OP)
    v = books.book_view(conn, b["id"])
    assert v["name"] == "2037-10 操盤" and v["rules_missing"]
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            books.create(w, account=OP, name="2037-10 操盤")           # 自己取同名：擋
    with db.write(conn) as w:
        books.close(w, b["id"])
        t = buy(w, "TEST2", 1, 100, account=OP)                          # 收掉之後沒給倉記一筆：自動開，名字加編號（不要擋住記帳）
    assert t["book"] == "2037-10 操盤 (2)"


def test_same_symbol_in_two_accounts_is_not_merged(conn):
    """投資帳戶的 TEST1 跟操盤帳戶的 TEST1 是兩個部位（不能混成一個，操盤那一倉才不會多算）。"""
    with db.write(conn) as w:
        buy(w, "TEST1", 1000, 500, account=INV, date="2037-10-01")
        b = books.create(w, account=OP, name="T", max_loss=5000)
        buy(w, "TEST1", 1000, 1000, book="T", date="2037-10-02")
        sell(w, "TEST1", 1000, 1000, book="T", date="2037-10-03")
    every = {x["name"]: x for x in books.list_books(conn)}
    assert every["T"]["result"] == 0 and every["T"]["in_market"] == 0
    assert every["2037 投資"]["in_market"] == 500000
    assert books.book_view(conn, b["id"])["result"] == 0


def test_backdated_trade_and_delete_recheck_later_flags(conn):
    with db.write(conn) as w:
        books.create(w, account=OP, name="B", max_loss=500)
        buy(w, "TEST2", 100, 100, book="B", date="2037-10-01")
        later = buy(w, "TEST4", 10, 30, book="B", date="2037-10-03")
    assert later["violation"] is None
    with db.write(conn) as w:
        loss = sell(w, "TEST2", 100, 91, book="B", date="2037-10-02")      # 補記比較早的一筆：賠 900 → 10/03 那筆變違規
    assert trades.all_trades(conn, symbol="TEST4")[0]["violation"].startswith("倉「B」")
    with db.write(conn) as w:
        trades.delete(w, loss["id"])                                        # 刪掉那一筆：10/03 不再違規
    assert trades.all_trades(conn, symbol="TEST4")[0]["violation"] is None


def test_same_day_order_follows_time(conn):
    """回補 13:00 先記、放空 09:05 後記：照時間排，放空在前、回補在後（不能照記的順序把回補當成進場）。"""
    with db.write(conn) as w:
        books.create(w, account=OP, name="D", max_loss=5000)
        cover = buy(w, "TEST5", 100, 48, book="D", date="2037-10-05", time="13:00")
        short = sell(w, "TEST5", 100, 50, book="D", date="2037-10-05", time="09:05")
    page = {t["id"]: t for t in trades.all_trades(conn, book_id=books.resolve(conn, "D")["id"], with_realized=True)}
    assert page[short["id"]]["realized"] == 0 and page[cover["id"]]["realized"] == 200
    assert trades.positions(conn, account=OP)["items"][0]["shares"] == 0


def test_stop_is_sticky_and_flips_count_as_entries(conn):
    with db.write(conn) as w:
        books.create(w, account=OP, name="S", max_loss=500)
        buy(w, "TEST2", 100, 100, book="S", date="2037-10-01")
        sell(w, "TEST2", 100, 91, book="S", date="2037-10-02")             # −900：停止
        buy(w, "TEST3", 100, 50, book="S", date="2037-10-03")              # 違規（停止後進場）
        sell(w, "TEST3", 100, 70, book="S", date="2037-10-04")             # +2,000 離場：不違規，但停止不會解除
        again = buy(w, "TEST4", 10, 30, book="S", date="2037-10-05")
    v = books.book_view(conn, books.resolve(conn, "S")["id"])
    assert v["stopped"] and v["result"] > 0 and v["loss_left"] == 0
    assert again["violation"] and "停止之後又進場" in again["violation"]
    with db.write(conn) as w:
        flip = sell(w, "TEST4", 30, 30, book="S", date="2037-10-06")       # 持有 10 股卻賣 30：翻成放空 20＝進場
    assert flip["violation"] and "停止之後又進場" in flip["violation"]


def test_closed_book_keeps_its_frozen_result(conn):
    with db.write(conn) as w:
        a = books.create(w, account=OP, name="A", max_loss=5000)
        buy(w, "TEST2", 10, 100, book="A", date="2037-10-02")
        sell(w, "TEST2", 10, 200, book="A", date="2037-10-03")
        books.close(w, a["id"])
        books.create(w, account=OP, name="B2", max_loss=5000)
        buy(w, "TEST2", 10, 100, book="B2", date="2037-10-01")             # 補記一筆更早的同一檔：平均成本變了
    v = books.book_view(conn, a["id"])
    assert v["result"] == 1000 and v["tithe_amount"] == 100 and "result_now" in v


def test_revert_batch_whose_book_got_later_trades_is_a_conflict(conn):
    with db.write(conn) as w:
        buy(w, "TEST2", 10, 100, account=OP, date="2037-10-01")            # 自動開「2037-10 操盤」
        first = w.batch_id
    with db.write(conn) as w:
        buy(w, "TEST3", 10, 50, account=OP, date="2037-10-02")             # 記進同一倉
    with pytest.raises(Conflict):
        with db.write(conn) as w:
            batches.revert(w, first)
    assert books.resolve(conn, "2037-10 操盤")                              # 整批沒動


def test_sim_tithe_rate_stays_zero_and_zero_net_is_allowed(conn):
    with db.write(conn) as w:
        s = books.create(w, account=OP, name="sim", kind="sim")
        buy(w, "TEST2", 10, 100, book="sim")
        sell(w, "TEST2", 10, 200, book="sim")
        books.close(w, s["id"])
        books.update(w, s["id"], tithe_rate="20%")
        t = trades.add(w, date="2037-10-06", action="other", net=0, account=OP, note="零元的調整")
    assert books.get(conn, s["id"])["tithe_amount"] == 0 and t["net"] == 0


def test_tithe_rounds_half_up():
    """1,165 × 10% ＝ 116.5 → 117（Python 的 round 會給 116）。"""
    assert books._tithe_of(1165, 0.1) == 117 and books._tithe_of(2000, 0.15) == 300 and books._tithe_of(-50, 0.1) == 0


def test_move_trades_into_another_book_and_reclose(conn):
    """把某一天之後的交易改記到另一倉：一段時間的交易搬過去，收掉的倉重新結算，可以撤銷。"""
    with db.write(conn) as w:
        old = books.create(w, account=INV, name="第一階段", kind="history", start="2036-01-01")
        trades.add(w, date="2036-03-01", action="buy", symbol="TEST1", shares=10, price=1000, book="第一階段")
        trades.add(w, date="2036-04-01", action="sell", symbol="TEST1", shares=10, price=1100, book="第一階段")
        trades.add(w, date="2036-09-01", action="buy", symbol="TEST2", shares=10, price=100, book="第一階段", allow_closed=True)
        trades.add(w, date="2036-09-02", action="sell", symbol="TEST2", shares=10, price=150, book="第一階段")
        books.close(w, old["id"], day="2036-09-02")
        books.create(w, account=INV, name="長期", start="2036-08-26")
    assert books.get(conn, old["id"])["result"] == 1500
    with db.write(conn) as w:
        r = books.move_trades(w, "長期", since="2036-08-26")
        batch = w.batch_id
    assert r["moved"] == 2 and r["from"] == ["第一階段"]
    assert books.get(conn, old["id"])["result"] == 1000                     # 收掉的倉照剩下的重新結算
    assert books.book_view(conn, "長期")["result"] == 500
    with db.write(conn) as w:
        batches.revert(w, batch)
    assert books.get(conn, old["id"])["result"] == 1500 and books.book_view(conn, "長期")["trades"] == 0
