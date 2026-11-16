"""信用卡對帳：對上、漏記、金額不同、分期、手續費、退款、下期入帳、記錯帳戶、等式、重送、手動配對學商家、補記、繳費。"""
import pytest

from money_engine import db
from money_engine.errors import Conflict, InvalidInput
from money_engine.services import batches, cards, ingest, refdata, transactions as tx


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name="範例卡二", kind="credit_card", aliases=["card2"], card_closing_day=25,
                            card_due_day=10, card_limit=50000,
                            pay_from_account_id=refdata.resolve_account(c, "生活帳戶")["id"])
    ingest.run(c, {"records": [
        {"type": "expense", "amount": 120, "date": "2037-08-28", "desc": "水餃 10 顆", "account": "card2"},
        {"type": "expense", "amount": 150, "date": "2037-09-02", "desc": "範例速食店", "account": "card2"},
        {"type": "expense", "amount": 280, "date": "2037-09-10", "desc": "小籠包", "account": "card2"},      # 帳單 250
        {"type": "expense", "amount": 70, "date": "2037-09-12", "desc": "飲料", "account": "card2"},         # 帳單寫店名
        {"type": "expense", "amount": 330, "date": "2037-09-14", "desc": "範例網購 收納盒", "account": "card2"},
        {"type": "expense", "amount": 160, "date": "2037-09-19", "desc": "範例雜貨店", "account": "現金"},   # 其實刷卡
        {"type": "expense", "amount": 170, "date": "2037-09-24", "desc": "鹹酥雞", "account": "card2"},     # 下期入帳
    ]})
    return c


STATEMENT = {"type": "card_statement", "card": "card2", "closing_date": "2037-09-25", "due_date": "2037-10-10",
             "total": 2950, "lines": [
                 {"date": "2037-08-28", "desc": "範例水餃店", "amount": 120},
                 {"date": "2037-08-30", "desc": "小黃 *TRIP", "amount": 180},
                 {"date": "2037-09-02", "desc": "範例速食店", "amount": 150},
                 {"date": "2037-09-10", "desc": "範例小籠包店", "amount": 250},
                 {"date": "2037-09-12", "desc": "巷口超商", "amount": 70},
                 {"date": "2037-09-14", "desc": "SamplePay-範例網購", "amount": 330},
                 {"date": "2037-09-15", "desc": "分期 範例課程 11/12期", "amount": 1600},
                 {"date": "2037-09-18", "desc": "範例網購退款", "amount": -100},
                 {"date": "2037-09-19", "desc": "範例雜貨店", "amount": 160},
                 {"date": "2037-09-25", "desc": "年費", "amount": 190},
             ]}


def _ingest_statement(conn, **over):
    rec = dict(STATEMENT, **over)
    out = ingest.run(conn, {"source_ref": "tg:1:stmt", "records": [rec]})
    return out, cards.detail(conn, out["results"][0]["statement_id"])


def test_infer_types():
    assert cards.infer_type("分期 範例課程 11/12期", 1600) == ("installment", 11, 12)
    assert cards.infer_type("年費", 190)[0] == "fee"
    assert cards.infer_type("範例網購退款", -100)[0] == "refund"
    assert cards.infer_type("自動扣繳 感謝您", -5000)[0] == "payment"
    assert cards.merchant_key("SamplePay-範例網購") == "範例網購"   # 支付前綴要剝掉


def test_reconcile_statuses_and_equation(conn):
    out, st = _ingest_statement(conn)
    by_desc = {ln["desc"]: ln for ln in st["lines"]}
    assert by_desc["範例水餃店"]["status"] == "matched"                       # 金額日期相同，名字不像也算
    assert by_desc["巷口超商"]["status"] == "matched"
    assert by_desc["SamplePay-範例網購"]["status"] == "matched"
    assert by_desc["範例小籠包店"]["status"] == "diff" and by_desc["範例小籠包店"]["ledger"]["amount"] == 280
    assert by_desc["小黃 *TRIP"]["status"] == "missing"
    assert by_desc["範例網購退款"]["status"] == "missing" and by_desc["範例網購退款"]["type"] == "refund"
    assert by_desc["分期 範例課程 11/12期"]["status"] == "installment"
    assert by_desc["年費"]["status"] == "fee"
    sugg = by_desc["範例雜貨店"]["suggestion"]
    assert by_desc["範例雜貨店"]["status"] == "missing" and sugg and sugg["action"] == "move"
    assert [t["desc"] for t in st["ledger_only"]] == ["鹹酥雞"] and "下期" in st["ledger_only"][0]["reason"]
    assert st["sums"]["lines_total"] == 2950 and st["gap"] == 0 and not st["balanced"]
    warnings = " ".join(out["warnings"])
    assert "找不到" in warnings and "金額跟記帳不同" in warnings and "年費" in warnings


def test_resend_same_statement_is_replayed(conn):
    _ingest_statement(conn)
    out, _ = _ingest_statement(conn)
    assert out["results"][0]["status"] == "replayed" and out["batch_id"] is None


def test_fixes_balance_the_statement_and_pay(conn):
    _, st = _ingest_statement(conn)
    ids = {ln["desc"]: ln["id"] for ln in st["lines"]}
    with db.write(conn) as w:
        cards.fix(w, ids["小黃 *TRIP"], "create")
        cards.fix(w, ids["範例網購退款"], "create")
        cards.fix(w, ids["範例小籠包店"], "use_ledger")
        cards.fix(w, ids["範例雜貨店"], "move")
        cards.fix(w, ids["年費"], "create")
        st = cards.fix(w, ids["分期 範例課程 11/12期"], "make_debt")
    assert st["balanced"] and st["problems"] == 0 and st["status"] == "balanced"
    inst = next(ln for ln in st["lines"] if ln["type"] == "installment")
    assert inst["debt"] == {"name": "範例課程", "no": 11, "periods": 12}
    taxi = next(ln for ln in st["lines"] if ln["desc"] == "小黃 *TRIP")["ledger"]
    assert taxi["category"] == "計程車" and taxi["account"] == "範例卡二" and taxi["source"] == "statement"
    fee = next(ln for ln in st["lines"] if ln["desc"] == "年費")["ledger"]
    assert fee["category"] == "雜支"
    refund = next(ln for ln in st["lines"] if ln["desc"] == "範例網購退款")["ledger"]
    assert refund["amount"] == -100
    assert next(ln for ln in st["lines"] if ln["desc"] == "範例雜貨店")["ledger"]["account"] == "範例卡二"
    assert tx.query(conn, text="小籠包")["items"][0]["amount"] == 250
    with db.write(conn) as w:
        r = cards.pay(w, st["id"])
    assert r["payment"]["purpose"] == "card_payment" and r["payment"]["amount"] == 2950
    assert r["statement"]["paid"] and r["statement"]["status"] == "paid"
    with pytest.raises(Conflict):
        with db.write(conn) as w:
            cards.pay(w, st["id"])


def test_manual_match_learns_alias(conn):
    _, st = _ingest_statement(conn)
    taxi_line = next(ln for ln in st["lines"] if ln["desc"] == "小黃 *TRIP")
    t = ingest.run(conn, {"records": [{"type": "expense", "amount": 180, "date": "2037-08-20", "desc": "叫車回家",
                                       "account": "card2", "category": "計程車"}]})["results"][0]
    with db.write(conn) as w:
        st = cards.match(w, taxi_line["id"], t["id"])
    assert next(ln for ln in st["lines"] if ln["id"] == taxi_line["id"])["status"] == "matched"
    alias = conn.execute("SELECT * FROM merchant_aliases").fetchone()
    assert alias["pattern"] == "小黃trip" and alias["merchant"] == "叫車回家"


def test_ignore_and_validation(conn):
    _, st = _ingest_statement(conn)
    fee_id = next(ln["id"] for ln in st["lines"] if ln["desc"] == "年費")
    with db.write(conn) as w:
        st = cards.fix(w, fee_id, "ignore")
    assert next(ln for ln in st["lines"] if ln["id"] == fee_id)["status"] == "ignored"
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            cards.fix(w, fee_id, "delete")
    with pytest.raises(InvalidInput):
        ingest.run(conn, {"records": [dict(STATEMENT, card="現金")]})


def test_card_status_alert_and_unbilled(conn):
    _ingest_statement(conn)
    ingest.run(conn, {"records": [{"type": "expense", "amount": 300, "date": "2037-10-08", "desc": "晚餐", "account": "card2"}]})
    s = next(c for c in cards.all_cards(conn) if c["account"] == "範例卡二")
    assert s["alert"] == "範例卡二 卡費 2,950 今天到期"
    assert s["unbilled"] == 300 + 170 and s["next_closing"] == "2037-10-25"
    assert s["usage"] == round((2950 + 470) / 50000, 3)


def test_statement_ingest_can_be_reverted(conn):
    out, _ = _ingest_statement(conn)
    with db.write(conn) as w:
        batches.revert(w, out["batch_id"])
    assert cards.statements(conn) == []


def test_an_advance_on_the_card_still_matches_its_statement_line(conn):
    """代墊（借出轉帳）刷的卡：帳單上還是有這一列，要對得上，不能因為不是「花錢」就變成漏記。"""
    from money_engine.services import editing
    tid = next(r["id"] for r in conn.execute("SELECT id FROM transactions WHERE description = ?", ("範例速食店",)))
    with db.write(conn) as w:
        editing.edit(w, tid, advance=True)
    assert conn.execute("SELECT kind, purpose FROM transactions WHERE id = ?", (tid,)).fetchone()[:] == ("transfer", "lend")
    out, st = _ingest_statement(conn)
    line = next(ln for ln in st["lines"] if ln["desc"] == "範例速食店")
    assert line["status"] == "matched" and line["ledger"]["id"] == tid
