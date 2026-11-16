"""欠別人：同一個入口記「我幫別人墊」與「別人幫我墊」，每個人看淨額；錢包等我還的那天才扣。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import allowance, debts, home, ingest, settings, stats, wallet


def iou(direction, who, amount, date="2037-10-08", **extra):
    rec = {"type": "iou", "direction": direction, "who": who, "amount": amount, "date": date, **extra}
    return ingest.run(db.connect(), {"records": [rec]})


def salary():
    ingest.run(db.connect(), {"records": [{"type": "income", "amount": 5000, "date": "2037-10-05", "desc": "薪水", "category": "薪資"}]})


def test_owe_counts_as_my_spending_but_wallet_waits_until_i_repay():
    """小華幫我付午餐 300：算我的生活費（吃的是我），錢包不動也不會多；還小華那天錢包才少 300，只算一次。"""
    salary()
    out = iou("owe", "小華", 300, desc="午餐", category="午餐", time="12:30")
    r = out["results"][0]
    assert (r["type"], r["direction"], r["who"], r["account"]) == ("expense", "owe", "小華", "欠別人")
    conn = db.connect()
    assert stats.period_summary(conn, "2037-10")["living"]["spent"] == 300           # 算我的花費
    book = wallet.passbook(conn, "2037-10")
    assert book["closing"] == 5000                                                    # 錢包沒少、也沒多
    row = next(i for i in book["items"] if i["type"] == "txn" and i["raw"] == 300)
    assert row["amount"] == 0 and "小華先幫你付" in row["detail"]
    s = wallet.summary(conn, "2037-10")
    assert any(x["id"] == "owe" and x["amount"] == 300 for x in s["reserved_items"])   # 欠的錢不是自由的
    pend = debts.pending_advances(conn)
    assert pend["total"] == 0 and pend["owe"]["total"] == 300
    assert pend["owe"]["who"][0]["counterparty"] == "小華"

    iou("repay", "小華", 300, date="2037-10-09", account="現金")
    assert wallet.passbook(conn, "2037-10")["closing"] == 4700                        # 還的那天才扣
    assert stats.period_summary(conn, "2037-10")["living"]["spent"] == 300            # 花費沒有多算一次
    assert debts.pending_advances(conn)["owe"]["total"] == 0
    assert not any(x["id"] == "owe" for x in wallet.summary(conn, "2037-10")["reserved_items"])


def test_both_directions_and_net_per_person():
    """小明欠我 500、我欠小華 300；同一個人兩邊都有就抵掉。"""
    iou("lend", "小明", 500, desc="電影票")
    iou("owe", "小華", 300, desc="午餐")
    conn = db.connect()
    pend = debts.pending_advances(conn)
    assert [(w["counterparty"], w["amount"]) for w in pend["who"]] == [("小明", 500)]
    assert [(w["counterparty"], w["amount"]) for w in pend["owe"]["who"]] == [("小華", 300)]
    iou("owe", "小明", 200, desc="飲料", date="2037-10-09")                           # 小明也幫我付過 200
    pend = debts.pending_advances(conn)
    assert [(w["counterparty"], w["amount"]) for w in pend["who"]] == [("小明", 300)]   # 淨額：小明欠我 300
    assert [(w["counterparty"], w["amount"]) for w in pend["owe"]["who"]] == [("小華", 300)]
    people = {p["counterparty"]: p for p in debts.people(conn)}
    assert people["小明"]["net"] == 300 and people["小明"]["direction"] == "they_owe"
    assert people["小華"]["net"] == -300 and people["小華"]["direction"] == "i_owe"
    iou("collect", "小明", 300, date="2037-10-10")                                    # 按「已還」收淨額，兩清
    pend = debts.pending_advances(conn)
    assert pend["total"] == 0 and [w["counterparty"] for w in pend["owe"]["who"]] == ["小華"]
    assert {p["counterparty"]: p["direction"] for p in debts.people(conn)}["小明"] == "even"


def test_partial_repay_is_first_in_first_out():
    iou("owe", "小華", 300, date="2037-10-01")
    iou("owe", "小華", 200, date="2037-10-05")
    iou("repay", "小華", 400, date="2037-10-08")
    owe = debts.pending_advances(db.connect())["owe"]
    assert owe["total"] == 100 and owe["items"][0]["date"] == "2037-10-05"


@pytest.mark.parametrize("rec,field", [
    ({"who": "小明", "amount": 300}, "direction"),                          # 沒說方向
    ({"direction": "borrow", "who": "小明", "amount": 300}, "direction"),    # 方向看不懂
    ({"direction": "owe", "amount": 300}, "who"),                           # 沒說是誰
])
def test_unclear_direction_or_person_is_blocked(rec, field):
    with pytest.raises(InvalidInput) as e:
        ingest.run(db.connect(), {"records": [{"type": "iou", "date": "2037-10-08", **rec}]})
    assert any(k.endswith(field) for k in e.value.fields)
    assert db.connect().execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0     # 整批沒寫


def test_repay_cannot_come_from_a_credit_card():
    iou("owe", "小華", 300)
    with pytest.raises(InvalidInput):
        iou("repay", "小華", 300, account="範例 Demo")


def test_old_owe_is_reminded_after_the_setting_days():
    iou("owe", "小華", 300, date="2037-09-01")
    conn = db.connect()
    items = allowance.check(conn, "2037-10")
    owe = [i for i in items if i["key"] == "owe"]
    assert owe and "小華 300" in owe[0]["text"] and owe[0]["level"] == "warn"
    with db.write(conn, actor="test", source="test", command="t") as w:
        settings.put(w, "owe.remind_days", 60)
    assert not [i for i in allowance.check(conn, "2037-10") if i["key"] == "owe"]


def test_fresh_owe_is_not_nagged():
    iou("owe", "小華", 300, date="2037-10-08")
    assert not [i for i in allowance.check(db.connect(), "2037-10") if i["key"] == "owe"]


def test_both_directions_show_on_home():
    """兩個方向都記得進去，總覽的「代墊」那一格兩邊都看得到；沒說方向的會被擋下來並說明。"""
    iou("owe", "小華", 300, desc="午餐")
    iou("lend", "小明", 500, account="現金")
    adv = home.home(db.connect())["advance"]
    assert adv["total"] == 500 and adv["owe"]["total"] == 300
    with pytest.raises(InvalidInput) as e:
        ingest.run(db.connect(), {"records": [{"type": "iou", "who": "小華", "amount": 300, "date": "2037-10-08"}]})
    assert "方向" in e.value.message


def test_lend_add_and_list_people_net():
    out = iou("owe", "小華", 300, desc="午餐")
    assert out["results"][0]["direction"] == "owe"
    iou("lend", "小明", 500, account="現金")
    conn = db.connect()
    pend = debts.pending_advances(conn)
    assert pend["total"] == 500 and pend["owe"]["total"] == 300
    assert {p["counterparty"]: p["net"] for p in debts.people(conn)} == {"小明": 500, "小華": -300}


def test_without_direction_error_has_a_hint():
    with pytest.raises(InvalidInput) as e:
        ingest.run(db.connect(), {"records": [{"type": "iou", "who": "小華", "amount": 300, "date": "2037-10-10"}]})
    assert e.value.hint and "owe" in e.value.hint
    assert "❌" not in e.value.message
