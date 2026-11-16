"""本期統計：生活支出、日額度與每日狀態、各大類、六大類（轉帳與分期不重複算）。"""
import pytest

from money_engine import db
from money_engine.services import plans, refdata, stats, transactions as tx


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        plans.set_plan(w, "2037-10", income_expected=45000, group_budgets={"三餐": 9300, "吃好喝好": 3100})
    return c


def _add(w, conn, amount, day, category=None, kind="expense", account="現金", to=None, financed=False):
    cat = refdata.find_category(conn, category)["id"] if category else None
    acc = refdata.resolve_account(conn, account)["id"]
    toid = refdata.resolve_account(conn, to)["id"] if to else None
    return tx.create(w, kind=kind, amount=amount, date=day, account_id=acc, category_id=cat, to_account_id=toid,
                     financed=financed)


def test_living_spend_daily_status_and_groups(conn):
    with db.write(conn) as w:
        _add(w, conn, 150, "2037-10-05", "晚餐")
        _add(w, conn, 700, "2037-10-06", "吃好")          # 日額度 400 → 1.75 倍 超超額
        _add(w, conn, 500, "2037-10-07", "午餐")          # 1.25 倍 超額
        _add(w, conn, 87, "2037-10-08")                   # 待分類也算生活
        _add(w, conn, 12000, "2037-10-05", "定期捐款")    # 捐贈，不是生活
        _add(w, conn, 25000, "2037-10-06", "進修", account="範例 Demo", financed=True)
        _add(w, conn, 120, "2037-10-10", "早餐")          # 今天
    s = stats.period_summary(conn, "2037-10")
    lv = s["living"]
    assert lv["budget"] == 12400 and lv["daily_budget"] == 400
    assert lv["spent"] == 150 + 700 + 500 + 87 + 120
    assert lv["remaining"] == 12400 - lv["spent"]
    assert lv["days_left"] == 26 and lv["per_day_left"] == round(lv["remaining"] / 26)
    assert lv["uncategorized_count"] == 1 and lv["uncategorized_amount"] == 87
    by_date = {d["date"]: d for d in s["daily"]}
    assert by_date["2037-10-05"]["status"] == "省很多"
    assert by_date["2037-10-06"]["status"] == "超超額"
    assert by_date["2037-10-07"]["status"] == "超額"
    assert by_date["2037-10-10"]["status"] == "今天" and by_date["2037-10-11"]["future"]
    assert lv["over_days"] == 2
    groups = {g["name"]: g for g in s["groups"]}
    assert groups["三餐"]["spent"] == 770 and groups["三餐"]["budget"] == 9300
    assert groups["待分類"]["spent"] == 87
    assert s["allocation"]["actual"]["giving"] == 12000
    assert s["allocation"]["financed"] == 25000 and s["allocation"]["actual"]["living"] == lv["spent"]


def test_transfers_by_purpose_and_card_payment_not_counted(conn):
    with db.write(conn) as w:
        _add(w, conn, 42000, "2037-10-05", "薪資", kind="income", account="生活帳戶")
        _add(w, conn, 3000, "2037-10-05", "緊急預備金", kind="transfer", account="生活帳戶", to="現金")
        _add(w, conn, 1000, "2037-10-05", "股票", kind="transfer", account="生活帳戶", to="現金")
        _add(w, conn, 5000, "2037-10-10", "繳卡費", kind="transfer", account="生活帳戶", to="範例 Demo")
        _add(w, conn, 800, "2037-10-05", "勞退自提", kind="transfer", account="生活帳戶", to="現金")
    a = stats.period_summary(conn, "2037-10")["allocation"]
    assert a["income"] == 42000
    assert a["actual"]["saving"] == 3000 and a["actual"]["invest"] == 1000 and a["actual"]["fixed"] == 800
    assert sum(a["actual"].values()) == 4800              # 繳卡費不算


def test_closed_period_and_text(conn):
    s = stats.period_summary(conn, "2037-09")
    assert s["period"]["open"] is False and s["living"]["days_left"] == 0
    assert "2037-09 期" in stats.text_summary(s)
    s10 = stats.period_summary(conn, "2037-10")
    assert "還能花 12,400" in stats.text_summary(s10)


def test_weekday_pattern_only_speaks_when_it_is_clear(conn):
    """「你星期幾花最多」只有在真的很明顯時才講；資料太少、或差不多，就不要講。"""
    from datetime import date, timedelta

    from money_engine.services import ingest
    assert stats.weekday_pattern(conn) is None                      # 幾乎沒資料：閉嘴
    end = date(2037, 10, 10)
    records = []
    for i in range(56):
        day = end - timedelta(days=i)
        records.append({"type": "expense", "date": day.isoformat(), "amount": 600 if day.weekday() == 4 else 100,
                        "desc": "排骨便當", "category": "午餐"})
    ingest.run(conn, {"records": records})
    p = stats.weekday_pattern(conn)
    assert p["weekday"] == "五" and p["avg"] > p["others"] * 1.3 and p["weeks"] == 8

    flat = db.connect(":memory:")
    ingest.run(flat, {"records": [{"type": "expense", "date": (end - timedelta(days=i)).isoformat(), "amount": 100,
                                   "desc": "排骨便當", "category": "午餐"} for i in range(56)]})
    assert stats.weekday_pattern(flat) is None                      # 每天都差不多：不要硬講一天

