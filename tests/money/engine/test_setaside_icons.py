"""滾存的格子「撥好了」、月底要留多少與結餘、圖示只從圖示欄來。今天 2037-10-10。

能滾存的格子要能先把額度處理完（錢真的離開日常帳戶）；每個月的結餘可以轉到另一個帳戶，只留下滾存＋信用卡費用。
名字裡打了 emoji 就是名字的一部分，不從名字猜圖示。
"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import batches, ingest, plans, refdata
from money_engine.services import home as homepage


@pytest.fixture
def rolled():
    conn = db.connect()
    with db.write(conn) as w:
        plans.add_bucket(w, "水電瓦斯")
        plans.add_bucket(w, "手機費")
        plans.set_plan(w, "2037-10", income_expected=40000, buckets={"extra1": 1500, "extra2": 600})
        plans.set_bucket(w, "extra1", rollover=True)
        plans.set_bucket(w, "extra2", rollover=True)
        plans.set_bucket_categories(w, "extra1", ["瓦斯"])
        plans.set_bucket_categories(w, "extra2", ["手機網路"])
    yield conn
    conn.close()


def _env(conn, bucket):
    return next(e for e in homepage.home(conn)["envelopes"] if e["id"] == bucket)


def _set_aside(conn, bucket, **kw):
    """跟網站「撥額度」一樣：回傳結果＋批次（沒變就沒有批次）。"""
    with db.write(conn) as w:
        r = plans.set_aside(w, bucket, kw.pop("period", None), **kw)
        r["batch_id"] = w.batch_id if w.changes else None
    return r


# ---- 撥額度：錢要離開日常帳戶才算

def test_set_aside_must_say_where_the_money_goes(rolled):
    with pytest.raises(InvalidInput, match="放到哪"):                                          # 不給去向就不給撥（避免以為留好了）
        _set_aside(rolled, "extra1")
    assert _env(rolled, "extra1")["set_aside"] is None


def test_set_aside_moves_the_money_to_another_account_and_stays_visible(rolled):
    before = homepage.home(rolled)
    r = _set_aside(rolled, "extra1", to="存錢帳戶", from_account="生活帳戶", amount=1000)
    assert r["set_aside"]["amount"] == 1000 and r["set_aside"]["dest"] == "存錢帳戶"
    e = _env(rolled, "extra1")
    assert (e["set_aside"]["amount"], e["set_aside"]["kind"], e["can_set_aside"], e["used"]) == (1000, "transfer", False, 0)
    t = rolled.execute("SELECT kind, amount, purpose FROM transactions WHERE id = ?", (r["txn_id"],)).fetchone()
    assert tuple(t) == ("transfer", 1000, "prepay")                                             # 預付：錢還是自己的，不是支出
    assert homepage.home(rolled)["wallet"]["wallet"] == before["wallet"]["wallet"]             # 錢包總數不變（只是換戶頭）
    again = _set_aside(rolled, "extra1", to="存錢帳戶")
    assert again["batch_id"] is None                                                            # 重按不會多一筆


def test_set_aside_rejects_the_same_account_and_bad_amounts(rolled):
    with pytest.raises(InvalidInput, match="離開"):
        _set_aside(rolled, "extra1", to="生活帳戶", from_account="生活帳戶")
    with pytest.raises(InvalidInput):
        _set_aside(rolled, "extra1", to="預繳", amount=0)


def test_prepay_is_a_real_expense_and_the_mark_survives_done(rolled):
    r = _set_aside(rolled, "extra1", to="預繳", from_account="生活帳戶", category="瓦斯")
    assert r["set_aside"]["kind"] == "prepay"
    e = _env(rolled, "extra1")
    assert e["used"] == 1500 and e["state"] == "done"                                           # 預繳＝本期支出，這一格做完了
    assert e["set_aside"]["dest"] == "預繳"                                                      # 做完之後還看得到撥去哪


def test_set_aside_can_be_undone_with_its_transaction_and_only_applies_to_rollover_buckets(rolled):
    r = _set_aside(rolled, "extra2", to="存錢帳戶", from_account="生活帳戶")
    with db.write(rolled) as w:
        batches.revert(w, r["batch_id"])                                                       # 「復原」整批
    assert _env(rolled, "extra2")["set_aside"] is None
    r = _set_aside(rolled, "extra2", to="存錢帳戶", from_account="生活帳戶")
    tid = r["txn_id"]
    _set_aside(rolled, "extra2", undo=True)
    assert _env(rolled, "extra2")["set_aside"] is None
    assert rolled.execute("SELECT status FROM transactions WHERE id = ?", (tid,)).fetchone()[0] == "deleted"
    with pytest.raises(InvalidInput):
        _set_aside(rolled, "giving", to="存錢帳戶")                                             # 沒滾存的格子不用撥


def test_set_aside_prepay_with_amount_then_remove(rolled):
    r = _set_aside(rolled, "extra1", to="預繳", from_account="生活帳戶", amount=1200)
    assert r["set_aside"]["amount"] == 1200 and r["set_aside"]["kind"] == "prepay"
    r = _set_aside(rolled, "extra1", undo=True)
    assert r["set_aside"] is None


def test_the_carry_math_does_not_depend_on_pressing_set_aside(rolled):
    assert plans.rollover_carry(rolled, "extra2", "2037-11") == 600
    _set_aside(rolled, "extra2", to="存錢帳戶", from_account="生活帳戶")
    assert plans.rollover_carry(rolled, "extra2", "2037-11") == 600                            # 按不按都一樣：轉帳不算花掉


# ---- 月底：要留多少、結餘

def test_month_end_lists_the_pots_and_the_surplus(rolled):
    ingest.run(rolled, {"records": [{"type": "income", "amount": 40000, "category": "薪資", "account": "生活帳戶",
                                     "date": "2037-10-05", "desc": "薪水"}]})
    home = homepage.home(rolled)
    me = home["month_end"]
    pots = {p["name"]: p["amount"] for p in me["pots"]}
    assert pots == {"水電瓦斯": 1500, "手機費": 600} and me["pots_total"] == 2100
    lv, w = home["living"], home["wallet"]
    assert me["surplus"] == w["wallet"] - w["reserved"] - max(lv["left"], 0)                    # 就是「多出來」
    assert me["from"] == "生活帳戶" and me["to"] == "存錢帳戶"                                  # 起始資料裡另一個銀行帳戶
    with db.write(rolled) as wr:
        refdata.add_account(wr, name="旅遊帳戶", kind="bank", target_amount=100000)
    assert homepage.home(rolled)["month_end"]["to"] == "旅遊帳戶"                               # 有目標的優先


def test_a_bill_uses_up_the_pot(rolled):
    ingest.run(rolled, {"records": [{"type": "expense", "amount": 1500, "date": "2037-10-09", "category": "瓦斯", "account": "生活帳戶"}]})
    pots = {p["name"]: p["amount"] for p in homepage.home(rolled)["month_end"]["pots"]}
    assert pots["水電瓦斯"] == 0 and pots["手機費"] == 600
    e = _env(rolled, "extra1")
    assert (e["state"], e["can_set_aside"]) == ("done", False)                                 # 帳單已經來了：不用再撥


# ---- 圖示只從圖示欄來，名字就是名字

def test_the_icon_comes_only_from_the_icon_field(conn):
    with db.write(conn) as w:
        c = refdata.add_category(w, name="飲食(補記用)", group="三餐", icon="🧂")
    assert (c["name"], c["icon"]) == ("飲食(補記用)", "🧂")
    with db.write(conn) as w:
        c = refdata.update_category(w, c["id"], icon="🍱")
    assert (c["name"], c["icon"]) == ("飲食(補記用)", "🍱")


def test_an_emoji_in_the_name_stays_in_the_name(conn):
    with db.write(conn) as w:
        refdata.add_group(w, name="休閒")
        c = refdata.add_category(w, name="🎮遊戲點數", group="休閒")
    assert (c["name"], c["icon"]) == ("🎮遊戲點數", "")                                          # 不從名字裡猜圖示
    with db.write(conn) as w:
        c = refdata.update_category(w, c["id"], name="🎲 桌遊")
    assert (c["name"], c["icon"]) == ("🎲 桌遊", "")
    with db.write(conn) as w:
        g = refdata.add_group(w, name="🎉 慶祝", bucket="fixed")
    assert (g["name"], g["icon"]) == ("🎉 慶祝", "")
    with db.write(conn) as w:
        g = refdata.add_group(w, name="慶祝", bucket="fixed", icon="🎉")
    assert (g["name"], g["icon"]) == ("慶祝", "🎉")


def test_a_category_name_cannot_be_blank(conn):
    with pytest.raises(InvalidInput):
        with db.write(conn) as w:
            refdata.add_category(w, name="   ", group="三餐")


# ---- 薪水週期：換期照日期，薪水晚到只是回報

def test_salary_cycle_reports_when_the_salary_arrived(rolled):
    cal = homepage.home(rolled)["calendar"]
    assert cal["salary"]["arrived"] is False and cal["salary"]["start"] == "2037-10-05"
    ingest.run(rolled, {"records": [{"type": "income", "amount": 40000, "category": "薪資", "account": "生活帳戶",
                                     "date": "2037-10-08", "desc": "薪水"}]})
    sal = homepage.home(rolled)["calendar"]["salary"]
    assert (sal["arrived"], sal["date"], sal["amount"], sal["late_days"]) == (True, "2037-10-08", 40000, 3)
