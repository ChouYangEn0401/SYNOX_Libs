"""負債：排程（零頭放第一期）、卡分期購買、自借款還款、帳單分期列對到排程、負債償還不重複算、應收、撤銷。"""
import pytest

from money_engine import db
from money_engine.errors import Conflict, InvalidInput
from money_engine.services import batches, debts, ingest, refdata, stats, transactions as tx


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name="範例卡二", kind="credit_card", aliases=["card2"], card_closing_day=25)
        refdata.add_account(w, name="預備金帳戶", kind="bank", purpose="應急用")
        refdata.add_account(w, name="借給小明", kind="receivable")
    return c


def test_schedule_amounts_put_remainder_first():
    assert debts.schedule_amounts(20000, 12, 1666) == [1674] + [1666] * 11
    assert debts.schedule_amounts(25000, 20, None) == [1250] * 20
    with pytest.raises(InvalidInput):
        debts.schedule_amounts(100, 0, None)


def test_installment_purchase_creates_financed_expense_and_schedule(conn):
    out = ingest.run(conn, {"records": [{"type": "installment_purchase", "name": "手機", "card": "card2", "total": 24000,
                                         "periods": 12, "date": "2037-10-08", "category": "大額採買"}]})
    r = out["results"][0]
    assert r["debt_id"] and r["per_period"] == 2000 and r["category"] == "大額採買"
    t = tx.get(conn, r["id"])
    assert t["financed"] and t["debt_id"] == r["debt_id"] and t["account"] == "範例卡二"
    s = stats.period_summary(conn, "2037-10")
    assert s["living"]["spent"] == 0 and s["allocation"]["financed"] == 24000
    assert s["allocation"]["actual"]["debt"] == 2000                    # 本期到期的第 1 期
    assert s["allocation"]["debts_due"][0]["name"] == "手機"


def test_self_loan_payment_is_transfer_counted_once(conn):
    with db.write(conn) as w:
        d = debts.create(w, name="線上課程", kind="self_loan", principal=25000, periods=20,
                         start_date="2036-11-01", account="生活帳戶", lender="預備金帳戶", paid_before="2037-10-05")["debt"]
    assert d["paid_count"] == 12 and d["remaining"] == 8 * 1250 and d["next"]["due_date"] == "2037-11-01"
    with db.write(conn) as w:
        r = debts.pay(w, d["id"], day="2037-10-10")
    p = r["payment"]
    assert p["kind"] == "transfer" and p["to_account"] == "預備金帳戶" and p["purpose"] == "debt" and r["paid_no"] == 13
    s = stats.period_summary(conn, "2037-10")
    assert s["allocation"]["actual"]["debt"] == 1250                     # 第 13 期 11/01 到期（屬 10/05–11/04 這一期）
    assert s["allocation"]["actual"]["saving"] == 0                       # 還款轉帳有連排程，不會再算一次
    s11 = stats.period_summary(conn, "2037-11")
    assert s11["allocation"]["actual"]["debt"] == 1250
    with pytest.raises(Conflict):
        with db.write(conn) as w:
            debts.pay(w, d["id"], no=13)


def test_statement_installment_line_links_schedule(conn):
    with db.write(conn) as w:
        debts.create(w, name="範例課程", kind="card_installment", principal=20000, periods=12, per_period=1666,
                     start_date="2036-10-15", account="card2", paid_before="2037-09-01")
    ingest.run(conn, {"records": [{"type": "card_statement", "card": "card2", "closing_date": "2037-09-25", "total": 1666,
                                   "lines": [{"date": "2037-09-15", "desc": "分期 範例課程 12/12期", "amount": 1666}]}]})
    from money_engine.services import cards
    st = cards.detail(conn, cards.statements(conn)[0]["id"])
    assert st["balanced"] and st["lines"][0]["debt"]["no"] == 12
    d = debts.all_debts(conn, include_done=True)[0]
    assert d["done"] and d["schedule"][-1]["statement_line_id"]


def test_card_installment_needs_card_and_validation(conn):
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            debts.create(w, name="x", kind="card_installment", principal=100, periods=2, start_date="2037-10-01",
                         account="現金")
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            debts.create(w, name="x", kind="loan", principal=100, periods=2, start_date="2037-10-01")


def test_receivables(conn):
    ingest.run(conn, {"records": [
        {"type": "transfer", "amount": 500, "date": "2037-10-01", "from": "現金", "to": "借給小明", "counterparty": "小明"},
        {"type": "transfer", "amount": 200, "date": "2037-10-08", "from": "借給小明", "to": "現金", "counterparty": "小明"}]})
    r = debts.receivables(conn)
    assert r == [{"counterparty": "小明", "account": "借給小明", "lent": 500, "collected": 200, "last_date": "2037-10-08",
                  "outstanding": 300}]


def test_debt_creation_can_be_reverted(conn):
    out = ingest.run(conn, {"records": [{"type": "installment_purchase", "name": "手機", "card": "card2", "total": 6000,
                                         "periods": 3, "date": "2037-10-08"}]})
    with db.write(conn) as w:
        batches.revert(w, out["batch_id"])
    assert debts.all_debts(conn) == [] and tx.query(conn)["total"] == 0
