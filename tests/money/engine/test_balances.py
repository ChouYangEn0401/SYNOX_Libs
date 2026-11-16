"""餘額：快照＋之後的交易、信用卡推算成負的、差額與補差額、備用金群組、帳戶總覽、撤銷。"""
import pytest

from money_engine import db
from money_engine.errors import Conflict
from money_engine.services import balances, batches, ingest, refdata


@pytest.fixture
def conn():
    c = db.connect()
    with db.write(c) as w:
        refdata.add_account(w, name="銀行 彈性活存", kind="bank", purpose="臨時開銷", reserve_group="緊急花銷緩衝",
                            floor_amount=36000, target_amount=50000)
        refdata.add_account(w, name="銀行 長期預備金", kind="bank", reserve_group="長期預備金",
                            floor_amount=50000, target_amount=90000)
        refdata.add_account(w, name="TEST1 長期預備金", kind="securities", reserve_group="長期預備金")
        refdata.add_account(w, name="範例卡二", kind="credit_card", aliases=["card2"])
    return c


def test_balance_is_snapshot_plus_later_transactions(conn):
    ingest.run(conn, {"records": [
        {"type": "balance", "account": "生活帳戶", "balance": 20000, "as_of": "2037-10-01"},
        {"type": "income", "amount": 42000, "date": "2037-10-05", "category": "薪資", "account": "生活帳戶"},
        {"type": "expense", "amount": 4800, "date": "2037-10-05", "category": "房租", "account": "生活帳戶"},
        {"type": "transfer", "amount": 3000, "date": "2037-10-06", "from": "生活帳戶", "to": "銀行 彈性活存"},
        {"type": "expense", "amount": 260, "date": "2037-10-09", "desc": "麻油雞", "account": "card2"}]})
    acc = refdata.resolve_account(conn, "生活帳戶")["id"]
    assert balances.balance_at(conn, acc)["balance"] == 20000 + 42000 - 4800 - 3000
    assert balances.balance_at(conn, acc, "2037-10-04")["balance"] == 20000
    card = refdata.resolve_account(conn, "card2")["id"]
    assert balances.balance_at(conn, card)["balance"] == -260
    assert balances.balance_at(conn, card)["verified_on"] is None


def test_snapshot_diff_and_adjust(conn):
    out = ingest.run(conn, {"records": [{"type": "balance", "account": "現金", "balance": 3000, "as_of": "2037-10-01"}]})
    assert out["results"][0]["first"] and "起始餘額" in out["warnings"][0]
    ingest.run(conn, {"records": [{"type": "expense", "amount": 120, "date": "2037-10-03", "desc": "便當", "account": "現金"}]})
    out = ingest.run(conn, {"records": [{"type": "balance", "account": "現金", "balance": 2500, "as_of": "2037-10-08"}]})
    r = out["results"][0]
    assert (r["derived"], r["diff"], r["can_adjust"]) == (2880, -380, True)
    assert "差 -380" in out["warnings"][0]
    with db.write(conn) as w:
        a = balances.adjust(w, r["snapshot_id"])
    assert a["adjustment"]["amount"] == 380 and a["adjustment"]["category"] is None      # 待分類，提醒使用者
    with pytest.raises(Conflict):
        with db.write(conn) as w:
            balances.adjust(w, r["snapshot_id"])


def test_resetting_same_day_snapshot_compares_without_itself(conn):
    ingest.run(conn, {"records": [{"type": "balance", "account": "現金", "balance": 1000, "as_of": "2037-10-01"}]})
    ingest.run(conn, {"records": [{"type": "balance", "account": "現金", "balance": 900, "as_of": "2037-10-08"}]})
    out = ingest.run(conn, {"records": [{"type": "balance", "account": "現金", "balance": 950, "as_of": "2037-10-08"}]})
    assert out["results"][0]["diff"] == -50


def test_reserves_group_members_and_estimate_months(conn):
    ingest.run(conn, {"records": [
        {"type": "balance", "account": "銀行 彈性活存", "balance": 18500, "as_of": "2037-06-01"},
        {"type": "balance", "account": "銀行 長期預備金", "balance": 21300, "as_of": "2037-06-01"},
        {"type": "balance", "account": "TEST1 長期預備金", "balance": 31000, "as_of": "2037-06-01"}]})
    for day in ("2037-07-10", "2037-08-10", "2037-09-10"):
        ingest.run(conn, {"records": [{"type": "transfer", "amount": 3000, "date": day, "from": "生活帳戶", "to": "銀行 彈性活存"}]})
    res = {r["name"]: r for r in balances.reserves(conn)}
    em = res["緊急花銷緩衝"]
    assert (em["balance"], em["floor"], em["goal"], em["monthly"]) == (27500, 36000, "底線", 3000)
    assert em["months"] == 3 and em["level"] == "crit"
    longterm = res["長期預備金"]
    assert longterm["balance"] == 52300 and longterm["accounts"] == ["銀行 長期預備金", "TEST1 長期預備金"]
    assert longterm["goal"] == "目標" and longterm["level"] == "warn" and longterm["months"] is None


def test_overview_totals(conn):
    ingest.run(conn, {"records": [
        {"type": "balance", "account": "銀行 彈性活存", "balance": 18500, "as_of": "2037-10-01"},
        {"type": "expense", "amount": 1000, "date": "2037-10-09", "desc": "晚餐", "account": "card2"}]})
    o = balances.overview(conn)
    assert o["assets"] == 18500 and o["card_debt"] == 1000 and o["net_worth"] == 17500
    row = next(a for a in o["accounts"] if a["name"] == "範例卡二")
    assert row["balance"] == -1000 and row["change"] == -1000


def test_snapshot_can_be_reverted(conn):
    out = ingest.run(conn, {"records": [{"type": "balance", "account": "現金", "balance": 1000, "as_of": "2037-10-01"}]})
    with db.write(conn) as w:
        batches.revert(w, out["batch_id"])
    assert balances.snapshots(conn) == []
