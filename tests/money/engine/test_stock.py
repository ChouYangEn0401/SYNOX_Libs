"""長期用：花了錢、算在生活費總額，但不算在每天的日曆。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import editing, ingest, plans, report, stats


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        plans.set_plan(w, "2037-10", income_expected=40000, group_budgets={"三餐": 9300, "吃好喝好": 3100})   # 生活費 12,400／31 天＝400
    yield c
    c.close()


def _add(conn, amount, day, **kw):
    out = ingest.run(conn, {"records": [{"type": "expense", "amount": amount, "date": day, "account": "現金", **kw}]},
                     actor="t", source="t")
    return out["results"][0]


def _day(summary, day):
    return next(d for d in summary["daily"] if d["date"] == day)


def test_long_use_counts_in_the_total_but_not_in_the_day(conn):
    _add(conn, 2500, "2037-10-05", desc="耳機", category="購物", nature="stock")
    _add(conn, 150, "2037-10-05", desc="午餐", category="午餐")
    s = stats.period_summary(conn, "2037-10")
    lv = s["living"]
    assert lv["spent"] == 2650 and lv["stock"] == 2500 and lv["daily_spent"] == 150      # 錢真的花了
    assert _day(s, "2037-10-05")["total"] == 150 and _day(s, "2037-10-05")["stock"] == 2500
    assert _day(s, "2037-10-05")["level"] != "crit"                                       # 那天不會因為耳機變紅
    assert lv["daily_budget"] == round((12400 - 2500) / 31)                              # 扣掉長期用的，每天的額度才是這個
    assert lv["remaining"] == 12400 - 2650                                               # 剩下可以花的還是照實算


def test_without_the_flag_the_same_purchase_turns_the_day_red(conn):
    _add(conn, 2500, "2037-10-05", desc="耳機", category="購物")
    assert _day(stats.period_summary(conn, "2037-10"), "2037-10-05")["level"] == "crit"


def test_only_living_expenses_can_be_long_use(conn):
    with pytest.raises(InvalidInput, match="固定"):
        _add(conn, 300, "2037-10-10", desc="範例訂閱", category="訂閱", nature="stock")     # 訂閱本來就不在每天
    with pytest.raises(InvalidInput):
        ingest.run(conn, {"records": [{"type": "income", "amount": 100, "date": "2037-10-10", "category": "薪資",
                                       "nature": "stock"}]}, actor="t", source="t")


def test_it_can_be_added_and_removed_afterwards(conn):
    r = _add(conn, 1200, "2037-10-06", desc="日用品囤貨", category="日用品")
    assert _day(stats.period_summary(conn, "2037-10"), "2037-10-06")["total"] == 1200
    with db.write(conn) as w:
        assert editing.edit(w, r["id"], nature="stock")["nature"] == "stock"
    assert _day(stats.period_summary(conn, "2037-10"), "2037-10-06")["total"] == 0
    with db.write(conn) as w:
        assert editing.edit(w, r["id"], nature="")["nature"] is None
    assert _day(stats.period_summary(conn, "2037-10"), "2037-10-06")["total"] == 1200


def test_unsorted_amounts_are_marked_on_the_day(conn):
    """沒分類的不會消失（還是算在那天），但日曆上標出來，才知道這天的顏色還沒定。"""
    _add(conn, 300, "2037-10-05", desc="範例小吃店", merchant="範例小吃店")
    _add(conn, 200, "2037-10-05", desc="範例支付", merchant="範例支付")
    d = _day(stats.period_summary(conn, "2037-10"), "2037-10-05")
    assert d["total"] == 500 and d["unsorted"] >= 200 and d["unsorted_count"] >= 1


def test_reports_and_health_do_not_blame_the_day_for_long_use(conn):
    _add(conn, 2500, "2037-10-05", desc="耳機", category="購物", nature="stock")
    rep = report.build(conn, "month", "2037-10")
    assert rep["living"]["stock"] == 2500 and rep["living"]["spent"] == 2500
    assert rep["day_stats"]["over"] == 0
