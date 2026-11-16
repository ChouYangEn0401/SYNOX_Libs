"""會浮動的格子：水電、訂閱的金額每期不一樣——差在容許範圍內（預設 ±10%）就算做完：
總覽打勾、錢包不再留、燈號不唸；滾存的格子也一樣，而且滾存的格子也有「今天處理」。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import allowance, envelopes, health, ingest, periods, plans, settings, stats, wallet
from money_engine.services import transactions as tx


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        plans.add_bucket(w, "訂閱服務")                       # extra1：訂閱
        plans.add_bucket(w, "水電瓦斯")                       # extra2：兩個月一次（滾存）
        plans.set_bucket_categories(w, "extra1", ["訂閱"])
        plans.set_bucket_categories(w, "extra2", ["電費", "水費", "瓦斯"])
        plans.set_bucket(w, "extra2", rollover=True, rollover_from="2037-09")
        plans.set_plan(w, "2037-09", income_expected=40000, buckets={"extra1": 3000, "extra2": 1500})
        plans.set_plan(w, "2037-10", income_expected=40000, buckets={"extra1": 3000, "extra2": 1500})
    yield c
    c.close()


def _pay(conn, amount, category, date="2037-10-08"):
    ingest.run(conn, {"records": [{"type": "expense", "amount": amount, "date": date, "account": "生活帳戶",
                                   "category": category, "desc": category}]}, actor="test", source="test")


def _env(conn, bucket):
    s = stats.period_summary(conn, "2037-10")
    rows = envelopes.rows(conn, plans.get(conn, "2037-10"), s["allocation"]["actual"], periods.get(conn, "2037-10"))
    return next(r for r in rows if r["id"] == bucket)


def _bucket_id(conn, name):
    for b in plans.buckets(conn, include_inactive=True):
        if name in (b["id"], b["name"], b["official"]):
            return b["id"]
    raise InvalidInput(f"沒有「{name}」這個用途")


def test_subscription_close_enough_is_done_only_when_flex(conn):
    _pay(conn, 2900, "訂閱")                                  # 2,900 / 3,000
    assert _env(conn, "extra1")["state"] == "todo"
    assert any(r["id"] == "extra1" for r in wallet.summary(conn)["reserved_items"])
    with db.write(conn) as w:
        plans.set_bucket(w, "extra1", flex=True)
    e = _env(conn, "extra1")
    assert e["state"] == "done" and e["near"] and e["flex"] and e["action"] is None
    assert not any(r["id"] == "extra1" for r in wallet.summary(conn)["reserved_items"])   # 不用再留 100


def test_over_a_little_is_done_too_but_far_off_is_not(conn):
    with db.write(conn) as w:
        plans.set_bucket(w, "extra1", flex=True)
    _pay(conn, 3150, "訂閱")                                  # 多 5%：還是做完，不算超過
    assert _env(conn, "extra1")["state"] == "done"
    assert not [i for i in health.checks(conn, "2037-10") if i["key"] == "over:extra1"]
    _pay(conn, 500, "訂閱", date="2037-10-09")                # 3,650：多 21%，超過
    assert _env(conn, "extra1")["state"] == "over"


def test_rollover_bucket_flex_and_today_button(conn):
    _pay(conn, 2900, "電費")                                  # 9 月留下 1,500＋10 月撥 1,500＝能用 3,000
    e = _env(conn, "extra2")
    assert e["state"] == "rolling" and e["action"]["amount"] == e["available"] - 2900 and e["can_set_aside"]
    with db.write(conn) as w:
        plans.set_bucket(w, "extra2", flex=True)
    e = _env(conn, "extra2")
    assert e["state"] == "done" and e["left"] == e["available"] - 2900      # 剩的照樣滾到下期，只是不再當成沒做完


def test_tolerance_is_a_setting(conn):
    with db.write(conn) as w:
        b = plans.set_bucket(w, _bucket_id(conn, "訂閱服務"), flex=True)  # 用名字找到那一格
    assert b["id"] == "extra1" and b["flex"] is True
    _pay(conn, 2600, "訂閱")                                  # 差 13%：預設 10% 不算
    assert _env(conn, "extra1")["state"] == "todo"
    value = settings.coerce(conn, "plan.flex_tolerance", "15%")
    with db.write(conn) as w:
        settings.put(w, "plan.flex_tolerance", value)
    assert settings.get(conn, "plan.flex_tolerance") == 0.15
    assert _env(conn, "extra1")["state"] == "done"
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            plans.set_bucket(w, "living", flex=True)


def test_flex_rollover_still_keeps_the_leftover_and_note_says_near(conn):
    """會浮動的滾存格子：差不多做到只是不提醒，剩下的錢照留、照滾（不然同一筆錢算兩次）；記完那一句也說 ≈✓。"""
    with db.write(conn) as w:
        plans.set_bucket(w, "extra2", flex=True)
        plans.set_bucket(w, "extra1", flex=True)
    _pay(conn, 2800, "電費")
    e = _env(conn, "extra2")
    assert e["state"] == "done" and e["keep"] == e["available"] - 2800
    assert any(r["id"] == "extra2" and r["amount"] == e["keep"] for r in wallet.summary(conn)["reserved_items"])
    _pay(conn, 3150, "訂閱", date="2037-10-09")
    t = conn.execute("SELECT * FROM transactions WHERE amount = 3150").fetchone()
    note = allowance.note_for(conn, tx.get(conn, t["id"]))
    assert note and "≈✓" in note
