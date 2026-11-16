"""固定項目是「用途底下的項目」：薪水怎麼分與每個月固定的錢是同一件事。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import allowance, envelopes, ingest, periods, plans, recurring, stats


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        plans.set_plan(w, "2037-10", income_expected=40000,
                       buckets={"giving": 4000, "fixed": 15000, "saving": 10000}, group_budgets={"三餐": 9300, "吃好喝好": 3100})
    yield c
    c.close()


def _row(conn, bucket, key="2037-10"):
    p = periods.get(conn, key)
    s = stats.period_summary(conn, key)
    return next(r for r in envelopes.rows(conn, plans.get(conn, key), s["allocation"]["actual"], p) if r["id"] == bucket)


def _rent(conn, amount=5000, date="2037-10-09", **rec):
    ingest.run(conn, {"records": [{"type": "expense", "amount": amount, "desc": "網路轉", "category": "房租", "date": date,
                                   "account": "生活帳戶", **rec}]}, actor="t", source="t")


def test_an_item_can_say_which_bucket_it_belongs_to(conn):
    with db.write(conn) as w:
        a = recurring.add(w, name="水電瓦斯", kind="expense", amount=None, day_of_month=9, bucket="固定支出")   # 金額不一定、沒有固定細項
        recurring.add(w, name="房租", kind="expense", amount=5000, day_of_month=9, category="房租")
    assert a["bucket_id"] == "fixed"
    by_name = {r["name"]: r for r in recurring.status(conn, "2037-10")}
    assert by_name["水電瓦斯"]["bucket"] == "fixed"                 # 明說的
    assert by_name["房租"]["bucket"] == "fixed"                     # 沒明說：照細項（住→固定支出）


def test_living_cannot_hold_fixed_items(conn):
    with pytest.raises(InvalidInput, match="生活費"):
        with db.write(conn) as w:
            recurring.add(w, name="每天的錢", kind="expense", amount=100, bucket="生活費")


def test_the_item_can_be_moved_or_unset_later(conn):
    with db.write(conn) as w:
        it = recurring.add(w, name="手機費", kind="expense", amount=600, day_of_month=15, category="手機網路")
        moved = recurring.update(w, it["id"], bucket="giving")
        assert moved["bucket_id"] == "giving"
        assert recurring.update(w, it["id"], bucket="")["bucket_id"] is None


def test_items_are_the_breakdown_of_the_bucket_not_another_amount(conn):
    """固定支出 15,000 是總額；房租、手機費、線上訂閱是它的明細，剩下的叫「其他」（電費這種金額不一定的）。"""
    with db.write(conn) as w:
        recurring.add(w, name="房租", kind="expense", amount=5000, day_of_month=9, category="房租", account="生活帳戶")
        recurring.add(w, name="手機費", kind="expense", amount=600, day_of_month=15, category="手機網路")
        recurring.add(w, name="線上訂閱", kind="expense", amount=2000, day_of_month=10, category="訂閱")
        recurring.add(w, name="水電瓦斯", kind="expense", amount=None, day_of_month=9, bucket="固定支出")
    row = _row(conn, "fixed")
    assert [i["name"] for i in row["items"]] == ["房租", "水電瓦斯", "線上訂閱", "手機費"]        # 照每月幾號排
    assert row["items_planned"] == 7600 and row["other"] == 15000 - 7600                         # 其他＝總額 − 項目
    assert row["action"] is None                                                                 # 有項目就不放整格的「今天處理」
    rent = next(i for i in row["items"] if i["name"] == "房租")
    assert rent["done"] is False and rent["action"]["amount"] == 5000 and rent["action"]["category"] == "房租"
    floating = next(i for i in row["items"] if i["name"] == "水電瓦斯")
    assert floating["amount"] is None and floating["action"]["amount"] is None                    # 金額不一定：按下去讓他填


def test_recording_the_item_makes_it_done_and_counts_in_the_bucket(conn):
    with db.write(conn) as w:
        recurring.add(w, name="房租", kind="expense", amount=5000, day_of_month=9, category="房租", account="生活帳戶")
    _rent(conn)
    row = _row(conn, "fixed")
    rent = row["items"][0]
    assert rent["done"] and rent["paid"] == 5000 and rent["action"] is None
    assert row["used"] == 5000 and row["other_used"] == 0                                        # 項目用掉的，不算在「其他」


def test_items_adding_up_to_more_than_the_bucket_is_flagged(conn):
    with db.write(conn) as w:
        recurring.add(w, name="大項目", kind="expense", amount=16000, day_of_month=9, bucket="固定支出")
    items = {i["key"]: i for i in allowance.check(conn, "2037-10")}
    assert "items:fixed" in items and "1,000" in items["items:fixed"]["text"]


def _bucket(conn, label):
    with db.write(conn) as w:
        return plans.add_bucket(w, label)["id"]


def test_folding_a_custom_bucket_into_fixed_keeps_the_total_and_removes_the_double_count(conn):
    """房租、手機費各開一格，會跟固定支出總額重複。併進去＝它們變成固定支出底下的項目。"""
    rent = _bucket(conn, "房租")                                       # 同名細項自動對上
    with db.write(conn) as w:
        plans.set_plan(w, "2037-10", buckets={rent: 5000})
        r = plans.fold_bucket(w, rent, "固定支出", period_key="2037-10")
    assert r["amount"] == 5000 and r["moved_categories"] == ["房租"] and r["item"]["category"] == "房租"
    p = plans.get(conn, "2037-10")
    assert p["buckets"]["fixed"] == 15000 + 5000 and p["buckets"].get(rent) is None       # 金額加到總額、舊格子不見
    assert rent not in plans.bucket_ids(conn)                                             # 停用了
    row = _row(conn, "fixed")
    assert [i["name"] for i in row["items"]] == ["房租"] and row["other"] == 15000        # 其他＝本來的 15,000
    _rent(conn)
    assert _row(conn, "fixed")["items"][0]["done"] and _row(conn, "fixed")["used"] == 5000  # 記房租自動算進固定支出的項目


def test_folding_when_the_amount_was_already_inside_the_total(conn):
    phone = _bucket(conn, "手機費")
    with db.write(conn) as w:
        plans.set_plan(w, "2037-10", buckets={phone: 600})
        plans.fold_bucket(w, phone, "固定支出", add_to_parent=False, period_key="2037-10")
    assert plans.get(conn, "2037-10")["buckets"]["fixed"] == 15000                         # 本來就在裡面：總額不動


def test_fold_guards(conn):
    x = _bucket(conn, "雜七雜八")
    with pytest.raises(InvalidInput, match="核心"):
        with db.write(conn) as w:
            plans.fold_bucket(w, "fixed", "捐贈")
    with pytest.raises(InvalidInput, match="轉到"):                                        # 存錢的項目是定期轉帳
        with db.write(conn) as w:
            plans.fold_bucket(w, x, "存錢")
    with db.write(conn) as w:
        plans.fold_bucket(w, x, "存錢", to="現金")                                        # 說了轉到哪就行
    item = next(i for i in recurring.all_items(conn, include_inactive=True) if i["name"] == "雜七雜八")
    assert item["kind"] == "transfer" and item["purpose"] == "saving" and item["bucket_id"] == "saving"


def test_fold_uses_the_day_you_actually_paid_and_the_item_sums_all_payments(conn):
    rent = _bucket(conn, "房租")
    _rent(conn)
    with db.write(conn) as w:
        plans.set_plan(w, "2037-10", buckets={rent: 5000})
        r = plans.fold_bucket(w, rent, "固定支出", period_key="2037-10")
    assert r["item"]["day_of_month"] == 9                                                   # 你是 9 號付的，不是預設的 5 號
    with db.write(conn) as w:
        recurring.add(w, name="線上訂閱", kind="expense", amount=2000, day_of_month=10, category="訂閱")
    for amount, day in ((1200, "2037-10-05"), (800, "2037-10-06")):                          # 同一項這期記了兩筆（兩個不同的服務）
        ingest.run(conn, {"records": [{"type": "expense", "amount": amount, "desc": "線上服務", "category": "訂閱", "date": day,
                                       "account": "範例 Demo"}]}, actor="t", source="t")
    item = next(i for i in _row(conn, "fixed")["items"] if i["name"] == "線上訂閱")
    assert item["done"] and item["paid"] == 1200 + 800                                       # 加起來，不是只看最近一筆
