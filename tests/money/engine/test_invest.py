"""投資：每月損益、目標與基準、市值當核對餘額、部位配置、設定＋手記一個月＋部位一起看。"""
import pytest

from money_engine import db
from money_engine.errors import InvalidInput
from money_engine.services import balances, batches, ingest, invest, refdata


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name="證券戶", kind="securities")
        invest.set_config(w, capital=100000, start="2036-09-04", target_year=0.22)
    yield c
    c.close()


def test_monthly_pnl_summary_target_and_benchmarks(conn):
    pnls = [-6000, 10000, -1000, 2000]
    for i, p in enumerate(pnls):
        ingest.run(conn, {"records": [{"type": "invest_pnl", "month": f"2036-{10 + i:02d}" if i < 3 else "2037-01",
                                       "pnl": p, "note": f"第 {i + 1} 個月"}]})
    s = invest.summary(conn)
    assert s["count"] == 4 and s["total"] == sum(pnls) and s["return"] == round(sum(pnls) / 100000, 4)
    assert s["target_monthly"] == round(100000 * (1.22 ** (1 / 12) - 1))
    assert [m["cumulative"] for m in s["months"]] == [-6000, 4000, 3000, 5000]
    assert s["hit_months"] == 2 and s["months"][1]["note"] == "第 2 個月"
    assert [b["name"] for b in s["benchmarks"]] == ["銀行定存", "ETF 定存", "台股基金", "操盤手"]


def test_resend_updates_and_market_value_sets_snapshot(conn):
    ingest.run(conn, {"records": [{"type": "invest_pnl", "month": "2037-09", "pnl": 2000, "market_value": 60000}]})
    out = ingest.run(conn, {"records": [{"type": "invest_pnl", "month": "2037-09", "pnl": 2000, "market_value": 60000}]})
    assert out["results"][0]["status"] == "unchanged"
    acc = refdata.resolve_account(conn, "證券戶")["id"]
    assert balances.balance_at(conn, acc, "2037-09-30")["balance"] == 60000
    out = ingest.run(conn, {"records": [{"type": "invest_pnl", "month": "2037-09", "pnl": 3000}]})
    assert out["results"][0]["status"] == "replaced" and invest.summary(conn)["total"] == 3000
    with pytest.raises(InvalidInput):
        ingest.run(conn, {"records": [{"type": "invest_pnl", "month": "2037/9", "pnl": 1}]})


def test_holdings_replace_same_day_and_buckets(conn):
    items = [{"name": "範例ETF", "bucket": "定存型股票", "market_value": 30000},
             {"name": "範例股A", "bucket": "短單股票", "market_value": 5000},
             {"name": "範例股B", "bucket": "短單股票", "market_value": 10000}]
    ingest.run(conn, {"records": [{"type": "holdings", "as_of": "2036-10-21", "items": items}]})
    out = ingest.run(conn, {"records": [{"type": "holdings", "as_of": "2036-10-21", "items": items[:2]}]})
    h = invest.latest_holdings(conn)
    assert h["total"] == 30000 + 5000 and [b["bucket"] for b in h["buckets"]] == ["定存型股票", "短單股票"]
    with db.write(conn) as w:
        batches.revert(w, out["batch_id"])
    assert invest.latest_holdings(conn)["total"] == 30000 + 5000 + 10000


def test_config_month_and_holdings_in_summary():
    conn = db.connect()
    with db.write(conn) as w:
        refdata.add_account(w, name="證券戶", kind="securities")
    with db.write(conn) as w:
        cfg = invest.set_config(w, capital=110000, start="2036-09-04", target_year=0.22)
    assert cfg["capital"] == 110000
    with db.write(conn) as w:
        r = invest.add_month(w, month="2036-10", pnl=-1000, account=None, market_value=None, net_deposit=None,
                             note="第一個月", source="cli")
    assert r["month"]["pnl"] == -1000
    ingest.run(conn, {"records": [{"type": "holdings", "items": [{"name": "長單", "bucket": "長單股票", "market_value": 40000}]}]})
    s = invest.summary(conn)
    assert s["count"] == 1 and s["holdings"]["total"] == 40000
    assert s["months"][0]["note"] == "第一個月" and s["config"]["target_year"] == 0.22
    conn.close()
