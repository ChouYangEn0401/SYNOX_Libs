"""滾存（rollover）：每期撥的額度沒用完就留到下期、超過的下期補。水電費兩個月才收一次：
每個月撥額度、不真的搬錢，用額度滾存。今天 2037-10-10（本期 2037-10）。"""
from datetime import date

import pytest

from money_engine import db
from money_engine.errors import InvalidInput, NotFound
from money_engine.services import allowance, envelopes, ingest, periods, plans, settings, stats, wallet
from money_engine.services import home as homepage


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        plans.add_bucket(w, "水電瓦斯")
        plans.set_plan(w, "2037-09", income_expected=40000, buckets={"extra1": 1500})        # 每個月撥 1,500
    yield c
    c.close()


def _bill(conn, amount, date, **rec):
    ingest.run(conn, {"records": [{"type": "expense", "amount": amount, "date": date, "account": "生活帳戶", **rec}]},
               actor="test", source="test")


def test_only_custom_buckets_can_roll_over_and_not_while_locked(conn):
    with db.write(conn) as w:
        with pytest.raises(InvalidInput):
            plans.set_bucket(w, "fixed", rollover=True)                       # 核心的用途不行
        b = plans.set_bucket(w, "extra1", rollover=True, rollover_from="2037-09")
        assert (b["rollover"], b["rollover_from"]) == (True, "2037-09")
        with pytest.raises(InvalidInput):
            plans.set_bucket(w, "extra1", locked=True)                        # 滾存中不能固定住
        b = plans.set_bucket(w, "extra1", rollover=False)
        assert (b["rollover"], b["rollover_from"]) == (False, None)
        plans.set_bucket(w, "extra1", locked=True)
        with pytest.raises(InvalidInput):
            plans.set_bucket(w, "extra1", rollover=True)                      # 固定住了不能滾存


def test_turning_it_on_starts_from_the_current_period_by_default(conn):
    with db.write(conn) as w:
        b = plans.set_bucket(w, "extra1", rollover=True)
    assert b["rollover_from"] == "2037-10"
    assert plans.rollover_carry(conn, "extra1", "2037-10") == 0               # 前面的期不追溯


def test_carry_is_what_was_not_used_and_goes_negative_when_over(conn):
    with db.write(conn) as w:
        plans.set_bucket(w, "extra1", rollover=True, rollover_from="2037-09")
    assert plans.rollover_carry(conn, "extra1", "2037-09") == 0               # 開始的那一期還沒有「上期」
    assert plans.rollover_carry(conn, "extra1", "2037-10") == 1500            # 9 月沒帳單：整份留下來
    _bill(conn, 3000, "2037-10-09", category="瓦斯")                            # 10 月帳單 3,000（兩個月的）
    with db.write(conn) as w:
        plans.set_bucket(w, "extra1", rollover=True)
        for c in ("瓦斯",):
            plans.set_bucket_categories(w, "extra1", [c])
    assert plans.rollover_carry(conn, "extra1", "2037-11") == 0               # 1500 + (1500 − 3000) = 0：剛好用完
    assert plans.rollover_carry(conn, "extra1", "2037-12") == 1500            # 11 月沒帳單，又留下一份


def test_off_means_zero_carry(conn):
    assert plans.rollover_carry(conn, "extra1", "2037-10") == 0
    assert plans.rollover_carry(conn, "no-such-bucket", "2037-10") == 0


def test_toggle_on_off_and_start_period(conn):
    with db.write(conn) as w:
        b = plans.set_bucket(w, "extra1", rollover=True)
    assert b["rollover"] is True
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            plans.set_bucket(w, "fixed", rollover=True)
    assert plans.buckets(conn)[-1]["id"] == "extra1" and plans.buckets(conn)[-1]["rollover"] is True
    with db.write(conn) as w:
        assert plans.set_bucket(w, "extra1", rollover=False)["rollover"] is False
    with db.write(conn) as w:
        assert plans.set_bucket(w, "extra1", rollover=True, rollover_from="2037-09")["rollover_from"] == "2037-09"


def _env(conn, key, bucket="extra1"):
    s = stats.period_summary(conn, key)
    rows = envelopes.rows(conn, plans.get(conn, key), s["allocation"]["actual"], periods.get(conn, key))
    return next(r for r in rows if r["id"] == bucket)


def _roll(conn):
    with db.write(conn) as w:
        plans.set_bucket(w, "extra1", rollover=True, rollover_from="2037-09")
        plans.set_bucket_categories(w, "extra1", ["電費", "水費", "瓦斯"])


def test_envelope_shows_what_is_carried_and_the_bill_lands_exactly(conn):
    """每月撥 1,500、9 月沒帳單 → 10 月帶 1,500 進來，3,000 的帳單剛好用完；11 月沒帳單，又留下一份。"""
    _roll(conn)
    before = _env(conn, "2037-10")
    assert (before["plan"], before["carry"], before["available"], before["used"], before["state"]) == (1500, 1500, 3000, 0, "rolling")
    # 滾存的格子也要能直接「今天處理」（帳單來了就記），不一定要先撥額度
    assert before["action"]["amount"] == 3000 and before["can_set_aside"]
    _bill(conn, 3000, "2037-10-09", category="瓦斯", desc="水電瓦斯")
    bill = _env(conn, "2037-10")
    assert (bill["used"], bill["left"], bill["state"]) == (3000, 0, "done")  # 剛好用完（不是「超過」）
    nxt = _env(conn, "2037-11")
    assert (nxt["carry"], nxt["available"], nxt["state"]) == (0, 1500, "rolling")


def test_going_over_what_is_available_is_still_over(conn):
    _roll(conn)
    _bill(conn, 3500, "2037-10-09", category="瓦斯")                         # 夏天帳單比較高
    e = _env(conn, "2037-10")
    assert (e["available"], e["left"], e["state"]) == (3000, -500, "over")
    assert _env(conn, "2037-11")["carry"] == -500                           # 下期的額度先補這一份


def test_without_rollover_the_same_bill_is_over_the_plan(conn):
    with db.write(conn) as w:
        plans.set_bucket_categories(w, "extra1", ["瓦斯"])
    _bill(conn, 3000, "2037-10-09", category="瓦斯")
    e = _env(conn, "2037-10")
    assert (e["available"], e["left"], e["state"]) == (1500, -1500, "over")   # 沒開滾存＝跟一般的格子一樣


def test_wallet_keeps_the_carried_money_for_the_bill_that_has_not_come(conn):
    """總覽的「要留的錢」要包含前期留下來的：帳單還沒來，那筆錢不能被當成可以花的。"""
    with db.write(conn) as w:
        plans.set_plan(w, "2037-10", income_expected=40000, buckets={"extra1": 1500})
    plain = {r["id"]: r["amount"] for r in wallet.summary(conn, "2037-10")["reserved_items"]}
    assert plain["extra1"] == 1500
    _roll(conn)
    rolled = {r["id"]: r["amount"] for r in wallet.summary(conn, "2037-10")["reserved_items"]}
    assert rolled["extra1"] == 3000                                          # 這期的 1,500 ＋ 9 月留下的 1,500


def test_allowance_does_not_nag_about_a_bill_that_fits_what_was_set_aside(conn):
    _roll(conn)
    _bill(conn, 3000, "2037-10-09", category="瓦斯")
    assert not any(a["key"] == "over:extra1" for a in allowance.check(conn, "2037-10"))
    _bill(conn, 500, "2037-10-09", category="電費")                           # 再多花就超過了
    assert any(a["key"] == "over:extra1" for a in allowance.check(conn, "2037-10"))


def test_note_after_recording_uses_the_available_amount(conn):
    _roll(conn)
    out = ingest.run(conn, {"records": [{"type": "expense", "amount": 3000, "date": "2037-10-09", "category": "瓦斯",
                                         "account": "生活帳戶"}]}, actor="test", source="test")
    assert any("做到了" in m for m in out["warnings"]), out["warnings"]       # 3,000 / 3,000，不是「比計畫 1,500 多」


# ---- 壞的起算期、視窗、每次重算、起算日 16 號、訊息

@pytest.mark.parametrize("bad", ["2037", "1999", "abc", "2037-13", "2037-9", ""])
def test_a_bad_rollover_from_is_rejected_and_never_breaks_the_pages(conn, bad):
    """起算期寫壞（打成 2037）不能存進去：存進去的話，之後總覽、存摺、記帳全部會壞。"""
    if bad == "":
        with db.write(conn) as w:
            b = plans.set_bucket(w, "extra1", rollover=True, rollover_from=bad)
        assert b["rollover_from"] == "2037-10"                                   # 空的＝這一期
    else:
        with pytest.raises((InvalidInput, NotFound)):
            with db.write(conn) as w:
                plans.set_bucket(w, "extra1", rollover=True, rollover_from=bad)
    assert homepage.home(conn)["envelopes"]                                      # 總覽、存摺照樣算得出來
    assert wallet.passbook(conn, "2037-10") is not None


def test_rollover_from_alone_needs_rollover_on(conn):
    with db.write(conn) as w:
        with pytest.raises(InvalidInput):
            plans.set_bucket(w, "extra1", rollover_from="2037-09")
        plans.set_bucket(w, "extra1", rollover=True, rollover_from="2037-09")
        b = plans.set_bucket(w, "extra1", rollover_from="2037-08")
    assert b["rollover_from"] == "2037-08"


def test_a_corrupt_stored_start_period_acts_like_rollover_off(conn):
    """資料庫裡已經有壞的起算期：當作沒開，不要讓頁面壞掉。"""
    conn.execute("UPDATE buckets SET rollover = 1, rollover_from = '2037' WHERE id = 'extra1'")
    assert plans.rollover_carry(conn, "extra1", "2037-10") == 0


def test_only_the_last_36_periods_before_this_one_count(conn):
    """起算期很久以前：看的是這一期往回 36 期（不是從起算期往後數再取最後 36，那樣近期的帳根本沒扣到）。"""
    with db.write(conn) as w:
        plans.set_plan(w, "2031-01", buckets={"extra1": 1000})
        plans.set_bucket(w, "extra1", rollover=True, rollover_from="2031-01")
        plans.set_bucket_categories(w, "extra1", ["瓦斯"])
    _bill(conn, 400, "2037-09-20", category="瓦斯")
    assert plans.rollover_carry(conn, "extra1", "2037-10") == 35 * 1000 + (1500 - 400)     # 9 月的計畫是 fixture 設的 1,500


def test_period_start_day_16_still_walks_every_period(conn):
    """起算日 16 號時期名會跳月：不能假設連續的月份就是連續的期，走過的每一期都要算到、不能丟例外。"""
    with db.write(conn) as w:
        settings.put(w, "period_start_day", 16)
    start = periods.period_for(conn, date(2037, 1, 20))
    target = periods.period_for(conn, date(2037, 6, 20))
    with db.write(conn) as w:
        plans.set_plan(w, start.key, buckets={"extra1": 1000})
        plans.set_bucket(w, "extra1", rollover=True, rollover_from=start.key)
    n, p = 0, start
    while p.start < target.start:
        n += 1
        p = periods.shift(conn, p, 1)
    assert n >= 4
    assert plans.rollover_carry(conn, "extra1", target.key) == n * 1000


def test_carry_is_cached_per_connection_but_follows_writes(conn):
    """同一次請求內不重算（總覽、錢包、提醒各問一次）；但自己記了一筆之後數字要跟著變。"""
    _roll(conn)
    assert plans.rollover_carry(conn, "extra1", "2037-10") == 1500
    _bill(conn, 500, "2037-09-20", category="瓦斯")                                  # 9 月其實花了 500
    assert plans.rollover_carry(conn, "extra1", "2037-10") == 1000


def test_over_messages_talk_about_what_is_available_not_the_plan(conn):
    _roll(conn)
    _bill(conn, 4000, "2037-10-09", category="瓦斯")
    item = next(a for a in allowance.check(conn, "2037-10") if a["key"] == "over:extra1")
    assert item["text"] == "水電瓦斯比能用的多 1,000" and "能用 3,000" in item["tip"]       # 不是「比計畫多」、也不是前後矛盾
    with db.write(conn) as w:
        plans.set_plan(w, "2037-09", buckets={"extra1": 0})                             # 9 月沒撥：欠到負的
    _bill(conn, 100, "2037-10-10", category="電費")
    out = ingest.run(conn, {"records": [{"type": "expense", "amount": 50, "date": "2037-10-10", "category": "水費",
                                         "account": "生活帳戶"}]}, actor="test", source="test")
    assert not any("-" in m and "計畫" in m for m in out["warnings"]), out["warnings"]   # 不會出現「比計畫 -xxx 多」
