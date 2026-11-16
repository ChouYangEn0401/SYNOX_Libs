"""週報／月報的數字：範圍、預算（週跨期按日額度）、各大類與上一段比、最大筆、常去的店、小日記。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import report

from .reviewdata import EXPENSES, seed


@pytest.fixture
def conn():
    c = db.connect()
    seed(c)
    return c


def test_report_month_and_week(conn):
    m = report.build(conn, "month", "2037-09")
    assert m["label"] == "2037-09 期（9/5–10/4）" and m["closed"] and m["days"] == 30
    assert m["living"]["spent"] == sum(e[2] for e in EXPENSES) and m["living"]["budget"] == 12500
    assert m["income"] == 45000 and m["allocation"]["plan"]["giving"] == 4500
    assert m["top"][0] == {"date": "2037-10-03", "desc": "耳機", "category": "購物", "amount": 2400}
    assert m["frequent"][0]["name"] == "排骨便當" and m["frequent"][0]["count"] == 3
    assert m["notes"] == {"2037-09-30": "朋友生日，吃燒肉"} and len(m["transactions"]) == 8
    w = report.build(conn, "week", "2037-W40")
    assert (w["start"], w["end"]) == ("2037-09-28", "2037-10-04") and w["living"]["spent"] == 140 + 1200 + 70 + 2400
    assert w["previous"]["key"] == "2037-W39" and w["previous"]["spent"] == 130 + 60
    assert w["living"]["budget"] == round(12500 / 30 * 7)        # 那 7 天都在 2037-09 期
    assert w["day_stats"]["way_over"] >= 2 and "比上一段" in report.text(w)
    now = report.build(conn, "month")                            # 不給 key＝這一期到今天
    assert now["key"] == "2037-10" and not now["closed"] and now["elapsed"] == 6


def test_text_skips_change_without_previous(conn):
    m = report.build(conn, "month", "2037-09")
    assert m["previous"]["spent"] == 0 and "（+" not in report.text(m)


def test_report_with_and_without_transactions_and_bad_key(conn):
    """不要明細時就不帶；要全部時明細與小日記都在；週的寫法不對就擋下來。"""
    w = report.build(conn, "week", "2037-W40", with_transactions=False)
    assert w["living"]["spent"] == 3810 and "transactions" not in w
    m = report.build(conn, "month", "2037-09")
    assert len(m["transactions"]) == len(EXPENSES) and m["notes"]
    with pytest.raises(InvalidInput):
        report.build(conn, "week", "2037-41")
