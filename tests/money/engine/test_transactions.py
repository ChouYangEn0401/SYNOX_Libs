"""交易服務：建立、驗證規則、修改、刪除、查詢。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput, NotFound
from money_engine.services import refdata, transactions as tx

CARD = "範例 Demo"


def ids(conn, *names):
    return [refdata.resolve_account(conn, n)["id"] for n in names]


def cat(conn, name):
    return refdata.find_category(conn, name)["id"]


def test_create_expense_with_items(conn):
    cash, = ids(conn, "現金")
    with db.write(conn, actor="chat:1", source="chat") as w:
        t = tx.create(w, kind="expense", amount=260, date="2037-10-09", time="20:13", account_id=cash,
                      category_id=cat(conn, "晚餐"), description="麻油雞", source="chat",
                      items=[{"name": "麻油雞", "amount": 200}, {"name": "燙青菜", "amount": "60"}])
    assert t["id"].startswith("t_") and t["category"] == "晚餐" and t["group"] == "三餐" and t["is_living"]
    assert t["items"] == [{"name": "麻油雞", "qty": None, "amount": 200}, {"name": "燙青菜", "qty": None, "amount": 60}]
    audit = conn.execute("SELECT action, after FROM audit_log WHERE entity_id = ?", (t["id"],)).fetchone()
    assert audit["action"] == "create" and "_items" in audit["after"]


@pytest.mark.parametrize("value,hint", [("126-10-09", "民國"), ("2037-13-01", "不存在"), ("10/9", "看不懂"),
                                        ("2037-10-12", "未來")])
def test_bad_dates(conn, value, hint):
    cash, = ids(conn, "現金")
    with pytest.raises(InvalidInput) as e:
        with db.write(conn) as w:
            tx.create(w, kind="expense", amount=10, date=value, account_id=cash)
    assert hint in e.value.message


def test_tomorrow_is_allowed_and_dates_normalised(conn):
    cash, = ids(conn, "現金")
    with db.write(conn) as w:
        t = tx.create(w, kind="expense", amount=10, date="2037/10/11", time="9:05", account_id=cash)
    assert t["date"] == "2037-10-11" and t["time"] == "09:05"


@pytest.mark.parametrize("value", [12.5, "12.5", 0, None, True, "abc"])
def test_bad_amounts(conn, value):
    cash, = ids(conn, "現金")
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            tx.create(w, kind="expense", amount=value, date="2037-10-09", account_id=cash)


def test_amount_accepts_float_integers_and_commas(conn):
    assert tx.parse_amount(260.0) == 260 and tx.parse_amount("1,280") == 1280 and tx.parse_amount("-50") == -50


def test_refund_is_negative_expense_but_income_must_be_positive(conn):
    cash, = ids(conn, "現金")
    with db.write(conn) as w:
        r = tx.create(w, kind="expense", amount=-300, date="2037-10-09", account_id=cash, category_id=cat(conn, "購物"))
    assert r["amount"] == -300
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            tx.create(w, kind="income", amount=-10, date="2037-10-09", account_id=cash, category_id=cat(conn, "薪資"))


def test_transfer_rules(conn):
    bank, card = ids(conn, "生活帳戶", CARD)
    with db.write(conn) as w:
        t = tx.create(w, kind="transfer", amount=5000, date="2037-10-10", account_id=bank, to_account_id=card,
                      category_id=cat(conn, "繳卡費"))
    assert t["purpose"] == "card_payment" and t["to_account"] == CARD and t["category"] == "繳卡費"
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            tx.create(w, kind="transfer", amount=10, date="2037-10-10", account_id=bank)
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            tx.create(w, kind="transfer", amount=10, date="2037-10-10", account_id=bank, to_account_id=bank)
    with db.write(conn) as w:
        t2 = tx.create(w, kind="transfer", amount=500, date="2037-10-10", account_id=bank, to_account_id=card)
    assert t2["purpose"] == "other"


def test_category_kind_must_match(conn):
    cash, = ids(conn, "現金")
    with pytest.raises(InvalidInput) as e:
        with db.write(conn) as w:
            tx.create(w, kind="expense", amount=10, date="2037-10-09", account_id=cash, category_id=cat(conn, "繳卡費"))
    assert "轉帳類" in e.value.message
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            tx.create(w, kind="income", amount=10, date="2037-10-09", account_id=cash)


def test_update_and_delete(conn):
    cash, card = ids(conn, "現金", CARD)
    with db.write(conn) as w:
        t = tx.create(w, kind="expense", amount=280, date="2037-09-10", account_id=cash, description="小籠包")
    with db.write(conn) as w:
        t2 = tx.update(w, t["id"], amount=250, account_id=card, category_id=cat(conn, "晚餐"),
                       items=[{"name": "小籠包"}])
    assert (t2["amount"], t2["account"], t2["category"], t2["items"][0]["name"]) == (250, CARD, "晚餐", "小籠包")
    with db.write(conn) as w:
        tx.delete(w, t["id"])
    with pytest.raises(NotFound):
        tx.get(conn, t["id"])
    assert tx.get(conn, t["id"], include_deleted=True)["status"] == "deleted"
    with pytest.raises(NotFound):
        with db.write(conn) as w:
            tx.update(w, t["id"], amount=1)


def test_query_filters_and_paging(conn):
    cash, card = ids(conn, "現金", CARD)
    with db.write(conn) as w:
        for i in range(5):
            tx.create(w, kind="expense", amount=100 + i, date=f"2037-10-0{i + 5}", account_id=card if i % 2 else cash,
                      category_id=cat(conn, "午餐") if i < 3 else None, description=f"便當{i}")
    page = tx.query(conn, start="2037-10-05", end="2037-11-04", limit=2)
    assert page["total"] == 5 and len(page["items"]) == 2 and page["items"][0]["date"] == "2037-10-09"
    page2 = tx.query(conn, start="2037-10-05", limit=2, cursor=page["next_cursor"])
    assert page2["items"][0]["date"] == "2037-10-07"
    assert tx.query(conn, uncategorized=True)["total"] == 2
    assert tx.query(conn, account_id=card)["total"] == 2
    assert tx.query(conn, text="便當3")["total"] == 1
    lunch_group = refdata.find_category(conn, "午餐")["group_id"]
    assert tx.query(conn, group_id=lunch_group)["total"] == 3
