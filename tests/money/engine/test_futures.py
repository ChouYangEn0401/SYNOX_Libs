"""期貨、選擇權（手記的交易）：口數 × 點數 × 每點、要給對的乘數、不跟基準比、倉的費用比率。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import books, piggy, refdata, trades

INV = "證券-投資"
OP = "證券-操盤"
FUT = "期貨試做"


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name=INV, kind="securities", asset_class="invest")
        refdata.add_account(w, name=OP, kind="securities", asset_class="trading")
        books.create(w, account=OP, name=FUT, kind="history", start="2037-06-01")
    yield c
    c.close()


def add_derivatives(w):
    """期貨 FUT1：買 2 口、賣 2 口（賺 50 點）；選擇權 OPT1：買 3 口、賣 2 口（剩 1 口在場上）。"""
    trades.add(w, date="2037-07-16", action="buy", symbol="FUT1", shares=2, price=20000, fee=40, tax=20,
               book=FUT, instrument="future", multiplier=10)
    trades.add(w, date="2037-07-16", action="sell", symbol="FUT1", shares=2, price=20050, fee=40, tax=20,
               book=FUT, instrument="future", multiplier=10)
    trades.add(w, date="2037-07-20", action="buy", symbol="OPT1", shares=3, price=10, fee=60, tax=6,
               book=FUT, instrument="option", multiplier=50)
    trades.add(w, date="2037-07-21", action="sell", symbol="OPT1", shares=2, price=20, fee=40, tax=4,
               book=FUT, instrument="option", multiplier=50)


def test_future_trade_is_lots_times_points_times_multiplier(conn):
    with db.write(conn) as w:
        t = trades.add(w, date="2037-07-16", action="buy", symbol="FUT1", shares=2, price=20000, fee=40, tax=20,
                       book=FUT, instrument="future", multiplier=10)
        trades.add(w, date="2037-07-17", action="sell", symbol="FUT1", shares=1, price=20100, fee=20, tax=10,
                   book=FUT, instrument="future", multiplier=10)
    assert t["gross"] == 400_000 and t["unit"] == "口"
    pos = trades.positions(conn, book_id=books.resolve(conn, FUT)["id"])["items"][0]
    assert pos["shares"] == 1 and pos["unit"] == "口" and pos["avg_price"] == pytest.approx(20003.0)   # 平均價換回點數（含費用）
    assert pos["realized"] == 1000 - 30 - 30                                                          # 100 點 × 10 − 兩邊的費用


def test_instrument_needs_the_right_multiplier(conn):
    with db.write(conn) as w:
        with pytest.raises(InvalidInput):
            trades.add(w, date="2037-07-16", action="buy", symbol="TEST1", shares=1, price=1000, multiplier=10)
        with pytest.raises(InvalidInput):
            trades.add(w, date="2037-07-16", action="buy", symbol="FUT1", shares=1, price=20000, instrument="future",
                       book=FUT)


def test_derivatives_are_not_compared_with_the_bench(conn):
    with db.write(conn) as w:
        add_derivatives(w)
    d = piggy.book_detail(conn, FUT)
    assert d["book"]["bench"] is None and d["book"]["derivatives"] == 4


def test_symbol_compare_leaves_out_derivatives(conn):
    with db.write(conn) as w:
        add_derivatives(w)
    c = piggy.symbol_compare(conn, "trading", book_id=books.resolve(conn, FUT)["id"])
    assert c["total"] == 0 and c["items"] == []


def test_book_costs_and_trade_rates(conn):
    with db.write(conn) as w:
        add_derivatives(w)
    c = books.book_view(conn, FUT)["costs"]["derivatives"]
    assert c["lots"] == 9 and c["fee"] == 180 and c["fee_per_lot"] == round(180 / 9, 2)
    with db.write(conn) as w:
        books.create(w, account=INV, name="第一階段", kind="history", start="2032-12-10", end="2036-02-20")
        books.create(w, account=INV, name="第二階段", start="2036-08-26", end="2037-08-14")
        t = trades.add(w, date="2036-09-01", action="sell", symbol="TEST1", shares=100, price=50, fee=7, tax=15,
                       book=books.covering(w.conn, books.resolve(w.conn, "第二階段")["account_id"], "2036-09-01")["id"])
    assert t["book"] == "第二階段" and t["fee_rate"] == round(7 / 5000, 6) and t["tax_rate"] == round(15 / 5000, 6)
    assert t["fee_discount"] == round(7 / 5000 / 0.001425, 3)
    acc = books.resolve(conn, "第一階段")["account_id"]
    assert books.covering(conn, acc, "2034-05-01")["name"] == "第一階段" and books.covering(conn, acc, "2036-05-01") is None
