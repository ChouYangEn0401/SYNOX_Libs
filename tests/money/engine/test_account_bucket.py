"""帳戶綁定用途、轉帳多一種「預付」（prepay）。今天 2037-10-10。

轉帳的用途大概是「存錢」「投資」「預付」「支出」：哪怕存到別的地方，帳戶數目就是要真的減少。
情境：生活帳戶轉 2,000 進「旅遊基金帳戶」，要算進「旅遊基金」那一格，而不是只能算成「存錢」。
"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import ingest, plans, refdata, stats, wallet

TRIP = "旅遊基金帳戶"


def move(conn, amount, to, *, purpose="saving", day="2037-10-05", src="生活帳戶"):
    rec = {"type": "transfer", "amount": amount, "date": day, "from": src, "to": to, "desc": "搬錢"}
    if purpose:
        rec["purpose"] = purpose
    return ingest.run(conn, {"records": [rec]})


def actual(conn):
    return stats.period_summary(conn, "2037-10")["allocation"]["actual"]


@pytest.fixture
def conn():
    """一格「旅遊基金」（extra1，這期計畫 5,000）＋一個綁定它的帳戶；另外起始資料裡的「存錢帳戶」沒綁。"""
    c = db.connect()
    with db.write(c) as w:
        b = plans.add_bucket(w, "旅遊基金")
        plans.set_plan(w, "2037-10", income_expected=40000, buckets={b["id"]: 5000})
        refdata.add_account(w, name=TRIP, kind="bank", bucket="旅遊基金")
    assert refdata.resolve_account(c, "存錢帳戶")["bucket_id"] is None
    yield c
    c.close()


def test_transfer_into_a_bound_account_counts_for_that_bucket_and_leaves_the_wallet(conn):
    assert refdata.resolve_account(conn, TRIP)["bucket_id"] == "extra1"
    before = wallet.summary(conn)
    move(conn, 2000, TRIP)                                     # 寫的是「存錢」也一樣：帳戶說了算
    a = actual(conn)
    assert a["extra1"] == 2000 and a["saving"] == 0
    after = wallet.summary(conn)
    assert after["wallet"] == before["wallet"] - 2000          # 錢真的離開了
    left = {r["id"]: r["amount"] for r in after["reserved_items"]}
    assert left["extra1"] == 3000                              # 那一格還要留的跟著變少
    assert after["free"] == before["free"]                     # 本來就要留的錢：可以自由花的不會再少一次


def test_without_a_binding_nothing_changes(conn):
    move(conn, 1000, "存錢帳戶")                                 # 存錢 → 存錢那一格（照舊）
    move(conn, 700, "存錢帳戶", purpose="other")                  # 一般轉帳 → 不算哪一格、錢包不變
    a = actual(conn)
    assert a["saving"] == 1000 and a["extra1"] == 0
    book = wallet.passbook(conn, "2037-10")
    effects = sorted(i["amount"] for i in book["items"] if i["type"] == "txn")
    assert effects == [-1000, 0]


def test_trading_account_still_counts_for_trading(conn):
    with db.write(conn) as w:
        plans.set_bucket(w, "trading", active=True)
        refdata.add_account(w, name="證券-操盤", kind="securities", asset_class="trading")
        refdata.add_account(w, name="證券-投資", kind="securities", asset_class="invest")
    move(conn, 3000, "證券-操盤", purpose="investing")
    move(conn, 5000, "證券-投資", purpose="investing")
    a = actual(conn)
    assert a["trading"] == 3000 and a["invest"] == 5000 and a["extra1"] == 0


def test_prepay_is_a_purpose_and_has_no_bucket_of_its_own(conn):
    move(conn, 800, "存錢帳戶", purpose="prepay")
    row = conn.execute("SELECT purpose FROM transactions WHERE amount = 800").fetchone()
    assert row["purpose"] == "prepay"
    a = actual(conn)
    assert a["saving"] == 0 and a["extra1"] == 0               # 沒綁的帳戶：不算哪一格、錢包不變
    item = next(i for i in wallet.passbook(conn, "2037-10")["items"] if i["raw"] == 800)
    assert item["amount"] == 0 and item["title"] == "搬錢"
    move(conn, 1200, TRIP, purpose="prepay")                   # 轉進綁了的帳戶：算進那一格
    assert actual(conn)["extra1"] == 1200
    with pytest.raises(InvalidInput):                          # 預付不能轉進信用卡
        move(conn, 100, "範例 Demo", purpose="prepay")


def test_inactive_bucket_does_not_count(conn):
    with db.write(conn) as w:
        plans.set_bucket(w, "extra1", active=False)
    move(conn, 2000, TRIP)
    a = actual(conn)
    assert a.get("extra1", 0) == 0 and a["saving"] == 2000     # 那一格停用了：照用途（存錢）算


def test_binding_rules(conn):
    with db.write(conn) as w:
        for bad in ("生活費", "debt", "不存在的格子"):
            with pytest.raises(InvalidInput):
                refdata.add_account(w, name=f"x{bad}", kind="bank", bucket=bad)
        with pytest.raises(InvalidInput):                      # 信用卡不能綁
            refdata.add_account(w, name="另一張卡", kind="credit_card", bucket="旅遊基金")
        card = refdata.resolve_account(w.conn, "範例 Demo")
        with pytest.raises(InvalidInput):
            refdata.update_account(w, card["id"], bucket="旅遊基金")
        acc = refdata.resolve_account(w.conn, TRIP)
        with pytest.raises(InvalidInput):                      # 綁著的帳戶不能改成信用卡
            refdata.update_account(w, acc["id"], kind="credit_card")
        assert refdata.update_account(w, acc["id"], bucket="")["bucket_id"] is None


def test_bind_and_clear_by_name_or_id(conn):
    """新帳戶用用途名字綁、用 id 綁、用空字串或 None 清掉；生活費不能綁；帳戶清單看得到綁了哪一格。"""
    with db.write(conn) as w:
        acc = refdata.add_account(w, name="出遊帳戶", kind="bank", bucket="旅遊基金")
    assert acc["bucket_id"] == "extra1"
    with db.write(conn) as w:
        assert refdata.update_account(w, acc["id"], bucket=None)["bucket_id"] is None
    with db.write(conn) as w:
        assert refdata.update_account(w, acc["id"], bucket="extra1")["bucket_id"] == "extra1"
    with db.write(conn) as w:
        assert refdata.update_account(w, acc["id"], bucket="")["bucket_id"] is None
    for bad in ("生活費", "living"):
        with pytest.raises(InvalidInput):
            with db.write(conn) as w:
                refdata.update_account(w, acc["id"], bucket=bad)
    assert any(a["name"] == TRIP and a["bucket_id"] == "extra1" for a in refdata.accounts(conn))


def test_set_aside_uses_prepay_and_keeps_free_money_the_same(conn):
    """撥額度：purpose=prepay；就算放進綁了那一格的帳戶，也不算「做到」、錢包不動——
    滾存格子要留的錢本來就在「保留」裡，再算一次就扣兩次。"""
    with db.write(conn) as w:
        b = plans.add_bucket(w, "水電瓦斯")
        plans.set_plan(w, "2037-10", buckets={b["id"]: 1500})
        plans.set_bucket(w, b["id"], rollover=True)
        refdata.add_account(w, name="水電帳戶", kind="bank", bucket=b["id"])
    before = wallet.summary(conn)
    with db.write(conn) as w:
        r = plans.set_aside(w, b["id"], "2037-10", to="水電帳戶", from_account="生活帳戶")
    t = conn.execute("SELECT kind, amount, purpose FROM transactions WHERE id = ?", (r["txn_id"],)).fetchone()
    assert tuple(t) == ("transfer", 1500, "prepay")
    after = wallet.summary(conn)
    assert after["wallet"] == before["wallet"] and after["free"] == before["free"]
    assert actual(conn)[b["id"]] == 0
