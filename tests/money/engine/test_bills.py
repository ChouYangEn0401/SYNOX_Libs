"""固定項目與帳單：每期記下不重複、扣繳、雙月、狀態；電費帳單、夏月、去年同期、繳費、提醒、撤銷。"""
import pytest

from money_engine import db
from money_engine.errors import Conflict
from money_engine.services import batches, bills, ingest, recurring, stats, transactions as tx


@pytest.fixture
def conn():
    return db.connect()


def test_recurring_post_once_with_salary_deductions(conn):
    with db.write(conn) as w:
        recurring.add(w, name="薪水", kind="income", amount=42000, day_of_month=5, category="薪資", account="生活帳戶",
                      deductions={"勞保": 1000, "健保": 620, "所得稅": 800, "勞退自提": 800})
        recurring.add(w, name="房租", kind="expense", amount=4800, day_of_month=5, category="房租", account="生活帳戶")
        recurring.add(w, name="定期捐款", kind="expense", amount=4200, day_of_month=6, category="定期捐款", account="生活帳戶")
        recurring.add(w, name="手機費", kind="expense", amount=800, day_of_month=20, category="手機網路", account="生活帳戶")
        recurring.add(w, name="電費", kind="expense", amount=None, day_of_month=20, category="電費", cadence="bimonthly")
    st = {s["name"]: s for s in recurring.status(conn, "2037-10")}
    assert st["房租"]["state"] == "到期了，還沒記" and st["手機費"]["state"] == "10/20 到期"
    assert st["薪水"]["due_date"] == "2037-10-05"
    with db.write(conn) as w:
        r = recurring.post(w, "2037-10")
    assert [c["name"] for c in r["created"]] == ["薪水", "房租", "定期捐款"]
    with db.write(conn) as w:
        assert recurring.post(w, "2037-10")["created"] == []          # 手機費 10/20 還沒到
        again = recurring.post(w, "2037-10", upcoming=True)
    assert [c["name"] for c in again["created"]] == ["手機費"] and again["created"][0]["date"] == "2037-10-10"
    s = stats.period_summary(conn, "2037-10")
    a = s["allocation"]
    assert a["income"] == 42000 and a["actual"]["giving"] == 4200
    assert a["actual"]["fixed"] == 1000 + 620 + 800 + 800 + 4800 + 800
    with db.write(conn) as w:
        assert recurring.post(w, "2037-10")["created"] == []


def test_bimonthly_only_every_other_month(conn):
    with db.write(conn) as w:
        recurring.add(w, name="水費", kind="expense", amount=300, day_of_month=10, category="水費", cadence="bimonthly")
    names = [len([s for s in recurring.status(conn, k) if s["name"] == "水費"]) for k in ("2037-10", "2037-11", "2037-12")]
    assert names == [1, 0, 1]


def test_electricity_bill_summer_trend_and_pay(conn):
    for end, amount, usage in [("2036-09-18", 1400, 600), ("2036-11-18", 800, 400)]:
        ingest.run(conn, {"records": [{"type": "bill", "bill": "電費", "amount": amount, "usage": usage,
                                       "period_end": end, "paid_date": end}]})
    out = ingest.run(conn, {"records": [{"type": "bill", "bill": "電費", "amount": 1650, "usage": 680,
                                         "period_start": "2037-07-20", "period_end": "2037-09-18", "due_date": "2037-10-13"}]})
    r = out["results"][0]
    assert r["summer"] and not r["paid"] and r["id"] is None
    assert any("10-13 到期" in w for w in out["warnings"]) and any("去年同期" in w for w in out["warnings"])
    t = bills.trend(conn, "electricity")
    assert [p["amount"] for p in t["points"]] == [1400, 800, 1650]
    assert "多 18%" in t["insight"] and "度數多 13%" in t["insight"]
    assert bills.alerts(conn)[0]["alert"] == "電費 1,650 3 天後到期"
    with db.write(conn) as w:
        p = bills.pay(w, r["bill_id"], account="生活帳戶")
    assert p["payment"]["category"] == "電費" and p["bill"]["paid"]
    with pytest.raises(Conflict):
        with db.write(conn) as w:
            bills.pay(w, r["bill_id"])
    assert bills.alerts(conn) == []


def test_bill_resend_and_conflict(conn):
    rec = {"type": "bill", "bill": "水費", "amount": 300, "usage": 20, "period_end": "2037-09-30"}
    ingest.run(conn, {"records": [rec]})
    assert ingest.run(conn, {"records": [rec]})["results"][0]["status"] == "replayed"
    with pytest.raises(Conflict):
        ingest.run(conn, {"records": [dict(rec, amount=400)]})


def test_bill_can_be_reverted(conn):
    out = ingest.run(conn, {"records": [{"type": "bill", "bill": "瓦斯", "amount": 280, "period_end": "2037-10-01",
                                         "paid": True}]})
    with db.write(conn) as w:
        batches.revert(w, out["batch_id"])
    assert bills.all_bills(conn) == [] and tx.query(conn)["total"] == 0
