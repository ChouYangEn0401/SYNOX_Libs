"""卡費要準備多少。今天 2037-10-10；範例卡二 25 號結帳、隔月 13 號繳 → 這期＝8/26–9/25、10/13 繳；下期 10/25 結帳。"""
from datetime import date

import pytest

from money_engine import db
from money_engine.services import balances, cardcash, cards, health, home, ingest, refdata


def test_dates():
    assert cardcash.closing_before(25, date(2037, 10, 10)) == date(2037, 9, 25)
    assert cardcash.closing_before(10, date(2037, 10, 10)) == date(2037, 9, 10)       # 結帳日當天還沒結
    assert cardcash.closing_before(31, date(2037, 3, 1)) == date(2037, 2, 28)
    assert cardcash.closing_on_or_after(10, date(2037, 10, 10)) == date(2037, 10, 10)
    assert cardcash.due_after(date(2037, 1, 31), 15) == date(2037, 2, 16)   # 2/15 是週日 → 順延一天
    assert cardcash.due_after(date(2037, 1, 10), 25) == date(2037, 1, 26)            # 同一個月就繳（1/25 週日 → 1/26）


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name="預備金帳戶", kind="bank", floor_amount=50000, sort=-1)   # 存錢用的：不當扣款帳戶
        refdata.add_account(w, name="範例卡二", kind="credit_card", card_closing_day=25, card_due_day=13)
    rows = [("2037-08-20", 900), ("2037-09-01", 1000), ("2037-09-20", 500), ("2037-09-27", 300), ("2037-10-08", 200)]
    ingest.run(c, {"records": [{"type": "expense", "date": d, "amount": a, "desc": "刷卡", "account": "範例卡二"}
                               for d, a in rows]})
    return c


def one(c):
    return next(x for x in cardcash.all_cards(c) if x["account"] == "範例卡二")


def test_estimate_without_statement(conn):
    c = one(conn)
    assert c["bill"] == {"amount": 1500, "paid": 0, "left": 1500, "closing": "2037-09-25", "due": "2037-10-13",
                         "days_to_due": 3, "source": "estimate"}
    assert c["next"] == {"amount": 500, "closing": "2037-10-25", "due": "2037-11-13"}
    assert c["keep"] == 2000 and c["bank"] == "生活帳戶" and c["bank_balance"] is None      # 沒核對過就不比
    assert c["level"] == "warn" and c["why"] == "卡費 1,500 3 天後要繳"
    assert c["text"].startswith("範例卡二：本期帳單 1,500（估的） 10/13 繳；下一期帳單已刷 500，10/25 結帳；生活帳戶要預留信用卡帳款 2,000")


def test_bank_not_enough_then_paid(conn):
    with db.write(conn) as w:
        balances.set_snapshot(w, account="生活帳戶", balance=1000, as_of="2037-10-09")
    c = one(conn)
    assert c["bank_balance"] == 1000 and c["level"] == "bad" and "不夠繳這期 1,500" in c["why"]
    assert any(i["key"] == "card" and i["level"] == "bad" for i in health.checks(conn))
    ingest.run(conn, {"records": [{"type": "transfer", "date": "2037-10-10", "amount": 1500, "from": "生活帳戶",
                                   "to": "範例卡二", "desc": "繳卡費"}]})
    c = one(conn)
    assert c["bill"]["paid"] == 1500 and c["bill"]["left"] == 0 and c["keep"] == 500
    assert c["bank_balance"] == -500 and c["level"] == "warn" and "要留 500" in c["why"]
    assert "（繳好了）" in c["text"]


def test_statement_wins_over_estimate(conn):
    with db.write(conn) as w:
        cards.save_statement(w, card="範例卡二", closing_date="2037-09-25", due_date="2037-10-14", total=1600, lines=[])
    c = one(conn)
    assert c["bill"]["amount"] == 1600 and c["bill"]["source"] == "statement" and c["bill"]["due"] == "2037-10-14"
    assert "（估的）" not in c["text"]


def test_card_without_days(conn):
    """沒設日子的卡也要列出來，並直接說要去設什麼。"""
    blank = next(x for x in cardcash.all_cards(conn) if x["account"] == "範例 Demo")
    assert not blank["ready"] and blank["level"] == "good" and blank["spent"] == 0   # 沒用過：列出來但不算警告
    assert "結帳日、繳款日和從哪個帳戶扣" in blank["text"]
    ingest.run(conn, {"records": [{"type": "expense", "date": "2037-10-09", "amount": 80, "desc": "早餐", "account": "範例 Demo"}]})
    c = next(x for x in cardcash.all_cards(conn) if x["account"] == "範例 Demo")
    assert not c["ready"] and c["level"] == "warn" and c["spent"] == 80
    assert "已經刷了 80" in c["text"] and "還沒設結帳日和繳款日" in c["text"]
    assert {x["account"] for x in home.home(conn)["cards"]} == {"範例 Demo", "範例卡二"}


def test_due_date_skips_the_weekend():
    """繳款日遇到六日順延到下一個上班日。"""
    from datetime import date

    assert cardcash.due_after(date(2037, 9, 30), 16) == date(2037, 10, 16)       # 2037-10-16 是週五：照舊
    assert cardcash.due_after(date(2037, 10, 31), 16) == date(2037, 11, 16)      # 2037-11-16 是週一：照舊
    assert cardcash.due_after(date(2037, 7, 31), 16) == date(2037, 8, 17)        # 8/16 是週日 → 順延到 8/17
    assert cardcash.due_after(date(2037, 4, 30), 16) == date(2037, 5, 18)        # 5/16 是週六 → 順延到 5/18
    assert cardcash.workday(date(2037, 10, 17)) == date(2037, 10, 19)            # 週六 → 下週一


def test_an_advance_on_the_card_still_counts_toward_the_card_bill(conn):
    """幫別人先付（代墊）刷的卡：不是花費，但錢是卡上的錢——卡費預留與「下期已刷」都要算，不然銀行會少留。"""
    before = one(conn)
    tid = ingest.run(conn, {"records": [{"type": "expense", "date": "2037-10-08", "amount": 300, "desc": "線上訂閱",
                                         "account": "範例卡二"}]})["results"][0]["id"]
    after_expense = one(conn)
    assert after_expense["next"]["amount"] == before["next"]["amount"] + 300
    from money_engine.services import editing
    with db.write(conn) as w:
        editing.edit(w, tid, advance=True)
    after_advance = one(conn)
    assert after_advance["next"]["amount"] == after_expense["next"]["amount"]          # 切成代墊，卡上要繳的不變
    assert after_advance["keep"] == after_expense["keep"]
