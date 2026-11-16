"""對帳單的交易明細：買賣股利的淨收付、移動平均成本、已實現損益、重送不會變兩筆、手記一筆與看部位。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import ingest, refdata, trades


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name="證券戶", kind="securities")
    yield c
    c.close()


def test_buy_sell_dividend_and_average_cost(conn):
    """買兩次、賣一次、領一次股利：平均成本用移動平均，賣出時才算已實現損益。"""
    with db.write(conn) as w:
        a = trades.add(w, date="2037-09-01", action="buy", symbol="TEST1", name="範例ETF",
                       shares=1000, price=50, fee=20)
        trades.add(w, date="2037-09-20", action="buy", symbol="TEST1", shares=1000, price=60, fee=24)
        trades.add(w, date="2037-10-05", action="sell", symbol="TEST1", shares=1000, price=70, fee=28, tax=210)
        trades.add(w, date="2037-10-08", action="dividend", symbol="TEST1", gross=3000)
    assert a["net"] == -(50000 + 20)                      # 買：價金＋手續費都是付出去的
    p = trades.positions(conn)
    pos = p["items"][0]
    assert pos["shares"] == 1000                          # 買 2000 賣 1000
    # 平均成本＝(50,020＋60,024) / 2000 ＝ 55.022
    assert pos["avg_price"] == pytest.approx(55.02, abs=0.01)
    # 賣出：收到 70,000−28−210＝69,762，成本 round(55.022×1000)＝55,022 → 已實現 +14,740
    assert pos["realized"] == 69762 - 55022
    assert pos["dividends"] == 3000 and p["dividends"] == 3000
    assert pos["cost"] == 110044 - 55022                  # 剩下那 1000 股的成本
    assert "已實現" in trades.text_positions(p)


def test_statement_can_be_sent_again_without_doubling(conn):
    """同一張對帳單重送（同一張圖傳兩次）不會變兩筆：source＋source_ref 是冪等鍵。"""
    payload = {"source": "chat", "source_ref": "stmt-2037-09", "records": [
        {"type": "trade", "date": "2037-09-01", "action": "buy", "symbol": "TEST2", "name": "範例股",
         "shares": 100, "price": 1000, "fee": 10},
        {"type": "trade", "date": "2037-09-02", "action": "sell", "symbol": "TEST2", "shares": 100,
         "price": 1050, "fee": 10, "tax": 30},
    ]}
    first = ingest.run(conn, payload)
    assert [r["status"] for r in first["results"]] == ["created", "created"]
    again = ingest.run(conn, payload)
    assert [r["status"] for r in again["results"]] == ["replayed", "replayed"]
    assert len(trades.all_trades(conn, symbol="TEST2")) == 2
    pos = {i["key"]: i for i in trades.positions(conn)["items"]}["TEST2"]
    assert pos["shares"] == 0 and pos["realized"] == (105000 - 10 - 30) - (100000 + 10)


def test_rules(conn):
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            trades.add(w, date="2037-09-01", action="買進", symbol="TEST1", shares=100)
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            trades.add(w, date="2037-09-01", action="buy", symbol="TEST1", shares=-5)


def test_trade_positions_and_list(conn):
    with db.write(conn) as w:
        t = trades.add(w, date="2037-10-02", action="buy", symbol="TEST3", name="範例高股息",
                       shares=2000, price=20, fee=30)
    assert t["net"] == -(40000 + 30)
    p = trades.positions(conn)
    assert p["items"][0]["shares"] == 2000
    assert "平均成本" in trades.text_positions(p) and "TEST3" in trades.text_positions(p)
    assert len(trades.all_trades(conn, symbol="TEST3")) == 1
