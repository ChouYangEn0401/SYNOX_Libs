"""依現在的錢估每天可以花多少。今天 2037-10-10；本期 10/5–11/4，還有 26 天（含今天）。"""
import pytest

from money_engine import db
from money_engine.services import balances, estimate, home, ingest, piggy, plans, refdata


def test_compute_is_pure_math():
    e = estimate.compute(cash_total=30000, unverified=0, reserved_items=[{"name": "固定支出", "amount": 8000}],
                         card_items=[{"account": "範例卡二", "bill": 1500, "next": 500, "keep": 2000}],
                         days_left=10, plan_per_day=1500, salary={"arrived": True, "date": "2037-10-06"})
    assert e["pool"] == 20000 and e["per_day"] == 2000 and e["level"] == "ok"
    assert e["basis"] == "after_salary" and e["label"] == "薪水 10/6 入帳後重算"
    assert [r["amount"] for r in e["reasons"]] == [30000, -8000, -2000, 20000, 2000]
    assert "範例卡二" in e["reasons"][2]["text"] and "這期帳單還沒繳 1,500" in e["reasons"][2]["text"]


def test_compute_warns_when_far_below_plan_or_negative():
    low = estimate.compute(cash_total=20000, unverified=1, reserved_items=[], card_items=[], days_left=10,
                           plan_per_day=3000, salary=None)
    assert low["per_day"] == 2000 and low["level"] == "warn"                  # 比計畫少兩成以上
    assert low["basis"] == "before_salary" and "薪水還沒進來" in low["label"]
    assert "1 個帳戶還沒核對" in low["reasons"][0]["text"]
    close = estimate.compute(cash_total=26000, unverified=0, reserved_items=[], card_items=[], days_left=10,
                             plan_per_day=3000, salary=None)
    assert close["level"] == "ok"                                              # 2,600 vs 3,000：差不到兩成
    neg = estimate.compute(cash_total=1000, unverified=0, reserved_items=[{"name": "房租", "amount": 5000}],
                           card_items=[], days_left=5, plan_per_day=None, salary=None)
    assert neg["pool"] == -4000 and neg["per_day"] == 0 and neg["level"] == "warn"


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name="範例卡二", kind="credit_card", card_closing_day=25, card_due_day=13,
                            pay_from_account_id=refdata.resolve_account(c, "生活帳戶")["id"])
        plans.set_plan(w, "2037-10", income_expected=50000, buckets={"fixed": 8000, "living": 15000})
        balances.set_snapshot(w, account="生活帳戶", balance=40000, as_of="2037-10-04")
    ingest.run(c, {"records": [{"type": "expense", "date": "2037-09-20", "amount": 1500, "desc": "刷卡", "account": "範例卡二"},
                               {"type": "expense", "date": "2037-10-08", "amount": 500, "desc": "刷卡", "account": "範例卡二"}]})
    return c


def salary(c, day="2037-10-06", amount=50000):
    ingest.run(c, {"records": [{"type": "income", "date": day, "amount": amount, "category": "薪資", "account": "生活帳戶",
                                "desc": "薪水"}]})


def test_same_formula_as_piggy_leftover(conn):
    """跟撲滿「分到最後剩下的」同一套：pool ＝ leftover ＋ 生活費還剩的（卡費從現金扣、不從錢包再扣一次）。"""
    salary(conn)
    e = estimate.for_period(conn)
    v = piggy.cash_view(conn)
    assert e["pool"] == v["leftover"] + v["living_left"]
    assert e["cash"] == 90000 and e["card_keep"] == 2000 and e["reserved"] == v["reserved"]
    assert e["days_left"] == 26 and e["per_day"] == round(e["pool"] / 26)
    assert e["basis"] == "after_salary" and e["label"] == "薪水 10/6 入帳後重算"


def test_before_salary_is_labelled(conn):
    e = estimate.for_period(conn)
    assert e["basis"] == "before_salary" and e["label"] == "薪水還沒進來，這個估算是用上期剩的錢"
    assert e["cash"] == 40000


def test_past_period_has_no_estimate(conn):
    assert estimate.for_period(conn, "2037-09") is None
    assert home.home(conn, "2037-09")["calendar"]["estimate"] is None


def test_does_not_write(conn):
    salary(conn)
    before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    estimate.for_period(conn)
    home.home(conn)
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before


def test_home_and_summary_text_use_the_same_estimate(conn):
    """總覽的月曆與摘要文字用的是同一個估算。"""
    salary(conn)
    est = estimate.for_period(conn)
    cal = home.home(conn)["calendar"]
    assert cal["estimate"]["per_day"] == est["per_day"] and cal["estimate"]["reasons"]
    assert "依現在的錢估：每天可以花" in estimate.text(est) and est["basis"] == "after_salary"
