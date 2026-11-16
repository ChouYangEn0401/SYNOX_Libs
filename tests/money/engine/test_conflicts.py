"""待解決：帳裡對不上的地方。每一種都有一個會講、一個不該講的例子；只讀不寫。今天 2037-10-10。"""
import pytest

from money_engine import db
from money_engine.services import balances, cards, conflicts, dedup, health, home, inbox, ingest, refdata, transactions as tx


@pytest.fixture
def conn():
    return db.connect()


def keys(c, prefix):
    return [i for i in conflicts.scan(c) if i["key"].startswith(prefix)]


def income(c, amount, day="2037-10-06", account="生活帳戶"):
    return ingest.run(c, {"records": [{"type": "income", "date": day, "amount": amount, "category": "薪資",
                                       "account": account, "desc": "薪水"}]})


def test_empty_book_says_nothing_to_do(conn):
    assert conflicts.scan(conn) == []
    p = home.home(conn)["conflicts"]
    assert p == {"count": 0, "bad": 0, "headline": "沒有待解決的", "items": []}


# (a) 可能重複

def _meal(c, desc, time):
    ingest.run(c, {"records": [{"type": "expense", "amount": 260, "date": "2037-10-09", "time": time, "desc": desc}]})


def test_duplicate_pair_listed_until_decided(conn):
    _meal(conn, "麻油雞", "20:13")
    _meal(conn, "麻油雞晚餐", "20:15")
    got = keys(conn, "dup:")
    assert len(got) == 1 and got[0]["level"] == "warn" and got[0]["where"]["tab"] == "home" and len(got[0]["ids"]) == 2
    with db.write(conn) as w:
        dedup.resolve(w, dedup.pending(conn)[0]["pair_id"], "keep_both")
    assert keys(conn, "dup:") == []


# (b) 同一天同金額同類別的收入兩筆

def test_same_income_twice_same_day(conn):
    ingest.run(conn, {"records": [{"type": "income", "date": "2037-10-06", "amount": 48000, "category": "薪資",
                                   "account": "生活帳戶", "desc": "薪水"} for _ in range(2)]})   # 同一批：不會進可能重複
    got = keys(conn, "twice:")
    assert len(got) == 1 and "10/6 生活帳戶有 2 筆 48,000 的薪資" == got[0]["title"] and "48,000" in got[0]["why"]


def test_different_amounts_or_already_decided_are_not_twice(conn):
    income(conn, 48000)
    income(conn, 3000)
    assert keys(conn, "twice:") == []
    income(conn, 3000)                                   # 不同批：可能重複會先抓到，這裡不重複講
    assert keys(conn, "twice:") == [] and keys(conn, "dup:")
    with db.write(conn) as w:
        dedup.resolve(w, dedup.pending(conn)[0]["pair_id"], "keep_both")   # 你說兩筆都是真的
    assert keys(conn, "twice:") == [] and keys(conn, "dup:") == []


# (c) 薪水進了不是薪水入帳的帳戶

def test_salary_into_the_wrong_account(conn):
    income(conn, 50000, account="現金")
    got = keys(conn, "salary_account:")
    assert len(got) == 1 and "「現金」" in got[0]["title"] and "生活帳戶" in got[0]["why"]


def test_salary_into_the_right_account_or_unclear_rule(conn):
    income(conn, 50000, account="生活帳戶")
    assert keys(conn, "salary_account:") == []
    income(conn, 1000, day="2037-10-07", account="現金")
    with db.write(conn) as w:                            # 兩個帳戶都寫「薪水入帳」：不知道哪個才對，不講
        refdata.add_account(w, name="銀行二", kind="bank", purpose="薪水入帳")
    assert keys(conn, "salary_account:") == []


# (d) 信用卡

@pytest.fixture
def card(conn):
    with db.write(conn) as w:
        refdata.add_account(w, name="範例卡二", kind="credit_card", card_closing_day=25, card_due_day=5,
                            pay_from_account_id=refdata.resolve_account(conn, "生活帳戶")["id"])
    ingest.run(conn, {"records": [{"type": "expense", "date": "2037-09-20", "amount": 1000, "desc": "刷卡", "account": "範例卡二"}]})
    return conn


def test_card_overdue_is_red_until_paid(card):
    got = keys(card, "card_overdue:")                    # 9/25 結帳、10/5 繳；今天 10/10
    assert len(got) == 1 and got[0]["level"] == "bad" and "1,000" in got[0]["title"]
    assert conflicts.scan(card)[0]["level"] == "bad"     # 紅的在最前面
    ingest.run(card, {"records": [{"type": "transfer", "date": "2037-10-09", "amount": 1000, "from": "生活帳戶",
                                   "to": "範例卡二", "desc": "繳卡費"}]})
    assert keys(card, "card_overdue:") == []


def test_card_statement_mismatch(card):
    with db.write(card) as w:
        cards.save_statement(w, card="範例卡二", closing_date="2037-09-25", due_date="2037-10-14", total=1000,
                             lines=[{"date": "2037-09-20", "desc": "刷卡", "amount": 1000}])
    assert keys(card, "card_statement:") == []           # 對得上：不講
    with db.write(card) as w:
        cards.save_statement(w, card="範例卡二", closing_date="2037-09-25", due_date="2037-10-14", total=1300,
                             lines=[{"date": "2037-09-20", "desc": "刷卡", "amount": 1000},
                                    {"date": "2037-09-22", "desc": "範例車行", "amount": 200}])
    got = keys(card, "card_statement:")
    assert len(got) == 1 and "1 筆沒對上" in got[0]["title"] and "差 100" in got[0]["title"]


# (e) 核對過的餘額跟帳差

def test_balance_check_gap(conn):
    with db.write(conn) as w:
        balances.set_snapshot(w, account="生活帳戶", balance=1000, as_of="2037-10-01")
    assert keys(conn, "balance:") == []                  # 只核對一次＝起始餘額
    ingest.run(conn, {"records": [{"type": "expense", "date": "2037-10-03", "amount": 100, "desc": "早餐", "account": "生活帳戶"}]})
    with db.write(conn) as w:
        balances.set_snapshot(w, account="生活帳戶", balance=900, as_of="2037-10-08")
    assert keys(conn, "balance:") == []                  # 帳對得上
    with db.write(conn) as w:
        balances.set_snapshot(w, account="生活帳戶", balance=850, as_of="2037-10-09")
    got = keys(conn, "balance:")
    assert len(got) == 1 and "差 50" in got[0]["title"] and "少了 50" in got[0]["why"]


# (f) 很多筆沒分類

def _unsorted(c, n):
    acc = refdata.resolve_account(c, "現金")["id"]
    for i in range(n):
        with db.write(c) as w:
            tx.create(w, kind="expense", amount=100 + i, date=f"2037-10-0{i + 1}", account_id=acc, description=f"不明 {i}")


def test_many_unsorted(conn):
    _unsorted(conn, conflicts.UNSORTED_MANY - 1)
    assert keys(conn, "unsorted") == []                  # 少少幾筆：健康檢查講就好
    inbox_item = next(i for i in health.checks(conn) if i["key"] == "inbox")
    assert "also" not in inbox_item
    _unsorted(conn, 1)
    got = keys(conn, "unsorted")
    assert len(got) == 1 and f"{conflicts.UNSORTED_MANY} 筆" in got[0]["title"] and got[0]["where"]["tab"] == "book"
    assert next(i for i in health.checks(conn) if i["key"] == "inbox")["also"] == "conflicts"


# 整包

def test_read_only_payload_and_home(conn):
    income(conn, 50000, account="現金")
    _meal(conn, "麻油雞", "20:13")
    _meal(conn, "麻油雞晚餐", "20:15")
    before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    r = conflicts.payload(conflicts.scan(conn))
    assert r["count"] == 2 and r["headline"] == "待解決 2 件" and {i["level"] for i in r["items"]} == {"warn"}
    for i in r["items"]:
        assert set(i) == {"key", "level", "title", "why", "where", "ids"} and i["where"]["label"]
    assert home.home(conn)["conflicts"]["count"] == 2
    assert "duplicates" in inbox.details(conn)                # 原本的待確認清單照舊
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before    # 只讀：一筆都沒寫
