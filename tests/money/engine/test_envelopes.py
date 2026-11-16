"""用途認領細項、預繳「算哪一期」、固定項目自己對上、今天處理的預設值、額度提醒。

自己加的用途「房租」「手機費」要跟記帳對得上；先把今天記對，狀態就自己更新；
預繳（11 月房租先繳）要能處理；額度有問題要提醒。
"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import (allowance, editing, envelopes, ingest, periods, plans, recurring, refdata, stats,
                                   transactions as tx, wallet)


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        for label in ("房租", "手機費"):
            plans.add_bucket(w, label)
        plans.set_plan(w, "2037-10", income_expected=45000,
                       buckets={"giving": 7000, "fixed": 15000, "saving": 10000, "extra1": 5000, "extra2": 600},
                       group_budgets={"三餐": 9300, "吃好喝好": 3100})
    yield c
    c.close()


def _row(conn, bucket, key="2037-10"):
    p = periods.get(conn, key)
    s = stats.period_summary(conn, key)
    return next(r for r in envelopes.rows(conn, plans.get(conn, key), s["allocation"]["actual"], p) if r["id"] == bucket)


def _record(conn, **rec):
    base = {"type": "expense", "date": "2037-10-09", "account": "生活帳戶"}
    return ingest.run(conn, {"records": [{**base, **rec}]}, actor="test", source="test")


def test_a_bucket_claims_categories_so_recording_it_moves_that_envelope(conn):
    assert _row(conn, "extra2")["categories"] == []                      # 「手機費」沒有同名的細項（細項叫手機網路）：等他自己對
    with db.write(conn) as w:
        r = plans.set_bucket_categories(w, "extra2", ["手機網路"])
    assert r["categories"] == ["手機網路"]
    _record(conn, amount=600, desc="範例電信", category="手機網路")
    phone, fixed = _row(conn, "extra2"), _row(conn, "fixed")
    assert (phone["used"], phone["state"], phone["action"]) == (600, "done", None)    # 做到了，不再有「今天處理」
    assert fixed["used"] == 0                                            # 不再算進固定支出
    with db.write(conn) as w:                                            # 放開就回到大類本來的用途
        plans.set_bucket_categories(w, "extra2", [])
    assert _row(conn, "fixed")["used"] == 600 and _row(conn, "extra2")["used"] == 0


def test_a_new_bucket_picks_up_the_category_with_the_same_name(conn):
    """用途「房租」＝細項「房租」：新增的當下就對上，記房租馬上動到那一格，不用再去設定勾。"""
    assert _row(conn, "extra1")["categories"] == ["房租"]
    _record(conn, amount=5000, desc="網路轉", category="房租")
    rent = _row(conn, "extra1")
    assert (rent["used"], rent["state"]) == (5000, "done") and _row(conn, "fixed")["used"] == 0


def test_unclaimed_buckets_are_one_reminder_that_names_them(conn):
    items = {i["key"]: i for i in allowance.check(conn, "2037-10")}
    assert "手機費" in items["unlinked"]["text"] and "房租" not in items["unlinked"]["text"]   # 房租已經自動對上
    assert "對應" in items["unlinked"]["tip"]
    with db.write(conn) as w:
        plans.set_bucket_categories(w, "extra2", ["手機網路"])
    assert "unlinked" not in {i["key"] for i in allowance.check(conn, "2037-10")}


def test_today_button_defaults_come_from_the_bucket(conn):
    a = _row(conn, "extra1")["action"]
    assert (a["kind"], a["amount"], a["category"], a["item"]) == ("expense", 5000, "房租", "房租")
    save = _row(conn, "saving")["action"]                                # 存錢＝搬錢，不是花掉
    assert (save["kind"], save["purpose"], save["amount"]) == ("transfer", "saving", 10000)
    assert _row(conn, "living")["action"] is None                        # 生活費沒有「今天處理」


def test_prepaying_next_period_counts_for_next_period_only(conn):
    with db.write(conn) as w:
        plans.set_bucket_categories(w, "extra1", ["房租"])
        plans.set_plan(w, "2037-11", buckets={"extra1": 5000})
    _record(conn, amount=5000, desc="10月房租", category="房租")
    r = _record(conn, amount=5000, desc="11月房租", category="房租", date="2037-10-10")   # 沒寫 for_period：從「11月」猜
    assert any("2037-11" in x for x in r["warnings"])
    assert _row(conn, "extra1", "2037-10")["used"] == 5000               # 這期只有 10 月那一筆
    nov = _row(conn, "extra1", "2037-11")
    assert (nov["used"], nov["state"], nov["prepaid_in"]) == (5000, "done", 5000)
    assert _row(conn, "extra1", "2037-10")["prepaid_out"] == 5000
    # 現金照付款日：錢包兩筆都扣了
    assert wallet.passbook(conn, "2037-10")["closing"] == -10000
    assert any(i["key"] == "prepaid:extra1" for i in allowance.check(conn, "2037-10"))


def test_prepay_is_explicit_in_edit_and_living_cannot_be_prepaid(conn):
    r = _record(conn, amount=800, desc="房租", category="房租")
    tid = r["results"][0]["id"]
    with db.write(conn) as w:
        t = editing.edit(w, tid, for_period="2037-11")
    assert t["for_period"] == "2037-11"
    with db.write(conn) as w:
        assert editing.edit(w, tid, for_period="")["for_period"] is None
    with pytest.raises(InvalidInput, match="生活費"):                     # 午餐是生活費：每天在花，不能預繳
        _record(conn, amount=95, desc="午餐", category="午餐", for_period="2037-11")
    free = _record(conn, amount=95, desc="11月午餐", category="午餐")     # 生活費不猜、照付款日
    assert tx.get(conn, free["results"][0]["id"])["for_period"] is None
    assert stats.period_summary(conn, "2037-10")["living"]["spent"] == 95


def test_two_months_in_one_payment_can_be_split(conn):
    """手機費下個月一次繳兩個月：記兩筆（這期、下期各一筆），不用特別的功能。"""
    with db.write(conn) as w:
        plans.set_bucket_categories(w, "extra2", ["手機網路"])
        plans.set_plan(w, "2037-11", buckets={"extra2": 600})
    _record(conn, amount=600, desc="手機費", category="手機網路")
    _record(conn, amount=600, desc="手機費", category="手機網路", for_period="2037-11")
    assert _row(conn, "extra2", "2037-10")["state"] == "done" and _row(conn, "extra2", "2037-11")["state"] == "done"


def test_recurring_matches_what_you_already_recorded(conn):
    """先把今天記對，固定項目就自己對上：「記下到期的」不會再多記一筆。"""
    with db.write(conn) as w:
        recurring.add(w, name="房租", kind="expense", amount=5000, day_of_month=9, category="房租", account="生活帳戶")
    assert recurring.status(conn, "2037-10")[0]["level"] == "warn"
    _record(conn, amount=5000, desc="網路轉", category="房租")
    st = recurring.status(conn, "2037-10")[0]
    assert st["state"] == "已記（你記的）" and st["level"] == "good"
    with db.write(conn) as w:
        r = recurring.post(w, "2037-10", upcoming=True)
    assert r["created"] == []                                            # 沒有重複記


def test_note_after_recording_says_done_or_over(conn):
    with db.write(conn) as w:
        plans.set_bucket_categories(w, "extra1", ["房租"])
    first = _record(conn, amount=5000, desc="房租", category="房租")
    assert any("做到了" in x for x in first["warnings"])
    second = _record(conn, amount=5000, desc="房租", category="房租", date="2037-10-10")
    assert any("比計畫" in x and "預繳" in x for x in second["warnings"])


def test_allowance_flags_overallocation_and_low_living_budget(conn):
    items = {i["key"]: i for i in allowance.check(conn, "2037-10")}
    assert items["plan"]["level"] == "warn" and "多" in items["plan"]["text"]    # 7000+15000+10000+5000+600+12400 = 50,000 > 45,000
    with db.write(conn) as w:
        for name, cap in (("早餐", 50), ("午餐", 150), ("晚餐", 200)):
            refdata.update_category(w, refdata.find_category(conn, name)["id"], default_budget=cap, cap_basis="day")
        plans.set_plan(w, "2037-10", buckets={"living": 2500})
    items = {i["key"]: i for i in allowance.check(conn, "2037-10")}
    assert "living:low" in items and "2,500" in items["living:low"]["text"]
