"""對明細：最後核對日與檢查模式。股數對不上、放空、收掉的倉還有沒平倉、很久沒核對餘額都要被列出來；
檢查模式只讀；記下實際股數要能重跑、能撤銷。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import balances, books, reconcile, refdata, trades, transactions as tx

INV = "證券-投資"


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name=INV, kind="securities", asset_class="invest")
        refdata.add_account(w, name="現金包", kind="cash")
    yield c
    c.close()


def _trade(w, day, action, symbol, shares, price=10):
    trades.add(w, date=day, action=action, symbol=symbol, name=symbol, shares=shares, price=price, account=INV)


def _count(conn, **kw):
    with db.write(conn, command="reconcile count") as w:
        r = reconcile.count(w, account=INV, **kw)
        r["batch_id"] = w.batch_id
    return r


def test_short_is_flagged_and_count_mismatch_is_bad(conn):
    with db.write(conn) as w:
        _trade(w, "2037-01-05", "buy", "TEST2", 41)
        _trade(w, "2037-02-01", "buy", "TEST6", 100)
        _trade(w, "2037-03-01", "sell", "TEST6", 112)          # 賣比買多 12：帳上變放空
    r = reconcile.check(conn)
    keys = {i["key"]: i for i in r["items"]}
    assert keys["stock:short:TEST6"]["level"] == "warn"
    assert "2037-03-01" in keys["stock:short:TEST6"]["why"]
    assert keys["stock:uncounted:TEST2"]["level"] == "info"
    c = _count(conn, symbol="TEST2", shares=40, as_of="2037-06-01")
    assert c["diff"] == -1 and not c["match"]
    r = reconcile.check(conn)
    bad = [i for i in r["items"] if i["level"] == "bad"]
    assert [i["key"] for i in bad] == ["stock:mismatch:TEST2"] and r["bad"] == 1
    assert r["status"]["stocks"]["last_counted"] == "2037-06-01"


def test_matching_count_clears_the_item_and_is_idempotent(conn):
    with db.write(conn) as w:
        _trade(w, "2037-01-05", "buy", "TEST2", 41)
        reconcile.count(w, account=INV, symbol="TEST2", shares=41, as_of="2037-06-01")
    with db.write(conn) as w:
        reconcile.count(w, account=INV, symbol="TEST2", shares=41, as_of="2037-06-01")    # 重跑
    assert conn.execute("SELECT COUNT(*) FROM holdings_counts").fetchone()[0] == 1
    assert not [i for i in reconcile.check(conn)["items"] if i["key"].startswith("stock:")]
    item = reconcile.stock_status(conn)["items"][0]
    assert item["state"] == "ok" and not item["moved_since"]


def test_trade_after_count_is_noted_not_called_mismatch(conn):
    with db.write(conn) as w:
        _trade(w, "2037-01-05", "buy", "TEST2", 41)
        reconcile.count(w, account=INV, symbol="TEST2", shares=41, as_of="2037-02-01")
        _trade(w, "2037-03-01", "buy", "TEST2", 10)
    item = reconcile.stock_status(conn)["items"][0]
    assert item["state"] == "ok" and item["moved_since"] and item["derived"] == 51


def test_open_position_in_closed_book_is_flagged(conn):
    with db.write(conn) as w:
        b = books.create(w, account=INV, layer="invest", name="2037 投資", start="2037-01-01", end="2037-12-31")
        trades.add(w, date="2037-01-05", action="buy", symbol="TEST2", name="範例股", shares=41, price=100,
                   account=INV, book=b["id"])
        books.close(w, b["id"], day="2037-06-30")
    keys = [i["key"] for i in reconcile.check(conn)["items"]]
    assert "stock:closedbook:TEST2" in keys


def test_ledger_last_checked_and_stale_account(conn):
    with db.write(conn) as w:
        balances.set_snapshot(w, account="現金包", balance=1000, as_of="2031-01-01")
    with db.write(conn) as w:
        tx.create(w, kind="expense", amount=100, date="2031-02-01", account_id=refdata.resolve_account(conn, "現金包")["id"],
                  description="x")
    st = reconcile.ledger_status(conn)
    assert st["last_checked"] == "2031-01-01"
    r = reconcile.check(conn)
    assert any(i["key"] == "ledger:stale:現金包" for i in r["items"])


def test_check_writes_nothing_then_count_and_status(conn):
    with db.write(conn) as w:
        _trade(w, "2037-01-05", "buy", "TEST2", 41)
    before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    r = reconcile.check(conn)
    assert r["count"] >= 1 and r["status"]["stocks"]["items"][0]["state"] == "uncounted"
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before          # 檢查模式不寫任何東西
    c = _count(conn, symbol="TEST2", shares=41, as_of="2037-06-01")
    assert c["match"] is True and c["batch_id"]
    assert reconcile.status(conn)["stocks"]["last_counted"] == "2037-06-01"


def test_count_today_by_default_and_negative_shares_refused(conn):
    with db.write(conn) as w:
        _trade(w, "2037-01-05", "buy", "TEST2", 41)
    c = _count(conn, symbol="TEST2", shares=41)
    assert c["match"] is True and c["batch_id"]
    with pytest.raises(InvalidInput):
        _count(conn, symbol="TEST2", shares=-1)
